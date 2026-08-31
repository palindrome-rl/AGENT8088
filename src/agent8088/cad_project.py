"""Checkpointed CAD projects for bounded, multi-turn assembly generation.

Complex assemblies should not require one model response to contain an entire
build123d program.  This module keeps a small manifest inside the authorized
artifact workspace, validates one constrained component at a time through the
existing CAD worker, and deterministically assembles those verified STEP
components from a compact placement specification.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from . import cad, cad_ports

PROJECT_SUFFIX = ".cadproject.json"
PROJECT_SCHEMA_VERSION = 2
MAX_PROJECT_COMPONENTS = 64
MAX_PROJECT_OCCURRENCES = 512
MAX_CUSTOM_REPAIR_ATTEMPTS = 3
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _json_object(value: str | dict | None, label: str, *, default=None) -> dict:
    if value is None or value == "":
        payload = {} if default is None else default
    elif isinstance(value, dict):
        payload = value
    elif isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} must be a JSON object: {exc}") from exc
    else:
        raise TypeError(f"{label} must be a JSON object")
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be a JSON object")
    return payload


def _json_array(value: str | list | None, label: str, *, default=None) -> list:
    if value is None or value == "":
        payload = [] if default is None else default
    elif isinstance(value, list):
        payload = value
    elif isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} must be a JSON array: {exc}") from exc
    else:
        raise TypeError(f"{label} must be a JSON array")
    if not isinstance(payload, list):
        raise TypeError(f"{label} must be a JSON array")
    return payload


def _formats(value: str, *, default: str = "step") -> list[str]:
    requested: list[str] = []
    for item in (value or default).split(","):
        name = item.strip().lower().lstrip(".")
        if name and name not in requested:
            requested.append(name)
    unsupported = [
        name for name in requested if name not in cad.CONVERTIBLE_CAD_TARGETS
    ]
    if unsupported:
        raise ValueError("unsupported CAD output format(s): " + ", ".join(unsupported))
    if "step" not in requested:
        requested.insert(0, "step")
    return requested


def _manifest_path(path) -> Path:
    manifest = Path(path)
    if not manifest.name.lower().endswith(PROJECT_SUFFIX):
        raise ValueError(
            "project filename must end in .cadproject.json, for example "
            "robotic_gripper/project.cadproject.json"
        )
    return manifest


def _safe_name(value: str, label: str) -> str:
    name = str(value or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"{label} must start with a letter and contain only letters, numbers, "
            "underscores, or hyphens (maximum 64 characters)"
        )
    return name


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load(path) -> tuple[Path, dict[str, Any]]:
    manifest = _manifest_path(path)
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"project does not exist: {manifest}. Call cad_project_create first"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"project manifest is invalid JSON: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != PROJECT_SCHEMA_VERSION
    ):
        raise ValueError("project manifest has an unsupported schema version")
    _safe_name(payload.get("name", ""), "project name")
    if not isinstance(payload.get("parameters"), dict):
        raise TypeError("project parameters must be an object")
    if not isinstance(payload.get("components"), dict):
        raise TypeError("project components must be an object")
    if not isinstance(payload.get("parts"), dict):
        raise TypeError("project parts must be an object")
    if not isinstance(payload.get("mates"), list):
        raise TypeError("project mates must be an array")
    return manifest, payload


MATE_TYPES = ("coaxial", "face_to_face", "press_fit", "gear_mesh")


def _parse_parts(parts_value, label: str) -> dict[str, dict[str, Any]]:
    """Validate the declared part list into {name: {kind, description|params}}.

    Structural validation only -- a warehouse part's params are checked for
    real by cad.build_warehouse_component when it actually gets built a few
    lines below; a custom part's geometry is checked by add_component later,
    since there is nothing to check yet but a free-text description.
    """
    raw = _json_array(parts_value, label)
    if not raw:
        raise ValueError(f"{label} must be a non-empty array")
    if len(raw) > MAX_PROJECT_COMPONENTS:
        raise ValueError(f"{label} is limited to {MAX_PROJECT_COMPONENTS} parts")
    parsed: dict[str, dict[str, Any]] = {}
    for index, part in enumerate(raw):
        if not isinstance(part, dict):
            raise ValueError(f"{label}[{index}] must be an object")
        part_name = _safe_name(part.get("name", ""), f"{label}[{index}] name")
        if part_name in parsed:
            raise ValueError(f"duplicate part name: {part_name!r}")
        kind = str(part.get("kind") or "custom").strip()
        if kind == "custom":
            parsed[part_name] = {
                "kind": "custom",
                "description": str(part.get("description") or ""),
            }
        elif kind in cad_ports.WAREHOUSE_KINDS:
            part_params = part.get("params")
            if not isinstance(part_params, dict):
                raise ValueError(f"part {part_name!r} ({kind}) requires a 'params' object")
            parsed[part_name] = {"kind": kind, "params": part_params}
        else:
            raise ValueError(
                f"part {part_name!r} has unknown kind {kind!r}; expected 'custom' or "
                f"one of {cad_ports.WAREHOUSE_KINDS}"
            )
    return parsed


def _parse_mates(mates_value, part_names: set[str], label: str) -> list[dict[str, Any]]:
    raw = _json_array(mates_value, label, default=[])
    if len(raw) > MAX_PROJECT_OCCURRENCES:
        raise ValueError(f"{label} is limited to {MAX_PROJECT_OCCURRENCES} mates")
    parsed: list[dict[str, Any]] = []
    for index, mate in enumerate(raw):
        if not isinstance(mate, dict):
            raise ValueError(f"{label}[{index}] must be an object")
        mate_type = str(mate.get("type") or "")
        if mate_type not in MATE_TYPES:
            raise ValueError(
                f"{label}[{index}] has unknown type {mate_type!r}; expected one of {MATE_TYPES}"
            )
        a_ref, b_ref = str(mate.get("a") or ""), str(mate.get("b") or "")
        if "." not in a_ref or "." not in b_ref:
            raise ValueError(f"{label}[{index}] 'a'/'b' must be \"PartName.port\"")
        for ref in (a_ref, b_ref):
            comp = ref.split(".", 1)[0]
            if comp not in part_names:
                raise ValueError(f"{label}[{index}] references unknown part: {comp!r}")
        entry = {"type": mate_type, "a": a_ref, "b": b_ref}
        if mate_type == "gear_mesh":
            for field in ("module", "teeth_a", "teeth_b"):
                if mate.get(field) is None:
                    raise ValueError(f"{label}[{index}] (gear_mesh) requires {field!r}")
                entry[field] = mate[field]
        parsed.append(entry)
    return parsed


def create(
    path,
    name: str,
    parts: str | list,
    mates: str | list = "[]",
    parameters: str | dict = "{}",
    verification: str | dict | None = None,
    formats: str = "step,stl",
) -> str:
    """Create an idempotent project manifest declaring the FULL assembly up
    front -- every part and every inter-part connection -- rather than
    discovering it one cad_project_add_component call at a time. A part
    declared with a warehouse.* kind is built here immediately, deterministically,
    with no model round trip; only kind: "custom" parts need a later
    cad_project_add_component call."""
    try:
        manifest = _manifest_path(path)
        project_name = _safe_name(name, "project name")
        params = _json_object(parameters, "project parameters")
        checks = _json_object(verification, "project verification", default={})
        requested = _formats(formats, default="step,stl")
        parsed_parts = _parse_parts(parts, "parts")
        parsed_mates = _parse_mates(mates, set(parsed_parts), "mates")
    except (TypeError, ValueError) as exc:
        return f"CAD project creation failed: {exc}."
    encoded = json.dumps({"parameters": params, "verification": checks})
    if len(encoded.encode("utf-8")) > cad.MAX_VERIFICATION_BYTES:
        return "CAD project creation failed: parameters and verification are too large."
    # Resume semantics. An earlier version persisted the manifest even when a
    # warehouse part failed to build, then rejected the corrected retry as
    # "a different project specification" -- while add_component refused the
    # same part as "already built". That left no way forward but a new
    # filename, and a real run burned ~4 restarts cycling through them.
    #
    # Drift is therefore only rejected for parts that are ACTUALLY BUILT
    # (protecting verified geometry); anything not yet built is free to be
    # corrected in place, on the same manifest.
    carried: dict[str, Any] = {}
    if manifest.exists():
        try:
            _, existing = _load(manifest)
        except (TypeError, ValueError) as exc:
            return f"CAD project creation failed: {exc}."
        if existing.get("name") != project_name:
            return (
                f"CAD project creation failed: {manifest.name} already belongs to "
                f"{existing.get('name')!r}. Use another manifest filename."
            )
        existing_parts = existing.get("parts") or {}
        carried = {
            name: component
            for name, component in (existing.get("components") or {}).items()
            if component.get("status") == "built"
        }
        changed_built = sorted(
            name for name in carried
            if parsed_parts.get(name) != existing_parts.get(name)
        )
        if changed_built:
            return (
                "CAD project creation failed: these parts are already built and "
                "verified, so their specification cannot be changed in place: "
                + ", ".join(changed_built)
                + ". Keep them as they were declared, or use a new manifest filename "
                "to start over."
            )
        # Drop carried components whose part is no longer declared at all.
        carried = {n: c for n, c in carried.items() if n in parsed_parts}

    payload = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "name": project_name,
        "units": "mm",
        "parameters": params,
        "verification": checks,
        "formats": requested,
        "parts": parsed_parts,
        "mates": parsed_mates,
        "components": carried,
        "assembly": None,
    }
    (manifest.parent / "components").mkdir(parents=True, exist_ok=True)

    prebuilt: list[str] = []
    failed: list[str] = []
    for part_name, part in parsed_parts.items():
        if part["kind"] == "custom":
            continue
        if part_name in payload["components"]:
            continue  # already built on an earlier call — never rebuild good geometry
        output = manifest.parent / "components" / f"{part_name}.step"
        result = cad.build_warehouse_component(output, part["kind"], part["params"])
        if not result.get("ok"):
            failed.append(f"{part_name}: {result.get('error')}")
            continue
        payload["components"][part_name] = {
            "status": "built",
            "kind": part["kind"],
            "step": output.relative_to(manifest.parent).as_posix(),
            "ports": result.get("ports") or {},
        }
        prebuilt.append(part_name)

    # Persist whatever succeeded, always. A partial result is progress to build
    # on next call, not state to throw away -- and because drift is only
    # rejected for built parts, the corrected retry lands on this same manifest.
    _atomic_json(manifest, payload)

    if failed:
        return (
            "Some warehouse part(s) could not be built:\n"
            + "\n".join(f"  - {item}" for item in failed)
            + f"\n\nEverything else was saved to {manifest.name}. Fix only the "
            "failing part's params and call cad_project_create again with the SAME "
            "filename — already-built parts are kept and will not be rebuilt. Do "
            "not start a new project file."
        )

    custom_remaining = sorted(
        n for n, p in parsed_parts.items()
        if p["kind"] == "custom" and n not in payload["components"]
    )
    lines = [f"Created staged CAD project {project_name!r} at {manifest}."]
    if prebuilt:
        lines.append(f"Auto-built {len(prebuilt)} warehouse part(s) with zero model "
                      f"turns: {', '.join(sorted(prebuilt))}.")
    if custom_remaining:
        lines.append(
            f"{len(custom_remaining)} custom part(s) still need "
            f"cad_project_add_component: {', '.join(custom_remaining)}."
        )
    else:
        lines.append("All parts are built. Call cad_project_finalize.")
    return "\n".join(lines)


def status(path) -> str:
    """Return checkpoint state without echoing large source payloads."""
    try:
        manifest, project = _load(path)
    except (TypeError, ValueError) as exc:
        return f"CAD project status failed: {exc}."
    components = project["components"]
    parts = project.get("parts") or {}
    built = sum(1 for name in parts
                if components.get(name, {}).get("status") == "built")
    lines = [
        f"CAD project: {project['name']}",
        f"Manifest: {manifest}",
        f"Parts: {built}/{len(parts)} built",
    ]
    # Show every DECLARED part with its kind and real state -- not just the
    # components dict, which omits parts that were declared but never built
    # and so hid exactly the state a stuck run needed to see.
    for name in sorted(parts):
        kind = parts[name].get("kind", "?")
        component = components.get(name)
        if component is None:
            state = ("not built — fix params and re-run cad_project_create"
                     if kind != "custom" else "not built — needs cad_project_add_component")
        else:
            state = component.get("status", "unknown")
            if state == "failed":
                attempts = component.get("attempts", 0)
                remaining = max(0, MAX_CUSTOM_REPAIR_ATTEMPTS - attempts)
                state = f"failed ({remaining} repair attempt(s) left)"
        lines.append(f"  - {name} [{kind}]: {state}")
    assembly = project.get("assembly") or {}
    if assembly.get("status") == "built":
        lines.append(f"Assembly: built ({assembly.get('step')})")
    else:
        lines.append("Assembly: not finalized")
    return "\n".join(lines)


def add_component(
    path,
    name: str,
    source: str,
    parameters: str | dict = "{}",
    verification: str | dict | None = None,
    ports: str | dict | None = None,
    timeout: int = 600,
) -> str:
    """Build, validate, and checkpoint one constrained build123d component.

    Only for parts declared kind: "custom" at cad_project_create -- a
    warehouse.* part is already built, with zero model turns, by create()
    itself. Bounded to MAX_CUSTOM_REPAIR_ATTEMPTS real build attempts per
    component name; once exhausted, further calls are refused rather than
    letting a stuck component consume an unbounded number of turns."""
    try:
        manifest, project = _load(path)
        component_name = _safe_name(name, "component name")
        local_params = _json_object(parameters, "component parameters")
        checks = _json_object(verification, "component verification")
        port_specs = _json_object(ports, "component ports", default={})
    except (TypeError, ValueError) as exc:
        return f"CAD project component failed: {exc}."
    if not checks:
        return "CAD project component failed: verification must contain at least one check."
    if not isinstance(source, str) or not source.strip():
        return "CAD project component failed: source must define a non-empty gen_step() function."
    part = project["parts"].get(component_name)
    if part is None:
        return (
            f"CAD project component failed: {component_name!r} was not declared in "
            "cad_project_create's parts list."
        )
    if part.get("kind") != "custom":
        # Only claim it is built if it actually is. Saying "already built" about
        # a warehouse part whose auto-build FAILED is how a real run deadlocked:
        # finalize refused it as unbuilt, add_component refused it as built.
        already = project["components"].get(component_name, {}).get("status") == "built"
        if already:
            return (
                f"CAD project component failed: {component_name!r} is a "
                f"{part.get('kind')!r} part that cad_project_create already built. "
                "Do not write source for it."
            )
        return (
            f"CAD project component failed: {component_name!r} is a {part.get('kind')!r} "
            "part, so it is built from params by cad_project_create, not from source "
            "here. Its auto-build has not succeeded yet — fix its params and call "
            "cad_project_create again with the same manifest filename."
        )
    for port_name, port_spec in port_specs.items():
        if not isinstance(port_spec, dict) or "at" not in port_spec:
            return (
                f"CAD project component failed: port {component_name}.{port_name!r} "
                "must be an object with at least an 'at': [x,y,z] field."
            )

    components = project["components"]
    existing = components.get(component_name) or {}
    attempts = int(existing.get("attempts") or 0)
    if existing.get("status") != "built" and attempts >= MAX_CUSTOM_REPAIR_ATTEMPTS:
        return (
            f"CAD project component failed: {component_name!r} has used its "
            f"{MAX_CUSTOM_REPAIR_ATTEMPTS}-attempt repair budget without validating. "
            "Rework the approach for this component rather than retrying the same one, "
            "or ask for the budget to be raised if the design genuinely needs it."
        )

    merged_params = dict(project["parameters"])
    merged_params.update(local_params)
    digest_payload = json.dumps(
        {"source": source, "parameters": merged_params, "verification": checks, "ports": port_specs},
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
    output = manifest.parent / "components" / f"{component_name}.step"
    report = output.with_suffix(".report.json")
    if (
        existing.get("status") == "built"
        and existing.get("digest") == digest
        and output.is_file()
        and report.is_file()
    ):
        return (
            f"CAD component {component_name!r} is already built and verified.\n"
            + status(manifest)
        )

    result = cad.generate_cad_model(
        output,
        source,
        json.dumps(merged_params, ensure_ascii=False),
        "step",
        timeout=timeout,
        verification=checks,
    )
    success = result.startswith("Generated and verified ")
    components[component_name] = {
        "status": "built" if success else "failed",
        "kind": "custom",
        "digest": digest,
        "step": output.relative_to(manifest.parent).as_posix(),
        "source": output.with_suffix(".step.py")
        .relative_to(manifest.parent)
        .as_posix(),
        "report": report.relative_to(manifest.parent).as_posix(),
        "parameters": local_params,
        "verification": checks,
        "ports": port_specs,
        "attempts": attempts + 1,
    }
    if not success:
        components[component_name]["last_error"] = result[:1500]
    project["assembly"] = None
    _atomic_json(manifest, project)
    if not success:
        remaining = MAX_CUSTOM_REPAIR_ATTEMPTS - (attempts + 1)
        return (
            f"CAD project component {component_name!r} did not validate "
            f"({remaining} repair attempt(s) left). Repair only this component and "
            "call cad_project_add_component with the same name.\n"
            + result
        )
    try:
        report_payload = json.loads(report.read_text(encoding="utf-8"))
        metrics = cad._format_metrics(report_payload)
    except (OSError, json.JSONDecodeError, TypeError):
        metrics = "Component artifact and report were created."
    ready = sum(1 for item in components.values() if item.get("status") == "built")
    total = len(project["parts"])
    return (
        f"Built and checkpointed CAD component {component_name!r}.\n{metrics}\n"
        f"Progress: {ready}/{total} part(s) ready. Add the next custom component "
        "with one tool call, or finalize the assembly if all parts are built."
    )


def finalize(path, verification: str | dict | None = None, formats: str = "",
             timeout: int = 900) -> str:
    """Assemble every declared part from its mates -- no placement argument.

    Unlike the old occurrence-based finalize, this needs nothing from the
    model beyond an optional final verification override: every part and
    every mate was already declared at cad_project_create, so positions are
    computed here from that same declaration, not authored again."""
    try:
        manifest, project = _load(path)
        checks = _json_object(verification, "assembly verification", default=None)
        requested = _formats(formats or ",".join(project.get("formats") or ["step"]))
    except (TypeError, ValueError) as exc:
        return f"CAD project finalization failed: {exc}."

    parts = project["parts"]
    components = project["components"]
    unbuilt = [name for name in parts if components.get(name, {}).get("status") != "built"]
    if unbuilt:
        return (
            "CAD project finalization failed: these parts are not built yet: "
            + ", ".join(sorted(unbuilt))
            + ". Call cad_project_add_component for each custom one first."
        )

    expanded_components = []
    for part_name in parts:
        component = components[part_name]
        expanded_components.append({
            "name": part_name,
            "input": component["step"],
            "ports": component.get("ports") or {},
        })

    checks = checks if checks else (project.get("verification") or None)
    if not checks:
        return "CAD project finalization failed: no verification checks are available."
    expanded_spec = {
        "schema_version": 2,
        "name": project["name"],
        "units": "mm",
        "parameters": project["parameters"],
        "components": expanded_components,
        "mates": project["mates"],
        "verification": checks,
        "interference_policy": "error",
    }
    encoded = json.dumps(expanded_spec, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > cad.MAX_DESIGN_BYTES:
        return "CAD project finalization failed: assembly placement specification is too large."

    root = manifest.parent
    output = root / f"{project['name']}.step"
    assembly_path = root / f"{project['name']}.assembly.json"
    params_path = root / f"{project['name']}.params.json"
    report_path = root / f"{project['name']}.report.json"
    preview_path = root / f"{project['name']}.preview.png"
    _atomic_json(assembly_path, expanded_spec)
    _atomic_json(params_path, project["parameters"])
    result = cad._run_worker(
        {
            "action": "assemble_project",
            "assembly": str(assembly_path.resolve()),
            "output": str(output.resolve()),
            "report": str(report_path.resolve()),
            "preview": str(preview_path.resolve()),
            "formats": requested,
            "workspace": str(root.resolve()),
        },
        timeout=timeout,
    )
    if not result.get("ok"):
        project["assembly"] = {
            "status": "failed",
            "last_error": str(result.get("error") or "")[:1500],
        }
        _atomic_json(manifest, project)
        return cad._worker_failure("CAD project finalization failed", result)
    expected = [output, assembly_path, params_path, report_path, preview_path]
    expected.extend(
        output.with_suffix("." + item) for item in requested if item != "step"
    )
    failures = [
        problem
        for item in expected
        if (problem := cad._existing_artifact(item, item.name))
    ]
    if failures:
        return "CAD project finalization failed verification: " + "; ".join(failures)
    project["assembly"] = {
        "status": "built",
        "step": output.relative_to(root).as_posix(),
        "spec": assembly_path.relative_to(root).as_posix(),
        "report": report_path.relative_to(root).as_posix(),
        "preview": preview_path.relative_to(root).as_posix(),
        "component_count": len(expanded_components),
    }
    _atomic_json(manifest, project)
    artifacts = "\n".join(f"  - {item}" for item in expected)
    return (
        f"Finalized and verified staged CAD project {project['name']!r}.\n"
        + cad._format_metrics(result)
        + "\nArtifacts:\n"
        + artifacts
    )

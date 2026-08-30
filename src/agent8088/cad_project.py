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

from . import cad

PROJECT_SUFFIX = ".cadproject.json"
PROJECT_SCHEMA_VERSION = 1
MAX_PROJECT_COMPONENTS = 64
MAX_PROJECT_OCCURRENCES = 512
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
    return manifest, payload


def create(
    path,
    name: str,
    parameters: str | dict = "{}",
    verification: str | dict | None = None,
    formats: str = "step,stl",
) -> str:
    """Create an idempotent project manifest without overwriting prior work."""
    try:
        manifest = _manifest_path(path)
        project_name = _safe_name(name, "project name")
        params = _json_object(parameters, "project parameters")
        checks = _json_object(verification, "project verification", default={})
        requested = _formats(formats, default="step,stl")
    except (TypeError, ValueError) as exc:
        return f"CAD project creation failed: {exc}."
    encoded = json.dumps({"parameters": params, "verification": checks})
    if len(encoded.encode("utf-8")) > cad.MAX_VERIFICATION_BYTES:
        return "CAD project creation failed: parameters and verification are too large."
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
        if (
            existing.get("parameters") != params
            or existing.get("verification") != checks
            or existing.get("formats") != requested
        ):
            return (
                "CAD project creation failed: this manifest already contains a "
                "different project specification. Use cad_project_status to resume "
                "it, or choose a new manifest filename."
            )
        return status(manifest)

    payload = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "name": project_name,
        "units": "mm",
        "parameters": params,
        "verification": checks,
        "formats": requested,
        "components": {},
        "assembly": None,
    }
    _atomic_json(manifest, payload)
    (manifest.parent / "components").mkdir(parents=True, exist_ok=True)
    return (
        f"Created staged CAD project {project_name!r} at {manifest}.\n"
        "Components: 0 built. Call cad_project_add_component for exactly one "
        "component, then wait for its validation result."
    )


def status(path) -> str:
    """Return checkpoint state without echoing large source payloads."""
    try:
        manifest, project = _load(path)
    except (TypeError, ValueError) as exc:
        return f"CAD project status failed: {exc}."
    components = project["components"]
    lines = [
        f"CAD project: {project['name']}",
        f"Manifest: {manifest}",
        f"Components: {len(components)}/{MAX_PROJECT_COMPONENTS}",
    ]
    for name in sorted(components):
        lines.append(f"  - {name}: {components[name].get('status', 'unknown')}")
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
    timeout: int = 600,
) -> str:
    """Build, validate, and checkpoint one constrained build123d component."""
    try:
        manifest, project = _load(path)
        component_name = _safe_name(name, "component name")
        local_params = _json_object(parameters, "component parameters")
        checks = _json_object(verification, "component verification")
    except (TypeError, ValueError) as exc:
        return f"CAD project component failed: {exc}."
    if not checks:
        return "CAD project component failed: verification must contain at least one check."
    if not isinstance(source, str) or not source.strip():
        return "CAD project component failed: source must define a non-empty gen_step() function."
    components = project["components"]
    if component_name not in components and len(components) >= MAX_PROJECT_COMPONENTS:
        return f"CAD project component failed: projects are limited to {MAX_PROJECT_COMPONENTS} components."

    merged_params = dict(project["parameters"])
    merged_params.update(local_params)
    digest_payload = json.dumps(
        {"source": source, "parameters": merged_params, "verification": checks},
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
    existing = components.get(component_name) or {}
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
        "digest": digest,
        "step": output.relative_to(manifest.parent).as_posix(),
        "source": output.with_suffix(".step.py")
        .relative_to(manifest.parent)
        .as_posix(),
        "report": report.relative_to(manifest.parent).as_posix(),
        "parameters": local_params,
        "verification": checks,
    }
    if not success:
        components[component_name]["last_error"] = result[:1500]
    project["assembly"] = None
    _atomic_json(manifest, project)
    if not success:
        return (
            f"CAD project component {component_name!r} did not validate. Repair only "
            "this component and call cad_project_add_component with the same name.\n"
            + result
        )
    try:
        report_payload = json.loads(report.read_text(encoding="utf-8"))
        metrics = cad._format_metrics(report_payload)
    except (OSError, json.JSONDecodeError, TypeError):
        metrics = "Component artifact and report were created."
    ready = sum(1 for item in components.values() if item.get("status") == "built")
    return (
        f"Built and checkpointed CAD component {component_name!r}.\n{metrics}\n"
        f"Progress: {ready} component(s) ready. Add the next component with one "
        "tool call, or finalize the assembly."
    )


def finalize(path, assembly: str | dict, formats: str = "", timeout: int = 900) -> str:
    """Assemble verified component STEP files from a bounded placement object."""
    try:
        manifest, project = _load(path)
        spec = _json_object(assembly, "assembly")
        requested = _formats(formats or ",".join(project.get("formats") or ["step"]))
    except (TypeError, ValueError) as exc:
        return f"CAD project finalization failed: {exc}."
    occurrences = spec.get("occurrences")
    if not isinstance(occurrences, list) or not occurrences:
        return "CAD project finalization failed: assembly.occurrences must be a non-empty array."
    if len(occurrences) > MAX_PROJECT_OCCURRENCES:
        return f"CAD project finalization failed: assembly is limited to {MAX_PROJECT_OCCURRENCES} occurrences."

    components = project["components"]
    expanded = []
    seen: set[str] = set()
    for index, occurrence in enumerate(occurrences):
        if not isinstance(occurrence, dict):
            return f"CAD project finalization failed: occurrence {index} must be an object."
        unknown = set(occurrence) - {"name", "component", "at", "rotate"}
        if unknown:
            return (
                f"CAD project finalization failed: occurrence {index} has unsupported fields: "
                + ", ".join(sorted(unknown))
                + "."
            )
        try:
            occurrence_name = _safe_name(occurrence.get("name", ""), "occurrence name")
            component_name = _safe_name(
                occurrence.get("component", ""), "occurrence component"
            )
        except ValueError as exc:
            return f"CAD project finalization failed: {exc}."
        if occurrence_name in seen:
            return f"CAD project finalization failed: duplicate occurrence name {occurrence_name!r}."
        seen.add(occurrence_name)
        component = components.get(component_name)
        if not component or component.get("status") != "built":
            return f"CAD project finalization failed: component {component_name!r} is not built and verified."
        expanded.append(
            {
                "name": occurrence_name,
                "component": component_name,
                "input": component["step"],
                "at": occurrence.get("at", [0, 0, 0]),
                "rotate": occurrence.get("rotate", [0, 0, 0]),
            }
        )

    checks = spec.get("verification") or project.get("verification")
    if not isinstance(checks, dict) or not checks:
        return "CAD project finalization failed: assembly verification must contain geometry checks."
    expanded_spec = {
        "schema_version": 1,
        "name": project["name"],
        "units": "mm",
        "parameters": project["parameters"],
        "occurrences": expanded,
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
        "occurrence_count": len(expanded),
    }
    _atomic_json(manifest, project)
    artifacts = "\n".join(f"  - {item}" for item in expected)
    return (
        f"Finalized and verified staged CAD project {project['name']!r}.\n"
        + cad._format_metrics(result)
        + "\nArtifacts:\n"
        + artifacts
    )

"""Make a node_modules that npm installed in the WSL sandbox usable on Windows too.

The WSL sandbox runs npm on Linux, in the project folder on the Windows drive. What it leaves there works in the
sandbox but not on Windows, where the user runs the project:

- Packages that ship native code as per-platform packages (esbuild, rollup and so Vite, swc, lightningcss, sharp,
  turbo) get only their Linux one, `@esbuild/linux-x64`.
- An install script may replace a file with the Linux binary: esbuild's puts the Linux executable over its
  `bin/esbuild` launcher.
- `node_modules/.bin` holds Linux symlinks, which Windows sees as empty files; npm on Windows runs a tool through a
  `.cmd` shim there instead.

After an npm install in the sandbox, Agent8088 installs the same project again for Windows, into a temporary folder
and with no install scripts (still inside the sandbox), and then, from that copy:

1. adds the Windows platform packages next to the Linux ones (Node picks the one for the system it runs on);
2. puts back the files an install script changed, in packages that have one (the script-free launcher works on
   both systems: esbuild's finds whichever platform package is there);
3. writes a `.cmd` and a `.ps1` shim in `.bin` for every tool a package provides.

A package that compiles native code while it installs (node-gyp: a `binding.gyp`) has no Windows build to add; it is
reported, so the user knows to run `npm install` on Windows before using the project.

Everything here is file copying between two folders the caller names; nothing is run.
"""
from __future__ import annotations

import filecmp
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# A command that installs or changes packages with npm (not yarn or pnpm: their layouts differ).
INSTALL_RE = re.compile(r"(?:^|[\s;&|(])npm(?:\.cmd)?\s+(?:[-\w=]+\s+)*?(?:install|i|ci|add|update|up|uninstall|rm|"
                        r"remove|un|isntall|it|install-test|install-ci-test)(?=\s|$|[;&|)])")
_INSTALL_SCRIPTS = ("preinstall", "install", "postinstall")
_MAX_PACKAGES = 5000


def is_npm_install(command: str) -> bool:
    return bool(INSTALL_RE.search(command or ""))


def _manifest(folder: Path) -> dict:
    try:
        data = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def packages(node_modules: Path) -> dict[str, Path]:
    """{relative path under node_modules: folder} for every package in the tree, nested ones included."""
    found: dict[str, Path] = {}
    stack = [Path(node_modules)]
    while stack and len(found) < _MAX_PACKAGES:
        root = stack.pop()
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_dir() or entry.is_symlink():
                continue
            if entry.name.startswith("@"):
                stack.append(entry)
                continue
            if (entry / "package.json").is_file():
                found[entry.relative_to(node_modules).as_posix()] = entry
                if (entry / "node_modules").is_dir():
                    stack.append(entry / "node_modules")
    return found


def _only_for(manifest: dict, system: str) -> bool:
    """The package says it is for some systems, and `system` is not one of them."""
    systems = manifest.get("os")
    if not isinstance(systems, list) or not systems:
        return False
    allowed = [s for s in systems if isinstance(s, str) and not s.startswith("!")]
    denied = [s[1:] for s in systems if isinstance(s, str) and s.startswith("!")]
    return (bool(allowed) and system not in allowed) or system in denied


def linux_only(node_modules: Path) -> list[str]:
    """Packages installed here that do not run on Windows: the sign the tree came from Linux."""
    return sorted(rel for rel, folder in packages(node_modules).items() if _only_for(_manifest(folder), "win32"))


def native_addons(node_modules: Path) -> list[str]:
    """Packages that compiled native code when they were installed: there is no Windows build of those to add."""
    return sorted(rel.rsplit("node_modules/", 1)[-1] for rel, folder in packages(node_modules).items()
                  if (folder / "binding.gyp").is_file() or _manifest(folder).get("gypfile") is True)


@dataclass
class Result:
    added: list[str] = field(default_factory=list)      # Windows platform packages copied in
    restored: list[str] = field(default_factory=list)   # packages whose install-script changes were put back
    shims: list[str] = field(default_factory=list)      # tools given a .cmd/.ps1 shim
    native: list[str] = field(default_factory=list)     # packages compiled for Linux, with no Windows build

    def note(self) -> str:
        parts = []
        if self.added or self.shims:
            what = ", ".join(self.added[:6]) + (f" and {len(self.added) - 6} more" if len(self.added) > 6 else "")
            parts.append("[note: npm ran in the WSL sandbox, on Linux. So that the project also runs on Windows, "
                         "Agent8088 added " + (f"the Windows builds of its native packages ({what}) and " if what else "")
                         + "Windows shims for its tools in node_modules/.bin.]")
        if self.native:
            names = ", ".join(self.native[:6]) + (f" and {len(self.native) - 6} more" if len(self.native) > 6 else "")
            parts.append(f"[note: {names} compiled native code for Linux while installing; there is no Windows build "
                         "of it to add. Tell the user to run `npm install` on Windows before running the project "
                         "there.]")
        return "\n".join(parts)


def _copy_tree(source: Path, dest: Path) -> None:
    shutil.copytree(source, dest, dirs_exist_ok=True, symlinks=True)


def _restore_changed_files(source: Path, dest: Path) -> bool:
    """Copy each file of the script-free `source` over `dest` where they differ. True when one did."""
    changed = False
    for path in source.rglob("*"):
        if path.is_symlink() or not path.is_file() or "node_modules" in path.relative_to(source).parts:
            continue
        target = dest / path.relative_to(source)
        try:
            if target.is_file() and not target.is_symlink() and filecmp.cmp(path, target, shallow=False):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Removed first, never written through: esbuild's install script makes its launcher a hard link to the
            # Linux binary, and writing the launcher into that file replaced the binary too, so the launcher ran
            # itself forever.
            target.unlink(missing_ok=True)
            shutil.copy2(path, target)
            changed = True
        except OSError:
            continue
    return changed


_CMD_SHIM = """@ECHO off\r
REM Written by Agent8088: node_modules was installed in the WSL sandbox, on Linux.\r
SETLOCAL\r
IF EXIST "%~dp0\\node.exe" (SET "_prog=%~dp0\\node.exe") ELSE (SET "_prog=node")\r
"%_prog%" "%~dp0\\{target}" %*\r
"""
_PS1_SHIM = """#!/usr/bin/env pwsh
# Written by Agent8088: node_modules was installed in the WSL sandbox, on Linux.
$exe = if (Test-Path "$PSScriptRoot/node.exe") {{ "$PSScriptRoot/node.exe" }} else {{ "node" }}
& $exe "$PSScriptRoot/{target}" @args
exit $LASTEXITCODE
"""


def _bins(manifest: dict) -> dict[str, str]:
    bins = manifest.get("bin")
    name = str(manifest.get("name") or "")
    if isinstance(bins, str) and name:
        return {name.rsplit("/", 1)[-1]: bins}
    if isinstance(bins, dict):
        return {str(k): str(v) for k, v in bins.items() if isinstance(v, str)}
    return {}


def _is_node_script(path: Path) -> bool:
    """A file node can run: JavaScript, not a native executable (an ELF or Mach-O binary, a PE .exe)."""
    try:
        head = path.read_bytes()[:4]
    except OSError:
        return False
    return head[:4] not in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe") and head[:2] != b"MZ"


def write_shims(node_modules: Path) -> list[str]:
    """A .cmd and a .ps1 in node_modules/.bin for each tool a top-level package provides (what npm would link)."""
    bin_dir = Path(node_modules) / ".bin"
    written = []
    for rel, folder in packages(node_modules).items():
        if "/node_modules/" in f"/{rel}":
            continue   # npm links only top-level packages' tools into the top .bin
        for tool, target in _bins(_manifest(folder)).items():
            if not re.fullmatch(r"[A-Za-z0-9@._-]+", tool):
                continue
            script = (folder / target).resolve()
            try:
                script.relative_to(Path(node_modules).resolve())
            except ValueError:
                continue   # a bin path that leaves node_modules
            if not script.is_file() or not _is_node_script(script):
                continue
            relative = (Path("..") / rel / target).as_posix()
            bin_dir.mkdir(exist_ok=True)
            (bin_dir / f"{tool}.cmd").write_bytes(_CMD_SHIM.format(target=relative.replace("/", "\\")).encode())
            (bin_dir / f"{tool}.ps1").write_bytes(_PS1_SHIM.format(target=relative).encode())
            written.append(tool)
    return sorted(set(written))


def merge_windows_tree(project_modules: Path, windows_modules: Path) -> Result:
    """Make `project_modules` (installed on Linux) usable on Windows from `windows_modules` (the same project
    installed for Windows with no install scripts)."""
    project_modules, windows_modules = Path(project_modules), Path(windows_modules)
    result = Result()
    linux = packages(project_modules)
    for rel, folder in packages(windows_modules).items():
        manifest = _manifest(folder)
        target = project_modules / rel
        if rel not in linux:
            # Only what Windows needs and Linux does not have: a Windows platform package.
            if _only_for(manifest, "linux") and not _only_for(manifest, "win32") and target.parent.is_dir():
                _copy_tree(folder, target)
                result.added.append(rel.rsplit("node_modules/", 1)[-1])
            continue
        scripts = manifest.get("scripts") if isinstance(manifest.get("scripts"), dict) else {}
        if any(name in scripts for name in _INSTALL_SCRIPTS) and _restore_changed_files(folder, target):
            result.restored.append(rel.rsplit("node_modules/", 1)[-1])
    result.shims = write_shims(project_modules)
    result.native = native_addons(project_modules)
    return result

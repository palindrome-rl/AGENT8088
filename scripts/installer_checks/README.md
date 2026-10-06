# Public v1.2 installer regression checks

These checks execute individual installer functions against temporary folders,
fake processes, and fake package/download commands. They do not install system
packages, alter the real user configuration, or disable security software.

Install pytest in an isolated environment and run from the repository root:

```sh
python -m pytest scripts/installer_checks -q
```

Run on Windows with PowerShell and on macOS/Linux with bash. Platform-specific
checks skip on other hosts; a skip is not evidence that the platform was tested.
Tests live here rather than the omitted root `tests/` release directory and are
never imported by Agent8088. No additional runtime dependency is introduced.

Coverage includes terminal handoff confirmation and text-command execution,
spaces/apostrophes in paths, timeout/failure reporting, public repository errors,
partial-install cleanup, uninstall coordination, skipped-stage diagnostics,
installation locks, stage validation, and command startup/shadowing checks.

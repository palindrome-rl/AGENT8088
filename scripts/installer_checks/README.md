# Installer regression checks

These checks execute individual installer functions against temporary folders,
fake processes, and fake package/download commands. They do not install system
packages, alter the real user configuration, or disable security software.

Install the package and pytest in an isolated environment and run from the repository root:

```sh
python -m pip install . pytest
python -m pytest scripts/installer_checks -q
```

Run on Windows with PowerShell and on macOS/Linux with bash. Platform-specific
checks skip on other hosts; a skip is not evidence that the platform was tested.
These checks are never imported by Agent8088 and introduce no additional
runtime dependency.

Coverage includes terminal handoff confirmation and text-command execution,
spaces/apostrophes in paths, timeout/failure reporting, public repository errors,
partial-install cleanup, uninstall coordination, skipped-stage diagnostics,
installation locks, stage validation, and command startup/shadowing checks.

OpenCodeReview checks replay DNS failures, recovery, retry exhaustion, permission
errors, timeouts, missing binaries and exact-version verification. Runtime checks
also cover capability reporting, safe display, memory-save behavior, output-limit
recovery and the update channel without live model calls.

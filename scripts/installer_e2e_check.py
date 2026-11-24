"""After a real install: check the installed sandbox end to end. Run with the installed venv's Python.

Used by .github/workflows/installer-e2e.yml on throwaway runners. Prints what it found and exits non-zero when a
check that the backend can enforce fails. A check the active backend does not enforce (the Windows runner limits
writes only) is reported, not failed.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from agent8088 import engine

failures = []


def check(name, ok, detail=""):
    print(f"[{'pass' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)


backend = engine._resolve_sandbox_backend()
status = engine.sandbox_status()
print("sandbox:", {k: status.get(k) for k in ("resolved", "network", "network_enforced", "verification")})
check("a sandbox backend is available", backend in ("native", "docker"), backend)
if backend not in ("native", "docker"):
    sys.exit(1)
windows_native = backend == "native" and sys.platform == "win32"

# An ordinary folder (not owner-only like mkdtemp), as a user's project would be.
project = Path(tempfile.gettempdir()) / f"agent8088-e2e-{os.getpid()}"
(project / "artifacts").mkdir(parents=True, exist_ok=True)
subprocess.run(["git", "init", "-q", str(project)], check=True)
(project / "build.py").write_text("import os\nos.makedirs('out', exist_ok=True)\n"
                                  "open(os.path.join('out', 'app.txt'), 'w').write('built')\n", encoding="utf-8")
engine.PROJECT_ROOT = project
engine.ARTIFACTS_ROOT = project / "artifacts"
engine.reset_turn_counters()
python = "python" if backend == "docker" else f'"{sys.executable}"'
os.environ["E2E_SERVICE_TOKEN"] = "e2e-secret-value"


def run(command):
    return str(engine._run_sandbox_command(command, 300))


# A check for a protection this build does not have yet is skipped, so the same script runs on any branch.
if hasattr(engine, "_project_shell_root"):
    out = run(f"{python} build.py")
    check("a build writes out/ in the project", (project / "out" / "app.txt").exists(), out.strip()[-200:])
    hook = project / ".git" / "hooks" / "post-checkout"
    out = run(f"{python} -c \"open('.git/hooks/post-checkout', 'w').write('x')\"")
    check("a git hook cannot be planted", not hook.exists(), out.strip()[-200:])
else:
    out = run(f"{python} -c \"print('ran')\"")
    check("a sandboxed command runs", "ran" in out, out.strip()[-200:])

if hasattr(engine, "_secret_env_names"):
    out = run(f"{python} -c \"import os; print('token=[' + os.environ.get('E2E_SERVICE_TOKEN', '') + ']')\"")
    check("a secret variable is not inherited", "e2e-secret-value" not in out, out.strip()[-120:])

if windows_native:
    print("[skip] network checks: the Windows runner does not filter the network")
else:
    probe = ("import urllib.request as u, sys\n"
             "try:\n    print('STATUS', u.urlopen(sys.argv[1], timeout=20).status)\n"
             "except Exception as e:\n    print('REFUSED', type(e).__name__)\n")
    (project / "probe.py").write_text(probe, encoding="utf-8")
    if "pypi.org" in str(status.get("network")):
        out = run(f"{python} probe.py https://pypi.org/simple/six/")
        check("PyPI is reachable through the allowlist", "STATUS 200" in out, out.strip()[-200:])
    out = run(f"{python} probe.py https://example.com/")
    check("a host off the allowlist is refused", "STATUS" not in out, out.strip()[-200:])
    out = run(f"{python} probe.py http://169.254.169.254/latest/meta-data/")
    check("the metadata address is refused", "STATUS" not in out, out.strip()[-200:])

print(f"{len(failures)} failed" + (f": {', '.join(failures)}" if failures else ""))
sys.exit(1 if failures else 0)

"""Long-lived processes for a session: a dev server, an API, a database the task needs while it is being checked.

A shell command ends with the command that started it, so a server started by one command was gone before the next
one could test it, and a model left to its own devices backgrounded it with `&` (killed with the command anyway) or
waited on it forever. A service is started once, runs in the sandbox for as long as the session needs it, and is
reached from the host on its own port:

    start    run a command in the sandbox, with PORT set to a free port; wait until something listens on it
    status   what is running, for whom, on which address, for how long
    logs     the last lines it printed
    stop     end it and everything it started

Each service belongs to its owner (the main agent, or a child agent later), and an owner sees and stops only its own.
At most MAX_SERVICES run at once. Everything still running is stopped when the session ends, and a session that died
without stopping its services leaves a record, so the next session stops them (`reap_stale`).

This module knows nothing about sandboxes: the engine passes a launcher for the active backend that starts the
command and returns a Handle. The host port the launcher reports is the only address the host-side tools may reach.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MAX_SERVICES = 4
READY_SECONDS = 45.0
LOG_TAIL_BYTES = 64 * 1024
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass
class Handle:
    """What a launcher returns: how to stop the service, and how to tell it still runs."""
    stop: Callable[[], None]
    alive: Callable[[], bool]
    host_port: int
    pid: int | None = None                           # a host process to end after a crash
    containers: list = field(default_factory=list)   # Docker containers to remove after a crash
    note: str = ""                                   # how the host reaches it (shown in status)
    ready: Callable[[], bool] | None = None          # does it answer? (default: something listens on host_port)


@dataclass
class Service:
    name: str
    command: str
    owner: str
    port: int
    backend: str
    log_path: Path
    handle: Handle
    started: float = field(default_factory=time.time)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.handle.host_port}/"

    def describe(self) -> dict:
        return {"name": self.name, "owner": self.owner, "url": self.url, "port": self.port,
                "host_port": self.handle.host_port, "backend": self.backend, "running": self.handle.alive(),
                "seconds": int(time.time() - self.started), "command": self.command[:200],
                **({"note": self.handle.note} if self.handle.note else {})}


def free_port() -> int:
    """A port nothing on this computer listens on now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def listening(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def tail(path: Path, lines: int = 40) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-max(1, min(lines, 400)):])


class Registry:
    """The services of this process, and the record that lets a later process clean up after a crash."""

    def __init__(self, state_file: Path, log_dir: Path, pid: int | None = None):
        self.state_file = Path(state_file)
        self.log_dir = Path(log_dir)
        self.pid = pid or os.getpid()
        self.services: dict[str, Service] = {}
        self.lock = threading.Lock()

    # --- start / stop -----------------------------------------------------------------------------------

    def start(self, name: str, command: str, owner: str, backend: str, launch,
              port: int | None = None, ready_seconds: float = READY_SECONDS, wait: bool = True) -> Service:
        """Start `command` through `launch(command, port, log_path)`, which returns a Handle.

        Raises ValueError with a sentence for the model when the request cannot be served."""
        name = str(name or "").strip().lower()
        if not _NAME_RE.fullmatch(name):
            raise ValueError("name a service with lowercase letters, digits, - or _ (at most 32), for example `web`")
        if not str(command or "").strip():
            raise ValueError("give the command that starts the service")
        with self.lock:
            self._drop_ended()
            if name in self.services:
                raise ValueError(f"a service named {name} is already running ({self.services[name].url}); "
                                 "stop it first, or give the new one another name")
            if len(self.services) >= MAX_SERVICES:
                raise ValueError(f"{MAX_SERVICES} services are already running; stop one first")
            port = int(port) if port else free_port()
            if not 1024 <= port <= 65535:
                raise ValueError("use a port from 1024 to 65535, or leave it out to get a free one")
            if listening(port):
                raise ValueError(f"something on this computer already listens on port {port}; "
                                 "leave the port out to get a free one")
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.log_dir / f"{self.pid}-{name}.log"
            log_path.write_bytes(b"")
            handle = launch(command, port, log_path)
            service = Service(name, command, owner, port, backend, log_path, handle)
            self.services[name] = service
            self._save()
        if wait:
            self.wait_ready(service, ready_seconds)
        return service

    def wait_ready(self, service: Service, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.answers(service):
                return True
            if not service.handle.alive():
                return False
            time.sleep(0.25)
        return self.answers(service)

    @staticmethod
    def answers(service: Service) -> bool:
        """The service itself answers (a bridge in front of it listening is not enough)."""
        if service.handle.ready is not None:
            try:
                return bool(service.handle.ready())
            except Exception:  # noqa: BLE001
                return False
        return listening(service.handle.host_port)

    def stop(self, name: str, owner: str | None = None) -> Service | None:
        with self.lock:
            service = self.services.get(str(name or "").strip().lower())
            if service is None or (owner is not None and service.owner != owner):
                return None
            del self.services[service.name]
            self._save()
        try:
            service.handle.stop()
        except Exception:  # noqa: BLE001 -- stopping must never raise into the run
            pass
        return service

    def stop_all(self, owner: str | None = None) -> list[str]:
        names = [s.name for s in list(self.services.values()) if owner is None or s.owner == owner]
        return [name for name in names if self.stop(name)]

    # --- reading ----------------------------------------------------------------------------------------

    def get(self, name: str, owner: str | None = None) -> Service | None:
        service = self.services.get(str(name or "").strip().lower())
        return service if service is not None and (owner is None or service.owner == owner) else None

    def status(self, owner: str | None = None) -> list[dict]:
        return [s.describe() for s in self.services.values() if owner is None or s.owner == owner]

    def host_ports(self) -> set[int]:
        """The ports host-side tools may reach: those of services this session runs now."""
        return {s.handle.host_port for s in self.services.values() if s.handle.alive()}

    # --- the crash record --------------------------------------------------------------------------------

    def _drop_ended(self) -> None:
        """Forget services whose process ended on its own (their logs stay)."""
        for name in [n for n, s in self.services.items() if not s.handle.alive()]:
            service = self.services.pop(name)
            try:
                service.handle.stop()
            except Exception:  # noqa: BLE001
                pass

    def _records(self) -> list[dict]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self) -> None:
        mine = [{"owner_pid": self.pid, "name": s.name, "pid": s.handle.pid, "containers": list(s.handle.containers)}
                for s in self.services.values()]
        others = [r for r in self._records() if r.get("owner_pid") != self.pid]
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(others + mine), encoding="utf-8")
        except OSError:
            pass

    def reap_stale(self, process_alive: Callable[[int], bool], kill_tree: Callable[[int], None],
                   remove_containers: Callable[[list], None]) -> list[str]:
        """Stop what sessions that ended without stopping their services left running."""
        reaped, keep = [], []
        for record in self._records():
            owner = record.get("owner_pid")
            if not isinstance(owner, int) or owner == self.pid or process_alive(owner):
                keep.append(record)
                continue
            if isinstance(record.get("pid"), int):
                kill_tree(record["pid"])
            containers = [c for c in record.get("containers") or [] if isinstance(c, str)]
            if containers:
                remove_containers(containers)
            reaped.append(str(record.get("name")))
        try:
            if reaped:
                self.state_file.write_text(json.dumps(keep), encoding="utf-8")
        except OSError:
            pass
        return reaped

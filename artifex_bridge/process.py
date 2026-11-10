"""Own exactly one child process; never attach to or kill another Artifex."""

import atexit
import errno
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from .config import Config

log = logging.getLogger("comfyui.artifex")
_READY_PORT = re.compile(rb"\bINFO +server +gallery ready at http://127\.0\.0\.1:(\d+)\r?\n")
_DEFAULT_PORT = 18951
_MIN_PORT = 10000
_MAX_PORT = 65535
_MAX_PORT_OFFSET = 100
_UNAVAILABLE_PORT_ERRORS = {errno.EADDRINUSE, errno.EACCES, getattr(errno, "WSAEACCES", errno.EACCES)}


class ArtifexUnavailable(RuntimeError):
    pass


class ArtifexProcess:
    def __init__(self, config: Config):
        self.config = config
        self._process = None
        self._lock = threading.RLock()
        self._base_url = ""
        self._port = config.port
        self._port_file = config.runtime_dir / "port.txt"
        self._backend_log = None
        self._startup_log_offset = None
        self._opener = build_opener(ProxyHandler({}))
        self.library_id = None
        self._database_dir = None
        self._restart_pending = False
        self._library_aliases = {}
        atexit.register(self.stop)

    @property
    def base_url(self):
        return self._base_url

    @property
    def pid(self):
        return self._process.pid if self._process and self._process.poll() is None else None

    def _ping(self):
        child = self._process
        if child is None or child.poll() is not None:
            return False
        if self._startup_log_offset is not None:
            # The backend can try the next port when an existing config permits
            # it. Wait for its listening address before contacting any service.
            with open(self._backend_log.name, "rb") as stream:
                stream.seek(self._startup_log_offset)
                ready = _READY_PORT.search(stream.read())
            if ready is None:
                return False
            if int(ready[1]) != self._port:
                raise ArtifexUnavailable(f"Artifex could not listen on the selected port {self._port}")
            self._startup_log_offset = None
        try:
            request = Request(self._base_url + "/api/groups", headers={"Accept": "application/json"})
            with self._opener.open(request, timeout=1.0) as response:
                payload = json.load(response)
                library_id = response.headers.get("X-Artifex-Library")
                if response.status != 200 or not isinstance(payload, list) or not library_id:
                    return False
                settings_request = Request(self._base_url + "/api/settings", headers={"X-Artifex-Library": library_id})
                with self._opener.open(settings_request, timeout=1.0) as settings_response:
                    database_dir = str(Path(json.load(settings_response)["database_dir"]).resolve())
                if child.poll() is not None:
                    return False
                if self.library_id and self.library_id != library_id:
                    if self._restart_pending and self._database_dir == database_dir:
                        self._library_aliases = {old: library_id for old in self._library_aliases}
                        self._library_aliases[self.library_id] = library_id
                    else:
                        self._library_aliases.clear()
                self.library_id = library_id
                self._database_dir = database_dir
                self._restart_pending = False
                return True
        except (OSError, URLError) as exc:
            timed_out = isinstance(exc, TimeoutError) or (isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError))
            if timed_out:
                return self._busy_backend_alive(child)
            return False
        except (ValueError, KeyError):
            return False

    def _busy_backend_alive(self, child):
        # Loading a model holds Artifex's library lock. Static frontend requests
        # do not take that lock, so a busy API must not trigger a process kill.
        # This fallback is never sufficient for initial/restart readiness.
        if not self.library_id or self._restart_pending or self._process is not child or child.poll() is not None:
            return False
        try:
            with self._opener.open(Request(self._base_url + "/"), timeout=1.0) as response:
                return response.status == 200 and self._process is child and child.poll() is None
        except (OSError, URLError):
            return False

    def expected_library(self, library_id):
        """Translate only a managed restart of the same database directory."""
        return self._library_aliases.get(library_id, library_id)

    def _prepare(self) -> Path:
        source = self.config.binary_path
        if not source.is_file():
            raise FileNotFoundError(f"Bundled Artifex executable is missing: {source}")
        if not (self.config.frontend_dir / "index.html").is_file():
            raise FileNotFoundError(f"Bundled Artifex frontend is missing: {self.config.frontend_dir}")
        target_dir = self.config.runtime_dir / "bin"
        for directory in (target_dir, self.config.data_dir, self.config.log_dir):
            directory.mkdir(parents=True, exist_ok=True)
        # Artifex locates config.toml relative to its executable, not its cwd.
        # Copy only runtime binaries, leaving the immutable release untouched.
        for item in source.parent.iterdir():
            if item.is_file() and (item == source or ".so" in item.name or item.suffix.lower() in {".dll", ".dylib"}):
                target = target_dir / item.name
                if not target.exists() or (item.stat().st_size, item.stat().st_mtime_ns) != (target.stat().st_size, target.stat().st_mtime_ns):
                    shutil.copy2(item, target)
        executable = target_dir / source.name
        if os.name != "nt":
            executable.chmod(executable.stat().st_mode | 0o111)
        settings = target_dir / "config.toml"
        if not settings.exists():
            settings.write_text(
                f"database_dir = {json.dumps(str(self.config.data_dir.resolve()), ensure_ascii=False)}\n"
                'auto_tag = false\nupload_conflict_policy = "save_all"\n'
                "[cli]\nport_attempts = 1\n",
                encoding="utf-8",
            )
        return executable

    def _select_port(self) -> int:
        start = self.config.port
        if not start:
            try:
                start = int(self._port_file.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                start = _DEFAULT_PORT
            if not _MIN_PORT <= start <= _MAX_PORT:
                start = _DEFAULT_PORT
        elif not _MIN_PORT <= start <= _MAX_PORT:
            raise ArtifexUnavailable(f"Artifex start port must be between {_MIN_PORT} and {_MAX_PORT}")

        end = min(start + _MAX_PORT_OFFSET, _MAX_PORT)
        # Windows must detect exclusive listeners. On POSIX, match Go's
        # listener and allow reuse of an address with only TIME_WAIT sockets.
        socket_option = socket.SO_EXCLUSIVEADDRUSE if os.name == "nt" else socket.SO_REUSEADDR
        for port in range(start, end + 1):
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket_option, 1)
                try:
                    probe.bind(("127.0.0.1", port))
                    probe.listen()
                except OSError as exc:
                    if exc.errno not in _UNAVAILABLE_PORT_ERRORS:
                        raise
                else:
                    return port
        raise ArtifexUnavailable(f"No available Artifex port on 127.0.0.1 in range {start}-{end}")

    def _spawn(self):
        executable = self._prepare()
        self._port = self._select_port()
        self._base_url = f"http://127.0.0.1:{self._port}"
        self._backend_log = (self.config.log_dir / "artifex-backend.log").open("ab", buffering=0)
        self._startup_log_offset = self._backend_log.tell()
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            self._restart_pending = True
            self._process = subprocess.Popen(
                [str(executable), "--no-ui", "--host", "127.0.0.1", "--port", str(self._port),
                 "--frontend", str(self.config.frontend_dir.resolve())],
                cwd=str(executable.parent), stdin=subprocess.DEVNULL,
                stdout=self._backend_log, stderr=subprocess.STDOUT, **kwargs,
            )
        except Exception:
            self._backend_log.close()
            self._backend_log = None
            raise

    def _stop_child(self):
        child = self._process
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        self._process = None
        self._startup_log_offset = None
        if self._backend_log:
            self._backend_log.close()
            self._backend_log = None

    def _failure_detail(self):
        path = self.config.log_dir / "artifex-backend.log"
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 1600))
                return stream.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return "No backend log was written."

    def ensure_running(self) -> str:
        """Check the owned PID and an existing HTTP endpoint before every operation."""
        with self._lock:
            if self._ping():
                return self._base_url
            failures = []
            for attempt in range(3):
                if attempt:
                    time.sleep(self.config.retry_delay * 2 ** (attempt - 1))
                try:
                    self._stop_child()
                    self._spawn()
                    deadline = time.monotonic() + self.config.startup_timeout
                    while time.monotonic() < deadline:
                        if self._ping():
                            log.info("Artifex started (PID %s) at %s", self.pid, self._base_url)
                            return self._base_url
                        if self._process.poll() is not None:
                            raise ArtifexUnavailable(f"Artifex exited with code {self._process.returncode}: {self._failure_detail()}")
                        time.sleep(0.1)
                    raise ArtifexUnavailable(f"Artifex did not answer HTTP within {self.config.startup_timeout:g}s: {self._failure_detail()}")
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    failures.append(str(exc))
                    log.warning("Artifex start attempt %s/3 failed: %s", attempt + 1, exc)
            self._stop_child()
            raise ArtifexUnavailable("Artifex failed to start after 3 attempts: " + failures[-1])

    def start_background(self):
        def start():
            try:
                self.ensure_running()
            except Exception:
                log.exception("Artifex startup failed; the next operation will retry")
        threading.Thread(target=start, name="Artifex startup", daemon=True).start()

    def stop(self):
        with self._lock:
            self._stop_child()

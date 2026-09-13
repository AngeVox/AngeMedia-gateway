"""Safe fnOS queue runtime detection and Local/Celery switching."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from .. import config as C
from ..db.connection import db_connect
from ..providers.endpoint_policy import validate_admin_configured_host
from ..queue.settings import QueueSettings
from ..repositories.jobs import count_jobs

_QUEUE_ENV_KEYS = ("QUEUE_ENABLED", "QUEUE_BACKEND", "REDIS_URL", "CELERY_BROKER_URL")
_PROCESS_MODULES = {
    "dispatcher": "angemedia_gateway.cli.dispatcher",
    "worker": "angemedia_gateway.cli.worker",
}


class QueueRuntimeError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validate_redis_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        raise QueueRuntimeError(400, "Redis URL is required.")
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise QueueRuntimeError(400, "Redis URL is invalid.") from exc
    if parsed.scheme not in {"redis", "rediss"}:
        raise QueueRuntimeError(400, "Redis URL must use redis:// or rediss://.")
    if not parsed.hostname:
        raise QueueRuntimeError(400, "Redis URL is missing a hostname.")
    if parsed.fragment:
        raise QueueRuntimeError(400, "Redis URL must not contain a fragment.")
    if port is not None and not (1 <= port <= 65535):
        raise QueueRuntimeError(400, "Redis port is invalid.")
    if parsed.path not in {"", "/"}:
        db_value = parsed.path.removeprefix("/")
        if not db_value.isdigit() or int(db_value) > 15:
            raise QueueRuntimeError(400, "Redis database must be between 0 and 15.")
    try:
        validate_admin_configured_host(parsed.hostname)
    except ValueError as exc:
        raise QueueRuntimeError(400, "Redis target is not allowed.") from exc
    return url


def _safe_redis_target(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    port = parsed.port or (6380 if parsed.scheme == "rediss" else 6379)
    return {
        "host": str(parsed.hostname or "")[:253],
        "port": port,
        "tls": parsed.scheme == "rediss",
        "credentials_configured": bool(parsed.username or parsed.password),
    }


class QueueRuntimeService:
    def __init__(self) -> None:
        self.redis_timeout = 1.5

    def summary(self) -> dict[str, Any]:
        settings = self._settings()
        active = self._active_counts()
        return {
            "backend": settings.backend,
            "enabled": settings.enabled,
            "can_switch": self._can_manage(),
            "platform": "fnos" if self._can_manage() else "external",
            "redis_configured": bool(self._saved_redis_url()),
            "active_jobs": active["jobs"],
            "active_dispatches": active["dispatches"],
            "processes": self._process_summary(),
        }

    def detect_redis(self, redis_url: str | None = None) -> dict[str, Any]:
        candidates: list[tuple[str, str]] = []
        if redis_url and str(redis_url).strip():
            candidates.append(("manual", validate_redis_url(redis_url)))
        else:
            saved = self._saved_redis_url()
            if saved:
                candidates.append(("saved", validate_redis_url(saved)))
            default_url = "redis://127.0.0.1:6379/0"
            if not any(url == default_url for _, url in candidates):
                candidates.append(("local_default", default_url))

        results = []
        recommended = None
        for source, url in candidates:
            reachable = self._redis_ping(url)
            item = {"source": source, "reachable": reachable, **_safe_redis_target(url)}
            results.append(item)
            if reachable and recommended is None:
                recommended = source
        return {
            "detected": recommended is not None,
            "recommended_source": recommended,
            "candidates": results,
        }

    def switch_backend(self, backend: str, redis_url: str | None = None) -> dict[str, Any]:
        target = str(backend or "").strip().lower()
        if target not in {"local", "celery"}:
            raise QueueRuntimeError(400, "Queue backend must be local or celery.")
        if not self._can_manage():
            raise QueueRuntimeError(409, "Queue runtime switching is available only in the managed fnOS package.")

        with self._switch_lock():
            active = self._active_counts()
            if active["jobs"] or active["dispatches"]:
                raise QueueRuntimeError(409, "Queue backend cannot be switched while jobs or dispatches are active.")

            current = self._settings()
            chosen_redis = None
            if target == "celery":
                chosen_redis = validate_redis_url(redis_url) if redis_url and str(redis_url).strip() else self._saved_redis_url()
                if not chosen_redis:
                    chosen_redis = "redis://127.0.0.1:6379/0"
                chosen_redis = validate_redis_url(chosen_redis)
                if not self._redis_ping(chosen_redis):
                    raise QueueRuntimeError(409, "Redis is not reachable; queue backend was not changed.")

            env_file = self._env_file()
            if env_file is None:
                raise QueueRuntimeError(409, "Managed queue environment is unavailable.")
            original_file = env_file.read_bytes()
            original_env = {key: os.environ.get(key) for key in _QUEUE_ENV_KEYS}
            updates = {"QUEUE_ENABLED": "true", "QUEUE_BACKEND": target}
            if chosen_redis:
                updates["REDIS_URL"] = chosen_redis
                updates["CELERY_BROKER_URL"] = chosen_redis

            try:
                self._write_env_values(env_file, updates)
                self._apply_process_env(updates)
                self._refresh_cached_celery_app(target)
                self._restart_topology(target)
                if target == "celery" and chosen_redis and not self._redis_ping(chosen_redis):
                    raise RuntimeError("Redis health check failed after switch")
            except Exception as exc:
                rollback_ok = self._rollback(env_file, original_file, original_env, current.backend)
                if rollback_ok:
                    raise QueueRuntimeError(500, "Queue switch failed; the previous backend was restored.") from exc
                raise QueueRuntimeError(500, "Queue switch failed and automatic rollback did not complete. Restart the app from fnOS.") from exc

            return self.summary()

    def _settings(self) -> QueueSettings:
        try:
            return QueueSettings.from_env()
        except Exception as exc:
            raise QueueRuntimeError(500, "Queue runtime configuration is invalid.") from exc

    def _saved_redis_url(self) -> str | None:
        env_file = self._env_file()
        if env_file is not None:
            for key in ("CELERY_BROKER_URL", "REDIS_URL"):
                value = self._read_env_value(env_file, key)
                if value:
                    return value
            return None
        value = str(os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL") or "").strip()
        return value or None

    @staticmethod
    def _read_env_value(path: Path, key: str) -> str | None:
        prefix = f"{key}="
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            if not line.startswith(prefix):
                continue
            raw = line[len(prefix):].strip()
            try:
                parts = shlex.split(raw, comments=False, posix=True)
            except ValueError:
                return None
            return parts[0].strip() if len(parts) == 1 and parts[0].strip() else None
        return None

    def _active_counts(self) -> dict[str, int]:
        try:
            jobs = count_jobs(status="queued") + count_jobs(status="running")
            with closing(db_connect()) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS total FROM job_dispatches WHERE status IN ('pending','publishing')"
                ).fetchone()
            dispatches = int(row["total"] if row is not None else 0)
        except Exception as exc:
            raise QueueRuntimeError(500, "Queue state could not be verified; backend switching is blocked.") from exc
        return {"jobs": jobs, "dispatches": dispatches}

    def _redis_ping(self, url: str) -> bool:
        try:
            import redis

            client = redis.Redis.from_url(
                url,
                socket_connect_timeout=self.redis_timeout,
                socket_timeout=self.redis_timeout,
                health_check_interval=0,
            )
            try:
                return bool(client.ping())
            finally:
                client.close()
        except Exception:
            return False

    def _env_file(self) -> Path | None:
        root = str(os.getenv("TRIM_PKGETC") or "").strip()
        if not root:
            return None
        path = Path(root) / "angemedia.env"
        return path if path.is_file() else None

    def _run_dir(self) -> Path | None:
        root = str(os.getenv("TRIM_PKGVAR") or "").strip()
        if not root:
            return None
        return Path(root) / "run"

    def _log_dir(self) -> Path | None:
        value = str(os.getenv("ANGEMEDIA_LOG_DIR") or "").strip()
        return Path(value) if value else None

    def _can_manage(self) -> bool:
        env_file = self._env_file()
        run_dir = self._run_dir()
        log_dir = self._log_dir()
        return bool(env_file and run_dir and log_dir and Path(sys.executable).is_file())

    def _process_summary(self) -> dict[str, bool]:
        return {
            name: self._is_running(name)
            for name in _PROCESS_MODULES
        }

    def _pid_file(self, name: str) -> Path:
        run_dir = self._run_dir()
        if run_dir is None:
            raise QueueRuntimeError(409, "Managed run directory is unavailable.")
        return run_dir / f"{name}.pid"

    def _read_pid(self, name: str) -> int | None:
        path = self._pid_file(name)
        try:
            value = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return value if value > 1 else None

    def _pid_matches(self, name: str, pid: int) -> bool:
        expected = _PROCESS_MODULES[name]
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
        except OSError:
            return False
        return expected in raw

    def _is_running(self, name: str) -> bool:
        pid = self._read_pid(name)
        if pid is None or not self._pid_matches(name, pid):
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _stop_one(self, name: str) -> None:
        pid_file = self._pid_file(name)
        pid = self._read_pid(name)
        if pid is None or not self._pid_matches(name, pid):
            pid_file.unlink(missing_ok=True)
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pid_file.unlink(missing_ok=True)
            return
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.25)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        pid_file.unlink(missing_ok=True)

    def _start_one(self, name: str) -> None:
        if self._is_running(name):
            return
        run_dir = self._run_dir()
        log_dir = self._log_dir()
        if run_dir is None or log_dir is None:
            raise RuntimeError("managed runtime paths are unavailable")
        run_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        module = _PROCESS_MODULES[name]
        log_path = log_dir / f"{name}.log"
        env = os.environ.copy()
        with log_path.open("ab", buffering=0) as log_handle:
            proc = subprocess.Popen(
                [sys.executable, "-m", module],
                cwd=str(C.PROJECT_ROOT),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        self._pid_file(name).write_text(f"{proc.pid}\n", encoding="utf-8")
        time.sleep(0.75)
        if not self._is_running(name):
            raise RuntimeError(f"{name} failed to start")

    def _restart_topology(self, backend: str) -> None:
        self._stop_one("dispatcher")
        self._stop_one("worker")
        if backend == "celery":
            self._start_one("worker")
        self._start_one("dispatcher")

    def _refresh_cached_celery_app(self, backend: str) -> None:
        module = sys.modules.get("angemedia_gateway.queue.celery_app")
        if module is None:
            return
        current = getattr(module, "celery_app", None)
        if current is not None:
            try:
                current.close()
            except Exception:
                pass
        if backend == "celery":
            factory = getattr(module, "create_celery_app", None)
            if not callable(factory):
                raise RuntimeError("Celery app factory is unavailable")
            module.celery_app = factory(QueueSettings.from_env())

    def _write_env_values(self, path: Path, updates: dict[str, str]) -> None:
        original = path.read_text(encoding="utf-8").splitlines()
        keys = set(updates)
        kept = [line for line in original if not any(line.startswith(f"{key}=") for key in keys)]
        kept.extend(f"{key}={shlex.quote(str(value))}" for key, value in updates.items())
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    @staticmethod
    def _apply_process_env(updates: dict[str, str]) -> None:
        for key, value in updates.items():
            os.environ[key] = str(value)

    def _rollback(
        self,
        env_file: Path,
        original_file: bytes,
        original_env: dict[str, str | None],
        backend: str,
    ) -> bool:
        try:
            tmp = env_file.with_name(f"{env_file.name}.rollback.{os.getpid()}")
            tmp.write_bytes(original_file)
            os.chmod(tmp, 0o600)
            os.replace(tmp, env_file)
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            self._refresh_cached_celery_app(backend)
            if backend in {"local", "celery"}:
                self._restart_topology(backend)
            return True
        except Exception:
            return False

    @contextmanager
    def _switch_lock(self) -> Iterator[None]:
        run_dir = self._run_dir()
        if run_dir is None:
            raise QueueRuntimeError(409, "Managed run directory is unavailable.")
        run_dir.mkdir(parents=True, exist_ok=True)
        lock_path = run_dir / "queue-switch.lock"
        handle = lock_path.open("a+")
        try:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise QueueRuntimeError(409, "Another queue switch is already in progress.") from exc
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            handle.close()

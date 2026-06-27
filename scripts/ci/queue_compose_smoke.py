"""Run the real Docker compose queue smoke gate.

The smoke uses QUEUE_SMOKE_FAKE_PROVIDERS=true so provider execution writes
local fixtures, while admission, outbox dispatch, Celery/Redis, worker runtime,
Jobs/Dashboard/Assets APIs, and Studio assets all run through the normal stack.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROJECT_NAME = "angemedia-queue-smoke"
ADMIN_PASSWORD = "queue-smoke-admin-password"
GATEWAY_KEY = "am-queue-smoke-local-only"
FORBIDDEN_RESPONSE_TERMS = (
    "input_json",
    "output_json",
    "request_hash",
    "provider_body",
    "raw_body",
    "raw_response",
    "signed_url",
    "token=queue-smoke",
    "Authorization",
    "Bearer ",
)
FORBIDDEN_REDIS_TERMS = (
    "queue smoke image",
    "queue smoke video",
    "input_json",
    "output_json",
    "request_hash",
    "provider_body",
    "raw_body",
    "raw_response",
    "signed_url",
    "token=queue-smoke",
    GATEWAY_KEY,
)


class SmokeError(RuntimeError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Docker compose queue live smoke gate")
    parser.add_argument("--dry-run", action="store_true", help="Print the safe plan without starting Docker")
    parser.add_argument("--keep", action="store_true", help="Do not docker compose down after the run")
    parser.add_argument("--port", default=os.getenv("QUEUE_SMOKE_HOST_PORT", "9894"))
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser.parse_args(argv)


def compose_cmd(args: argparse.Namespace) -> list[str]:
    return [
        "docker", "compose",
        "-p", PROJECT_NAME,
        "-f", "docker-compose.yml",
        "-f", "docker-compose.queue-smoke.yml",
    ]


def compose_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "ADMIN_USERNAME": "admin",
        "ADMIN_DEFAULT_PASSWORD": ADMIN_PASSWORD,
        "GATEWAY_API_KEY": GATEWAY_KEY,
        "QUEUE_SMOKE_HOST_PORT": str(args.port),
        "QUEUE_SMOKE_FAKE_PROVIDERS": "true",
        "BUILTIN_PROVIDER_SILICONFLOW_ENABLED": "false",
        "BUILTIN_PROVIDER_MODELSCOPE_ENABLED": "false",
        "BUILTIN_PROVIDER_OPENAI_IMAGE_ENABLED": "false",
        "BUILTIN_PROVIDER_AGNES_IMAGE_ENABLED": "false",
        "BUILTIN_PROVIDER_AGNES_VIDEO_ENABLED": "false",
        "BUILTIN_PROVIDER_POLLINATIONS_ENABLED": "false",
    })
    return env


def run_process(command: list[str], *, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and result.returncode != 0:
        raise SmokeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}")
    return result


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookie_jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected: int = 200,
    ) -> Any:
        data = None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with self.opener.open(request, timeout=8) as response:
                body = response.read()
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            status = int(exc.code)
        if status != expected:
            raise SmokeError(f"{method} {path} expected {expected}, got {status}: {body[:500]!r}")
        if not body:
            return {}
        content_type = ""
        try:
            content_type = response.headers.get("content-type", "")  # type: ignore[possibly-undefined]
        except Exception:
            pass
        text = body.decode("utf-8", errors="replace")
        if "application/json" in content_type or text.startswith(("{", "[")):
            return json.loads(text)
        return text


def wait_for_http(client: Client, path: str, *, timeout: float) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            client.request("GET", path, expected=200)
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1)
    raise SmokeError(f"timed out waiting for {path}: {last_error}")


def wait_for_redis(args: argparse.Namespace, env: dict[str, str], *, timeout: float) -> None:
    deadline = time.time() + timeout
    command = compose_cmd(args) + ["exec", "-T", "redis", "redis-cli", "ping"]
    while time.time() < deadline:
        result = run_process(command, env=env, check=False)
        if result.returncode == 0 and "PONG" in result.stdout:
            return
        time.sleep(1)
    raise SmokeError("timed out waiting for Redis broker")


def login(client: Client) -> None:
    client.request(
        "POST",
        "/v1/admin/login",
        payload={"username": "admin", "password": ADMIN_PASSWORD},
        expected=200,
    )


def submit_jobs(client: Client) -> tuple[str, str]:
    image = client.request(
        "POST",
        "/v1/admin/jobs/images",
        payload={"prompt": "queue smoke image", "model": "queue-smoke-image", "response_format": "url"},
        expected=202,
    )
    video = client.request(
        "POST",
        "/v1/admin/jobs/videos",
        payload={"prompt": "queue smoke video", "model": "queue-smoke-video", "wait_for_completion": False},
        expected=202,
    )
    return str(image["job_id"]), str(video["job_id"])


def wait_for_job(client: Client, job_id: str, expected_status: str, *, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        body = client.request("GET", f"/v1/admin/jobs/{urllib.parse.quote(job_id)}", expected=200)
        latest = body.get("data") or {}
        if latest.get("status") == expected_status:
            assert_no_forbidden_payload(latest, f"job {job_id}")
            return latest
        if latest.get("status") == "failed" and expected_status != "failed":
            raise SmokeError(f"job {job_id} failed unexpectedly: {json.dumps(latest, ensure_ascii=False)[:1000]}")
        time.sleep(1)
    raise SmokeError(f"job {job_id} did not reach {expected_status}: {json.dumps(latest, ensure_ascii=False)[:1000]}")


def assert_gateway_key_rejected(base_url: str) -> None:
    client = Client(base_url)
    headers = {"Authorization": f"Bearer {GATEWAY_KEY}"}
    client.request("GET", "/v1/admin/jobs", headers=headers, expected=403)
    client.request("GET", "/v1/admin/dashboard/summary", headers=headers, expected=403)


def assert_dashboard_and_assets(client: Client, image_job_id: str, video_job_id: str) -> None:
    dashboard = client.request("GET", "/v1/admin/dashboard/summary", expected=200)
    assert_no_forbidden_payload(dashboard, "dashboard summary")
    data = dashboard.get("data") or {}
    if int(data.get("queue", {}).get("status_counts", {}).get("succeeded", 0)) < 1:
        raise SmokeError("dashboard summary did not observe succeeded jobs")

    for job_id in (image_job_id, video_job_id):
        assets = client.request("GET", f"/v1/assets?job_id={urllib.parse.quote(job_id)}", expected=200)
        assert_no_forbidden_payload(assets, f"assets for {job_id}")
        if not assets.get("data"):
            raise SmokeError(f"no asset was linked to job {job_id}")


def assert_modules_200(client: Client) -> None:
    for path in (
        "/studio",
        "/assets/studio/features/dashboard/page.js",
        "/assets/studio/features/jobs/page.js",
        "/assets/studio/features/assets/page.js",
        "/assets/studio/features/generate-image/page.js",
        "/assets/studio/features/generate-video/page.js",
    ):
        client.request("GET", path, expected=200)


def assert_no_forbidden_payload(value: Any, label: str) -> None:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    for term in FORBIDDEN_RESPONSE_TERMS:
        if term in text:
            raise SmokeError(f"{label} leaked forbidden term: {term}")


def assert_redis_payload_safe(args: argparse.Namespace, env: dict[str, str]) -> None:
    keys_result = run_process(
        compose_cmd(args) + ["exec", "-T", "redis", "redis-cli", "--raw", "KEYS", "*"],
        env=env,
        check=False,
    )
    if keys_result.returncode != 0:
        raise SmokeError(f"could not inspect Redis keys:\n{keys_result.stdout}")
    combined = keys_result.stdout
    for key in [line.strip() for line in keys_result.stdout.splitlines() if line.strip()]:
        type_result = run_process(
            compose_cmd(args) + ["exec", "-T", "redis", "redis-cli", "--raw", "TYPE", key],
            env=env,
            check=False,
        )
        redis_type = type_result.stdout.strip()
        if redis_type == "string":
            command = ["GET", key]
        elif redis_type == "list":
            command = ["LRANGE", key, "0", "-1"]
        elif redis_type == "zset":
            command = ["ZRANGE", key, "0", "-1"]
        elif redis_type == "hash":
            command = ["HGETALL", key]
        else:
            continue
        value_result = run_process(
            compose_cmd(args) + ["exec", "-T", "redis", "redis-cli", "--raw", *command],
            env=env,
            check=False,
        )
        combined += "\n" + value_result.stdout
    for term in FORBIDDEN_REDIS_TERMS:
        if term in combined:
            raise SmokeError(f"Redis payload leaked forbidden term: {term}")


def run_smoke(args: argparse.Namespace) -> None:
    env = compose_env(args)
    command = compose_cmd(args)
    client = Client(f"http://127.0.0.1:{args.port}")
    if args.dry_run:
        print("QUEUE_SMOKE_FAKE_PROVIDERS=true")
        print("docker compose command:", " ".join(command))
        print("planned checks: health, studio modules, redis, admin session, queued image/video, jobs, dashboard, assets, redis payload, cleanup")
        return
    cleaned = False
    try:
        run_process(command + ["up", "-d", "--build"], env=env)
        wait_for_http(client, "/health", timeout=args.timeout)
        wait_for_http(client, "/studio", timeout=args.timeout)
        assert_modules_200(client)
        wait_for_redis(args, env, timeout=args.timeout)
        login(client)
        assert_gateway_key_rejected(client.base_url)
        image_job_id, video_job_id = submit_jobs(client)
        wait_for_job(client, image_job_id, "succeeded", timeout=args.timeout)
        wait_for_job(client, video_job_id, "succeeded", timeout=args.timeout)
        assert_dashboard_and_assets(client, image_job_id, video_job_id)
        assert_redis_payload_safe(args, env)
        print("QUEUE_COMPOSE_SMOKE PASS")
    finally:
        if not args.keep:
            run_process(command + ["down", "-v", "--remove-orphans"], env=env, check=False)
            cleaned = True
        if not cleaned and args.keep:
            print("QUEUE_COMPOSE_SMOKE kept stack for inspection", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_smoke(args)
        return 0
    except SmokeError as exc:
        print(f"QUEUE_COMPOSE_SMOKE FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

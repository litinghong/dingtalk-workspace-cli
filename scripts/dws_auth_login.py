#!/usr/bin/env python3
"""
Reproduce `dws auth login --device` in Python and print token fields.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import parse, request, error


DEFAULT_MCP_BASE_URL = "https://mcp.dingtalk.com"
DEFAULT_DEVICE_BASE_URL = "https://login.dingtalk.com"
DEVICE_CODE_PATH = "/oauth2/device/code.json"
DEVICE_TOKEN_PATH = "/oauth2/device/token.json"
DEVICE_POLL_PATH = "/cli/oauth/device/poll"
CLIENT_ID_PATH = "/cli/clientId"
MCP_OAUTH_TOKEN_PATH = "/oauth2/getToken"
DEFAULT_SCOPES = "openid corpid"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3
MAX_POLL_INTERVAL = 30
MAX_POLL_TOTAL_WAIT = 10 * 60


@dataclass
class PendingDeviceFlow:
    client_id: str
    auth: Dict[str, Any]
    created_at: str


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.isoformat()


def get_config_dir() -> Path:
    env_dir = os.getenv("DWS_CONFIG_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    return Path.home() / ".dws"


def read_text_if_exists(path: Path) -> Optional[str]:
    try:
        txt = path.read_text(encoding="utf-8").strip()
        return txt or None
    except FileNotFoundError:
        return None


def get_mcp_base_url(config_dir: Path) -> str:
    v = read_text_if_exists(config_dir / "mcp_url")
    return (v or DEFAULT_MCP_BASE_URL).rstrip("/")


def http_request(
    method: str,
    url: str,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> bytes:
    req = request.Request(url=url, method=method, data=data, headers=headers or {})
    try:
        with request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
            return resp.read()
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {url}: {body[:300]}") from e
    except error.URLError as e:
        raise RuntimeError(f"request failed {url}: {e}") from e


def fetch_client_id(mcp_base_url: str, ssl_context: Optional[ssl.SSLContext]) -> str:
    url = mcp_base_url + CLIENT_ID_PATH
    last_err: Optional[Exception] = None
    for attempt in range(DEFAULT_RETRIES):
        if attempt > 0:
            time.sleep(attempt)
        try:
            body = http_request("GET", url, ssl_context=ssl_context)
            payload = json.loads(body)
            if not payload.get("success"):
                raise RuntimeError(f"{payload.get('errorCode')}: {payload.get('errorMsg')}")
            client_id = payload.get("result", "")
            if not client_id:
                raise RuntimeError("empty clientId result")
            return client_id
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"fetch clientId failed after {DEFAULT_RETRIES} attempts: {last_err}")


def request_device_code(
    device_base_url: str,
    client_id: str,
    scope: str,
    ssl_context: Optional[ssl.SSLContext],
) -> Dict[str, Any]:
    url = device_base_url + DEVICE_CODE_PATH
    form = {"client_id": client_id}
    if scope:
        form["scope"] = scope
    body = http_request(
        "POST",
        url,
        data=parse.urlencode(form).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        ssl_context=ssl_context,
    )
    payload = json.loads(body)
    if not payload.get("success"):
        raise RuntimeError(f"request device code failed: {payload.get('errorCode')} {payload.get('errorMsg')}")
    result = payload.get("result", {})
    if not result.get("deviceCode") or not result.get("userCode"):
        raise RuntimeError("device code response missing deviceCode/userCode")
    interval = int(result.get("interval", 2))
    if interval <= 0 or interval > MAX_POLL_INTERVAL:
        interval = 2
    result["interval"] = interval
    if int(result.get("expiresIn", 0)) <= 0:
        result["expiresIn"] = 900
    return result


def poll_by_flow_id(mcp_base_url: str, flow_id: str, ssl_context: Optional[ssl.SSLContext]) -> Dict[str, Any]:
    url = f"{mcp_base_url}{DEVICE_POLL_PATH}?flowId={parse.quote(flow_id)}"
    body = http_request("GET", url, ssl_context=ssl_context)
    payload = json.loads(body)
    data = payload.get("data") or {}
    result = payload.get("result") or {}
    effective = data if data.get("status") or not result.get("status") else result
    status = effective.get("status", "")
    if (not payload.get("success")) and not status:
        raise RuntimeError(f"poll failed: {payload.get('code')} {payload.get('message')}")
    return effective


def poll_by_device_code(
    device_base_url: str,
    client_id: str,
    device_code: str,
    ssl_context: Optional[ssl.SSLContext],
) -> Dict[str, Any]:
    url = device_base_url + DEVICE_TOKEN_PATH
    form = {
        "grant_type": DEVICE_GRANT_TYPE,
        "device_code": device_code,
        "client_id": client_id,
    }
    body = http_request(
        "POST",
        url,
        data=parse.urlencode(form).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        ssl_context=ssl_context,
    )
    payload = json.loads(body)
    if not payload.get("success"):
        raise RuntimeError(f"poll failed: {payload.get('errorCode')} {payload.get('errorMsg')}")
    return payload.get("result", {})


def wait_for_authorization(
    mcp_base_url: str,
    device_base_url: str,
    client_id: str,
    auth: Dict[str, Any],
    ssl_context: Optional[ssl.SSLContext],
) -> str:
    interval = int(auth.get("interval", 2))
    if interval <= 0:
        interval = 2
    expires_in = int(auth.get("expiresIn", 900))
    start = time.time()
    poll_count = 0
    while True:
        elapsed = time.time() - start
        if elapsed >= MAX_POLL_TOTAL_WAIT or elapsed >= expires_in:
            raise RuntimeError(f"device auth expired ({expires_in}s)")
        time.sleep(interval)
        poll_count += 1
        if auth.get("flowId"):
            poll = poll_by_flow_id(mcp_base_url, auth["flowId"], ssl_context)
            status = (poll.get("status") or "").upper()
            if status == "APPROVED":
                code = poll.get("authCode", "")
                if not code:
                    raise RuntimeError("approved but authCode missing")
                return code
            if status == "PENDING":
                print(f"[poll #{poll_count}] pending", file=sys.stderr)
                continue
            if status == "REJECTED":
                raise RuntimeError("authorization rejected by user")
            if status == "EXPIRED":
                raise RuntimeError("device authorization expired")
            print(f"[poll #{poll_count}] unknown status={status}", file=sys.stderr)
            continue
        poll = poll_by_device_code(device_base_url, client_id, auth["deviceCode"], ssl_context)
        err = poll.get("error", "")
        if not err:
            code = poll.get("authCode", "")
            if not code:
                raise RuntimeError("authCode missing")
            return code
        err = err.lower()
        if err == "authorization_pending":
            print(f"[poll #{poll_count}] pending", file=sys.stderr)
            continue
        if err == "slow_down":
            interval = min(interval + 5, MAX_POLL_INTERVAL)
            print(f"[poll #{poll_count}] slow_down, interval={interval}s", file=sys.stderr)
            continue
        if err == "access_denied":
            raise RuntimeError("authorization rejected by user")
        if err == "expired_token":
            raise RuntimeError("device authorization expired")
        print(f"[poll #{poll_count}] unknown error={err}", file=sys.stderr)


def exchange_code_for_token(
    mcp_base_url: str,
    client_id: str,
    auth_code: str,
    ssl_context: Optional[ssl.SSLContext],
) -> Dict[str, Any]:
    url = mcp_base_url + MCP_OAUTH_TOKEN_PATH
    payload = {
        "clientId": client_id,
        "authCode": auth_code,
        "grantType": "authorization_code",
    }
    body = http_request(
        "POST",
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        ssl_context=ssl_context,
    )
    resp = json.loads(body)
    if resp.get("errorCode") or resp.get("errorMsg"):
        raise RuntimeError(f"token exchange failed: {resp.get('errorCode')} {resp.get('errorMsg')}")
    if not resp.get("accessToken"):
        raise RuntimeError(f"token response missing accessToken: {resp}")

    now = now_utc()
    expires_in = int(resp.get("expiresIn", 7200) or 7200)
    token_data = {
        "access_token": resp.get("accessToken", ""),
        "refresh_token": resp.get("refreshToken", ""),
        "persistent_code": resp.get("persistentCode", ""),
        "expires_at": to_iso(now + timedelta(seconds=expires_in)),
        "refresh_expires_at": to_iso(now + timedelta(days=30)),
        "corp_id": resp.get("corpId", ""),
        "user_id": "",
        "user_name": "",
        "corp_name": "",
        "client_id": client_id,
        "source": "mcp",
    }
    return token_data


def save_pending(config_dir: Path, pending: PendingDeviceFlow) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "device_flow_pending.json"
    path.write_text(
        json.dumps(
            {
                "clientId": pending.client_id,
                "auth": pending.auth,
                "createdAt": pending.created_at,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_pending(config_dir: Path) -> PendingDeviceFlow:
    path = config_dir / "device_flow_pending.json"
    if not path.exists():
        raise RuntimeError("pending session not found, run --step init first")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PendingDeviceFlow(
        client_id=payload.get("clientId", ""),
        auth=payload.get("auth", {}),
        created_at=payload.get("createdAt", ""),
    )


def clear_pending(config_dir: Path) -> None:
    path = config_dir / "device_flow_pending.json"
    if path.exists():
        path.unlink()


def print_device_hint(auth: Dict[str, Any]) -> None:
    print("Open this URL in browser and authorize:")
    print(f"  verificationUri: {auth.get('verificationUri', '')}")
    print(f"  userCode:        {auth.get('userCode', '')}")
    if auth.get("verificationUriComplete"):
        print(f"  directLink:      {auth['verificationUriComplete']}")
    print(f"  expiresIn:       {auth.get('expiresIn')}")
    print(f"  interval:        {auth.get('interval')}")
    if auth.get("flowId"):
        print(f"  flowId:          {auth.get('flowId')}")


def run(args: argparse.Namespace) -> int:
    config_dir = get_config_dir()
    mcp_base_url = get_mcp_base_url(config_dir)
    device_base_url = args.device_base_url.rstrip("/")
    ssl_context = build_ssl_context(args.insecure, args.cafile)
    if args.step in ("init", "full"):
        client_id = fetch_client_id(mcp_base_url, ssl_context)
        auth = request_device_code(device_base_url, client_id, args.scope, ssl_context)
        print_device_hint(auth)
        if args.step == "init":
            save_pending(
                config_dir,
                PendingDeviceFlow(client_id=client_id, auth=auth, created_at=to_iso(now_utc())),
            )
            print("\nPending session saved. Run again with --step wait after authorization.")
            return 0
    else:
        pending = load_pending(config_dir)
        client_id = pending.client_id
        auth = pending.auth
        if not client_id:
            raise RuntimeError("pending session missing clientId")

    auth_code = wait_for_authorization(mcp_base_url, device_base_url, client_id, auth, ssl_context)
    token_data = exchange_code_for_token(mcp_base_url, client_id, auth_code, ssl_context)

    print(json.dumps(token_data, ensure_ascii=False, indent=2))
    clear_pending(config_dir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Reproduce `dws auth login --device` and print token fields.")
    p.add_argument("--step", choices=["full", "init", "wait"], default="full", help="Device flow step")
    p.add_argument("--scope", default=DEFAULT_SCOPES, help="OAuth scope")
    p.add_argument("--device-base-url", default=DEFAULT_DEVICE_BASE_URL, help="Device flow base URL")
    p.add_argument("--cafile", default="", help="Path to custom CA bundle PEM")
    p.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification (unsafe)")
    return p


def build_ssl_context(insecure: bool, cafile: str) -> Optional[ssl.SSLContext]:
    if insecure:
        return ssl._create_unverified_context()
    if cafile.strip():
        return ssl.create_default_context(cafile=cafile.strip())
    return None


if __name__ == "__main__":
    parser = build_parser()
    ns = parser.parse_args()
    try:
        raise SystemExit(run(ns))
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)

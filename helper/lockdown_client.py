"""Minimal synchronous HTTP client for lockdown's user-owned Unix socket."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(os.environ.get("ATLAS_LOCKDOWN_SOCKET", Path(runtime) / "atlas-lockdown.sock"))


def request(method: str, path: str, body: dict | None = None,
            timeout: float = 5.0) -> tuple[dict, bool]:
    payload = json.dumps(body).encode() if body is not None else b""
    headers = [
        f"{method} {path} HTTP/1.1",
        "Host: localhost",
        "Connection: close",
        f"Content-Length: {len(payload)}",
    ]
    if payload:
        headers.append("Content-Type: application/json")
    wire = ("\r\n".join(headers) + "\r\n\r\n").encode() + payload
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(socket_path()))
            client.sendall(wire)
            chunks = []
            while chunk := client.recv(65536):
                chunks.append(chunk)
        head, separator, raw_body = b"".join(chunks).partition(b"\r\n\r\n")
        if not separator:
            raise ValueError("Malformed response from lockdown daemon")
        status = int(head.split(b" ", 2)[1])
        data = json.loads(raw_body or b"{}")
        return data, status < 200 or status >= 300 or data.get("ok") is False
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {"error": f"Lockdown daemon unavailable: {error}"}, True

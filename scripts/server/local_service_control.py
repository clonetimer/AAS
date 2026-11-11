"""Local-only control gate for the Windows service shortcuts.

The shutdown endpoint is disabled unless Start-AAS.ps1 has written a service
record for the currently running process. The token is random per launch and
is accepted only from loopback clients for the matching PID and port.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
from pathlib import Path


SERVICE_RECORD_NAME = ".aas-service.json"


def _loopback(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(address).strip())
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _host_is_local(host_header: str) -> bool:
    value = str(host_header or "").split(",", 1)[0].strip().lower()
    if value == "::1":
        return True
    if value.startswith("["):
        value = value[1:].split("]", 1)[0]
    elif ":" in value:
        value = value.rsplit(":", 1)[0]
    return value in {"127.0.0.1", "localhost", "::1"}


def authorized_shutdown_request(
    root: Path,
    process_id: int,
    port: int,
    client_address: str,
    host_header: str,
    supplied_token: str,
) -> bool:
    """Return true only for a valid loopback shutdown request for this process."""
    if not _loopback(client_address) or not _host_is_local(host_header):
        return False
    if not isinstance(supplied_token, str) or len(supplied_token) < 32:
        return False
    record_path = Path(root).resolve() / SERVICE_RECORD_NAME
    try:
        if record_path.is_symlink() or not record_path.is_file():
            return False
        record = json.loads(record_path.read_text(encoding="utf-8-sig"))
        expected_pid = int(record.get("pid", 0))
        expected_port = int(record.get("port", 0))
        expected_token = record.get("token", "")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if expected_pid != int(process_id) or expected_port != int(port):
        return False
    if not isinstance(expected_token, str) or len(expected_token) < 32:
        return False
    return hmac.compare_digest(expected_token.encode("utf-8"), supplied_token.encode("utf-8"))

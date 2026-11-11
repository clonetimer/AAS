import json

from scripts.server.local_service_control import authorized_shutdown_request


def _record(root, *, pid, port, token):
    (root / ".aas-service.json").write_text(
        json.dumps({"pid": pid, "port": port, "token": token}),
        encoding="utf-8",
    )


def test_shutdown_requires_matching_local_process_and_token(tmp_path):
    token = "a" * 64
    _record(tmp_path, pid=1234, port=8765, token=token)

    assert authorized_shutdown_request(
        tmp_path, 1234, 8765, "127.0.0.1", "127.0.0.1:8765", token
    )


def test_shutdown_rejects_remote_client_and_nonlocal_host(tmp_path):
    token = "b" * 64
    _record(tmp_path, pid=1234, port=8765, token=token)

    assert not authorized_shutdown_request(
        tmp_path, 1234, 8765, "192.0.2.10", "127.0.0.1:8765", token
    )
    assert not authorized_shutdown_request(
        tmp_path, 1234, 8765, "127.0.0.1", "example.com:8765", token
    )


def test_shutdown_rejects_stale_pid_port_and_wrong_token(tmp_path):
    token = "c" * 64
    _record(tmp_path, pid=1234, port=8765, token=token)

    assert not authorized_shutdown_request(
        tmp_path, 9999, 8765, "127.0.0.1", "127.0.0.1:8765", token
    )
    assert not authorized_shutdown_request(
        tmp_path, 1234, 9999, "127.0.0.1", "127.0.0.1:8765", token
    )
    assert not authorized_shutdown_request(
        tmp_path, 1234, 8765, "127.0.0.1", "127.0.0.1:8765", "wrong"
    )


def test_shutdown_rejects_malformed_or_symlinked_record(tmp_path):
    path = tmp_path / ".aas-service.json"
    path.write_text("{not-json", encoding="utf-8")
    assert not authorized_shutdown_request(
        tmp_path, 1234, 8765, "127.0.0.1", "127.0.0.1:8765", "d" * 64
    )


def test_shutdown_accepts_powershell_utf8_bom_record_and_ipv6_loopback(tmp_path):
    token = "e" * 64
    payload = json.dumps({"pid": 1234, "port": 8765, "token": token}).encode("utf-8")
    (tmp_path / ".aas-service.json").write_bytes(b"\xef\xbb\xbf" + payload)
    assert authorized_shutdown_request(
        tmp_path, 1234, 8765, "::1", "[::1]:8765", token
    )

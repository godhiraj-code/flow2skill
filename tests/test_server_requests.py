from __future__ import annotations

import http.client
import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler

import pytest

import flow2skill.server as studio


@pytest.fixture
def local_server(tmp_path, monkeypatch):
    ready = threading.Event()
    servers = []
    original_server = studio.LocalHTTPServer

    def create_server(*args, **kwargs):
        server = original_server(*args, **kwargs)
        servers.append(server)
        ready.set()
        return server

    monkeypatch.setattr(studio, "LocalHTTPServer", create_server)
    thread = threading.Thread(
        target=studio.serve,
        kwargs={"port": 0, "workspace_root": tmp_path, "open_browser": False},
        daemon=True,
    )
    thread.start()
    assert ready.wait(5), "Studio failed to start"
    server = servers[0]
    try:
        yield server.server_port
    finally:
        server.shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()


def request(port, path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


def test_local_server_startup_does_not_resolve_reverse_dns(monkeypatch):
    def unavailable_dns(*args):
        raise AssertionError("Loopback startup must not depend on reverse DNS")

    monkeypatch.setattr(socket, "getfqdn", unavailable_dns)
    with studio.LocalHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
        assert server.server_name == "127.0.0.1"
        assert server.server_port > 0


def test_malformed_auth_headers_return_forbidden_and_server_stays_healthy(local_server):
    status, index = request(local_server, "/")
    assert status == 200
    token = re.search(r'const TOKEN = "([a-f0-9]+)";', index).group(1)
    cases = [
        {"X-Flow2Skill-Token": "\xe9"},
        {"X-Flow2Skill-Token": token, "Origin": "http://[malformed"},
    ]
    for headers in cases:
        status, body = request(local_server, "/api/health", headers)
        assert status == 403
        assert json.loads(body)["error"] == "Unauthorized local request"
    status, body = request(local_server, "/api/health", {"X-Flow2Skill-Token": token})
    assert status == 200
    assert json.loads(body)["ok"] is True

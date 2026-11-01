from app.core.logging_config import redact_processor
from app.main import create_app
from tests.conftest import make_settings


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["x-request-id"]


def test_ready_reports_not_configured_without_database(client):
    r = client.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "not_configured"


def test_unknown_route_uses_error_envelope(client):
    r = client.get("/nope")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found"
    assert err["request_id"] == r.headers["x-request-id"]


def test_client_supplied_request_id_is_echoed_when_valid(client):
    r = client.get("/health", headers={"X-Request-ID": "abc12345-req"})
    assert r.headers["x-request-id"] == "abc12345-req"


def test_malicious_request_id_is_replaced(client):
    r = client.get("/health", headers={"X-Request-ID": "bad id\r\nX-Evil: 1"})
    assert r.headers["x-request-id"] != "bad id"
    assert "x-evil" not in r.headers


def test_oversized_body_rejected_by_content_length():
    app = create_app(make_settings(max_request_bytes=2048))
    from fastapi.testclient import TestClient

    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/health", content=b"x" * 5000)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_oversized_streamed_body_rejected():
    from fastapi import Request
    from fastapi.testclient import TestClient

    app = create_app(make_settings(max_request_bytes=2048))

    @app.post("/_test/echo")
    async def echo(request: Request):
        return {"bytes": len(await request.body())}

    def chunks():
        for _ in range(10):
            yield b"x" * 1000

    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/_test/echo", content=chunks())  # chunked: no Content-Length header
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"
    ok = c.post("/_test/echo", content=b"x" * 500)
    assert ok.status_code == 200 and ok.json() == {"bytes": 500}


def test_cors_allows_configured_origin_only(client):
    ok = client.options(
        "/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options(
        "/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers


def test_unhandled_exception_hides_details(app):
    from fastapi.testclient import TestClient

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret-internal-detail")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "secret-internal-detail" not in r.text


def test_log_redaction():
    out = redact_processor(
        None,
        "info",
        {
            "event": "x",
            "authorization": "Bearer abc",
            "gemini_api_key": "k",
            "nested": {"password": "p", "ok": 1},
            "safe": "value",
        },
    )
    assert out["authorization"] == "***"
    assert out["gemini_api_key"] == "***"
    assert out["nested"] == {"password": "***", "ok": 1}
    assert out["safe"] == "value"

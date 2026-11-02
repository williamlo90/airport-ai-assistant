"""BE-02 wire behavior, input limits, isolation and error redaction."""

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request
from httpx import ASGITransport, AsyncClient

from airport_assistant.api.schemas import RequestModel
from airport_assistant.api.boundary import RequestBoundary
from airport_assistant.config import Settings
from airport_assistant.main import create_app


class Payload(RequestModel):
    message: str
    count: int


def app_with_routes(limit=1_048_576):
    app = create_app(Settings(env="test", max_request_body_bytes=limit, _env_file=None))

    @app.post("/api/v1/test")
    async def accept(payload: Payload, request: Request):
        return {"message": payload.message, "request_id": request.state.request_id}

    @app.get("/api/v1/explode")
    async def explode():
        raise RuntimeError("secret-sentinel SQL SELECT password FROM customers")

    @app.get("/api/v1/http-error/{status}")
    async def http_error(status: int):
        raise HTTPException(status, detail="secret-sentinel", headers={"Retry-After": "30", "X-Private": "secret-sentinel"})

    return app


def request(app, method="GET", path="/health/live", **kwargs):
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def assert_error(response, status, code, retryable=False):
    assert response.status_code == status
    error = response.json()["error"]
    assert set(error) == {"code", "message", "request_id", "retryable"}
    assert error["code"] == code
    assert error["retryable"] is retryable
    assert str(UUID(error["request_id"])) == response.headers["x-request-id"]
    assert "secret-sentinel" not in response.text
    assert "secret-sentinel" not in str(response.headers)


@pytest.mark.parametrize("environment", ["test", "production"])
def test_health_is_explicitly_process_only(environment):
    app = create_app(Settings(env=environment, _env_file=None))
    response = request(app)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "scope": "process"}
    UUID(response.headers["x-request-id"])
    assert_error(request(app, path="/health/ready"), 404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize("value", [str(uuid4()).upper(), "secret-sentinel", "x" * 1000, "", "00000000-0000-0000-0000-000000000000"])
def test_request_ids_are_normalized_or_replaced(value):
    response = request(app_with_routes(), headers={"X-Request-ID": value})
    actual = response.headers["x-request-id"]
    assert str(UUID(actual)) == actual
    if len(value) == 36:
        assert actual == value.lower()
    else:
        assert actual != value


def test_duplicate_ids_are_replaced():
    supplied = str(uuid4())
    response = request(app_with_routes(), headers=[("X-Request-ID", supplied), ("X-Request-ID", supplied)])
    assert response.headers["x-request-id"] != supplied


def test_concurrent_request_context_is_separate():
    app = app_with_routes()
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await asyncio.gather(*[
                client.post("/api/v1/test", json={"message": str(i), "count": i}) for i in range(20)
            ])
    responses = asyncio.run(run())
    assert len({r.headers["x-request-id"] for r in responses}) == 20
    assert all(r.json()["request_id"] == r.headers["x-request-id"] for r in responses)


@pytest.mark.parametrize("payload", [
    {"message": "ok", "count": 1, "tenant_id": "secret-sentinel"},
    {"message": "ok", "count": "secret-sentinel"},
    {"message": "secret-sentinel"},
    {"message": "x" * 4097, "count": 1},
    {"message": 123, "count": 1},
])
def test_strict_request_model_rejects_invalid_or_extra_input(payload, caplog):
    response = request(app_with_routes(), "POST", "/api/v1/test", json=payload)
    assert_error(response, 422, "VALIDATION_ERROR")
    assert "secret-sentinel" not in caplog.text


def test_valid_payload_and_string_boundary():
    response = request(app_with_routes(), "POST", "/api/v1/test", json={"message": "x" * 4096, "count": 1})
    assert response.status_code == 200


def test_malformed_json_is_redacted():
    response = request(app_with_routes(), "POST", "/api/v1/test", content=b'{"secret-sentinel":', headers={"Content-Type": "application/json"})
    assert_error(response, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("status,code,retryable", [
    (401, "AUTH_REQUIRED", False), (403, "ACTION_FORBIDDEN", False),
    (404, "RESOURCE_NOT_FOUND", False), (429, "RATE_LIMITED", True),
    (502, "SOURCE_INVALID_RESPONSE", True), (503, "SOURCE_UNAVAILABLE", True),
])
def test_http_exceptions_use_safe_contract(status, code, retryable):
    response = request(app_with_routes(), path=f"/api/v1/http-error/{status}")
    assert_error(response, status, code, retryable)
    assert response.headers["retry-after"] == "30"
    assert "x-private" not in response.headers


def test_unexpected_exception_has_safe_500_and_correlated_log(caplog):
    response = request(app_with_routes(), path="/api/v1/explode?token=secret-sentinel")
    assert_error(response, 500, "INTERNAL_ERROR")
    assert response.headers["x-request-id"] in caplog.text
    assert "secret-sentinel" not in caplog.text
    assert "Traceback" not in response.text + caplog.text


def test_routing_errors_have_envelope_and_preserve_allow():
    app = app_with_routes()
    assert_error(request(app, path="/secret-sentinel"), 404, "RESOURCE_NOT_FOUND")
    response = request(app, "POST")
    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert "GET" in response.headers["allow"]


def test_oversize_content_length_rejected():
    response = request(app_with_routes(limit=32), "POST", "/api/v1/test", content=b"x" * 33)
    assert_error(response, 413, "PAYLOAD_TOO_LARGE")


def raw_request(app, chunks, headers=()):
    sent = []
    incoming = iter([{"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1} for i, chunk in enumerate(chunks)])
    async def receive():
        return next(incoming, {"type": "http.disconnect"})
    async def send(message):
        sent.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/api/v1/test", "raw_path": b"/api/v1/test",
             "query_string": b"", "headers": list(headers), "server": ("test", 80), "client": ("test", 1)}
    asyncio.run(app(scope, receive, send))
    body = b"".join(m.get("body", b"") for m in sent)
    return sent[0], json.loads(body)


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
def test_streaming_or_dishonest_length_cannot_bypass_limit(headers):
    start, body = raw_request(app_with_routes(limit=32), [b"x" * 16, b"x" * 17], headers)
    assert start["status"] == 413
    assert body["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert dict(start["headers"])[b"x-request-id"].decode() == body["error"]["request_id"]


@pytest.mark.parametrize("lengths", [[b"-1"], [b"secret-sentinel"], [b"1", b"1"], [b"9" * 100]])
def test_invalid_length_rejected_safely(lengths):
    start, body = raw_request(app_with_routes(), [b""], [(b"content-length", x) for x in lengths])
    assert start["status"] == 400
    assert body["error"]["code"] == "BAD_REQUEST"
    assert "secret-sentinel" not in json.dumps(body)


def test_exact_byte_limit_accepted_and_mismatched_length_rejected():
    payload = b'{"message":"ok","count":1}'
    response = request(app_with_routes(len(payload)), "POST", "/api/v1/test", content=payload, headers={"Content-Type": "application/json"})
    assert response.status_code == 200
    start, _ = raw_request(app_with_routes(), [b"x"], [(b"content-length", b"2")])
    assert start["status"] == 400


def test_openapi_describes_actual_error_envelope():
    response = request(app_with_routes(), path="/openapi.json")
    schema = response.json()
    model = schema["paths"]["/api/v1/test"]["post"]["responses"]["422"]["content"]["application/json"]["schema"]
    assert model["$ref"].endswith("/ErrorResponse")
    assert "tenant_id" not in schema["components"]["schemas"]["Payload"]["properties"]
    assert schema["components"]["schemas"]["Payload"]["additionalProperties"] is False


def test_failure_after_headers_does_not_send_second_response_or_leak_exception(caplog):
    sent = []
    async def broken_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("secret-sentinel")
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        sent.append(message)
    with pytest.raises(RuntimeError, match="Response interrupted") as error:
        asyncio.run(RequestBoundary(broken_app, 32)({"type": "http", "headers": []}, receive, send))
    assert len(sent) == 1
    assert "secret-sentinel" not in str(error.value) + caplog.text
    assert error.value.__suppress_context__

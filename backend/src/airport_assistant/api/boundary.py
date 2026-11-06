"""Pure ASGI boundary: bounded bodies, correlation IDs and redacted failures."""

import logging
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from airport_assistant.api.errors import error_response

logger = logging.getLogger("airport_assistant.api")


class RequestBoundary:
    def __init__(self, app: ASGIApp, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = [value for name, value in scope.get("headers", []) if name.lower() == b"x-request-id"]
        try:
            request_id = str(UUID(supplied[0].decode("ascii"))) if len(supplied) == 1 and len(supplied[0]) == 36 else str(uuid4())
        except (ValueError, UnicodeError):
            request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        body_limit = 5*1024*1024 if scope.get("path")=="/api/v1/attachments" and scope.get("method")=="POST" else self.max_body_bytes
        if scope.get('path','').startswith('/api/v1/admin/knowledge/'):
            body_limit=min(body_limit,131072)
        if scope.get('path','').startswith('/integrations/email/'):
            body_limit=min(body_limit,65536)
        started = False
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                headers = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"x-request-id"]
                if scope.get('path', '').startswith(('/api/v1/staff/','/api/v1/admin/knowledge/')):
                    headers = [(key, value) for key, value in headers if key.lower() != b'cache-control']
                    headers.append((b'cache-control', b'no-store'))
                message = {**message, "headers": headers + [(b"x-request-id", request_id.encode("ascii"))]}
            await send(message)

        try:
            lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
            if lengths:
                if len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 20:
                    await error_response(400, request_id)(scope, receive, send_with_id)
                    return
                if int(lengths[0]) > body_limit:
                    await error_response(413, request_id)(scope, receive, send_with_id)
                    return

            # Count actual bytes too: Content-Length may be absent or dishonest.
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > body_limit:
                    await error_response(413, request_id)(scope, receive, send_with_id)
                    return
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            if lengths and int(lengths[0]) != len(body):
                await error_response(400, request_id)(scope, receive, send_with_id)
                return

            consumed = False

            async def replay() -> Message:
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, replay, send_with_id)
        except Exception:
            # No exception text, raw URL, body, credentials or traceback in general logs.
            logger.error("request_failed request_id=%s response_started=%s", request_id, started)
            if started:
                # The response cannot be replaced after headers were sent. Abort
                # with a safe exception so server logs cannot print the original.
                raise RuntimeError(f"Response interrupted; request_id={request_id}") from None
            await error_response(500, request_id)(scope, receive, send_with_id)
        finally:
            logger.info("request_finished request_id=%s status=%s", request_id, status)

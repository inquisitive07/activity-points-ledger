"""Request body size limit (pure ASGI middleware).

FastAPI parses a multipart body (spooling it to a temp file) before any route code or dependency runs, so the
10 MB file limit checked in the document service would come too late to stop a huge upload. This guard refuses a
body that is too large as it arrives: up front from Content-Length, and while streaming for chunked bodies. The limit
is the upload limit plus room for multipart headers; the exact file-size check stays in the service.
"""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.config import settings

MULTIPART_OVERHEAD = 64 * 1024


class BodyTooLarge(HTTPException):
    """An HTTPException so FastAPI's body parser re-raises it as is (it turns other errors into a 400)."""
    def __init__(self):
        super().__init__(413, f"Request body exceeds {settings.max_upload_bytes} bytes")


def body_limit() -> int:
    return settings.max_upload_bytes + MULTIPART_OVERHEAD


class BodySizeLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = body_limit()
        too_large = JSONResponse({"detail": BodyTooLarge().detail}, status_code=413)
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            return await too_large(scope, receive, send)

        received, started = 0, False

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if started:
                raise
            await too_large(scope, receive, send)

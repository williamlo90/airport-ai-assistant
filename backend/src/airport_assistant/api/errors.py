"""Public error messages come from server constants, never exception details."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from airport_assistant.api.schemas import ErrorDetail, ErrorResponse


ERRORS = {
    400: ("BAD_REQUEST", "Permintaan tidak valid.", False),
    401: ("AUTH_REQUIRED", "Verifikasi diperlukan.", False),
    403: ("ACTION_FORBIDDEN", "Tindakan tidak diizinkan.", False),
    404: ("RESOURCE_NOT_FOUND", "Data atau halaman tidak ditemukan.", False),
    405: ("METHOD_NOT_ALLOWED", "Metode tidak diizinkan.", False),
    409: ("VERSION_CONFLICT", "Data berubah. Periksa kembali sebelum melanjutkan.", False),
    410: ("DRAFT_EXPIRED", "Draf sudah kedaluwarsa.", False),
    413: ("PAYLOAD_TOO_LARGE", "Ukuran permintaan melebihi batas.", False),
    415: ("UNSUPPORTED_MEDIA_TYPE", "Format permintaan tidak didukung.", False),
    422: ("VALIDATION_ERROR", "Periksa format dan kelengkapan data permintaan.", False),
    429: ("RATE_LIMITED", "Terlalu banyak permintaan. Coba lagi nanti.", True),
    500: ("INTERNAL_ERROR", "Permintaan belum dapat diproses.", False),
    502: ("SOURCE_INVALID_RESPONSE", "Respons sumber belum dapat digunakan.", True),
    503: ("SOURCE_UNAVAILABLE", "Informasi belum dapat diperiksa.", True),
}


def error_response(status: int, request_id: str, headers: dict | None = None) -> JSONResponse:
    code, message, retryable = ERRORS.get(status, ("HTTP_ERROR", "Permintaan tidak dapat diproses.", False))
    body = ErrorResponse(error=ErrorDetail(
        code=code, message=message, request_id=request_id, retryable=retryable,
    ))
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        # Protocol headers only; never echo arbitrary exception headers/details.
        headers = {key: value for key, value in (exc.headers or {}).items()
                   if key.lower() in {"allow", "retry-after", "www-authenticate"}}
        return error_response(exc.status_code, request.state.request_id, headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic errors can contain the submitted value and user-chosen keys.
        return error_response(422, request.state.request_id)

"""Public error messages come from server constants, never exception details."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from airport_assistant.api.schemas import ErrorDetail, ErrorResponse
from airport_assistant.modules.identity.auth import AuthError
from airport_assistant.modules.source.models import SourceFailure
from airport_assistant.modules.conversations.service import ConversationError
from airport_assistant.modules.drafts.models import DraftError

AUTH_ERRORS={
    "STAFF_LOGIN_FAILED":("Login staf tidak berhasil. Periksa kredensial atau hubungi administrator.",False),
    "CSRF_REQUIRED":("Permintaan harus berasal dari browser dan sesi yang sah.",False),
    "VERIFICATION_FAILED":("Verifikasi tidak dapat diselesaikan. Periksa kode atau minta kode baru.",False),
    "VERIFICATION_UNAVAILABLE":("Pengiriman verifikasi belum tersedia. Coba lagi nanti.",True),
    "AUTH_UNAVAILABLE":("Layanan autentikasi belum tersedia.",True),
    "CONVERSATION_UNAVAILABLE":("Percakapan belum dapat diproses.",True),
    "MESSAGE_CONFLICT":("ID pesan sudah dipakai untuk isi yang berbeda.",False),
    "DRAFT_UNAVAILABLE":("Draf belum dapat diproses.",True),
    "DRAFT_DESTINATION_UNAVAILABLE":("Tujuan layanan belum tersedia. Coba lagi nanti.",True),
    "DRAFT_NOT_PENDING":("Draf ini sudah tidak dapat diubah atau dikonfirmasi.",False),
    "CASE_UNAVAILABLE":("Pengajuan belum dapat diproses.",True),
    "CASE_CONTACT_REQUIRED":("Isi email terverifikasi untuk tindak lanjut pengajuan.",False),
    "CASE_CONTACT_VERIFICATION_REQUIRED":("Email pengajuan harus merupakan kontak Anda yang sudah terverifikasi.",False),
    "CASE_DESTINATION_CHANGED":("Tujuan layanan berubah. Perbarui dan tinjau kembali draf.",False),
    "IDEMPOTENCY_KEY_REQUIRED":("Kunci retry permintaan wajib tersedia dan valid.",False),
    "IDEMPOTENCY_CONFLICT":("Kunci retry sudah digunakan untuk permintaan berbeda.",False),
}


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


def error_response(status: int, request_id: str, headers: dict | None = None, *, auth_code:str|None=None,result_ref:str|None=None) -> JSONResponse:
    code, message, retryable = ERRORS.get(status, ("HTTP_ERROR", "Permintaan tidak dapat diproses.", False))
    if auth_code in AUTH_ERRORS:
        code=auth_code;message,retryable=AUTH_ERRORS[auth_code]
    body = ErrorResponse(error=ErrorDetail(
        code=code, message=message, request_id=request_id, retryable=retryable,result_ref=result_ref,
    ))
    return JSONResponse(status_code=status, content=body.model_dump(exclude_none=True), headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DraftError)
    async def draft_error(request:Request,exc:DraftError):
        return error_response(exc.status,request.state.request_id,{'Cache-Control':'no-store'},auth_code=exc.code,result_ref=exc.result_ref)

    @app.exception_handler(ConversationError)
    async def conversation_error(request:Request,exc:ConversationError):
        return error_response(exc.status,request.state.request_id,{'Cache-Control':'no-store'},auth_code=exc.code)

    @app.exception_handler(SourceFailure)
    async def source_error(request:Request,exc:SourceFailure):
        return error_response(exc.status,request.state.request_id,{'Cache-Control':'no-store'})

    @app.exception_handler(AuthError)
    async def auth_error(request:Request,exc:AuthError):
        headers={"Cache-Control":"no-store"}
        if exc.status==429:headers["Retry-After"]=str(exc.retry_after or 900)
        return error_response(exc.status,request.state.request_id,headers,auth_code=exc.code)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        # Protocol headers only; never echo arbitrary exception headers/details.
        headers = {key: value for key, value in (exc.headers or {}).items()
                   if key.lower() in {"allow", "retry-after", "www-authenticate"}}
        return error_response(exc.status_code, request.state.request_id, headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic errors can contain the submitted value and user-chosen keys.
        return error_response(422, request.state.request_id, {"Cache-Control":"no-store"} if request.url.path.startswith("/api/v1/") else None)

"""Shared wire schemas; mutation payloads inherit the strict input contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_max_length=4096)


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    retryable: bool
    result_ref: str|None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ProcessHealth(BaseModel):
    status: Literal["ok"] = "ok"
    scope: Literal["process"] = "process"

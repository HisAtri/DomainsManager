from enum import StrEnum

from pydantic import BaseModel, Field

from modules.models.domain import DomainInfo


class WhoisResponseStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    ACCESS_DENIED = "access_denied"
    INVALID_QUERY = "invalid_query"
    TEMPORARY_FAILURE = "temporary_failure"
    UNKNOWN = "unknown"


class WhoisParseResult(BaseModel):
    status: WhoisResponseStatus
    info: DomainInfo | None = None
    warnings: list[str] = Field(default_factory=list)
    parser_key: str
    parser_version: str

"""Canonical public contracts. SDK objects and raw exceptions never cross this boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorCode(StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    MODEL_NOT_CONFIGURED = "MODEL_NOT_CONFIGURED"
    MODEL_NOT_READY = "MODEL_NOT_READY"
    MODEL_AUTH_FAILED = "MODEL_AUTH_FAILED"
    MODEL_ACCESS_DENIED = "MODEL_ACCESS_DENIED"
    MODEL_DEPLOYMENT_NOT_FOUND = "MODEL_DEPLOYMENT_NOT_FOUND"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    DATA_NOT_READY = "DATA_NOT_READY"
    CITATION_INVALID = "CITATION_INVALID"
    RUN_TIMEOUT = "RUN_TIMEOUT"
    RUN_CANCELLED = "RUN_CANCELLED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(Contract):
    code: ErrorCode
    message: str
    details: dict[str, JsonValue] | None = None


class ErrorResponse(Contract):
    error: ErrorDetail


class AppError(Exception):
    """Only explicitly authored, non-sensitive messages belong here."""

    def __init__(self, code: ErrorCode, message: str, status: int = 500):
        super().__init__(message)
        self.error = ErrorDetail(code=code, message=message)
        self.status = status


OpaqueId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{32,64}$")]
SourceId = Literal["aeris_manual", "northgate_standards"]
ToolName = Literal[
    "search_documents", "list_assets", "get_asset_status", "query_telemetry",
    "list_work_orders", "get_work_order", "analyze_correlation",
]
ALLOWED_TOOLS = frozenset(ToolName.__args__)
DOCUMENT_FILES: dict[str, str] = {
    "aeris_manual": "Aeris-3.2MW-Turbine-Maintenance-Manual-Excerpt.pdf",
    "northgate_standards": "Northgate-Asset-Performance-and-Dispatch-Standards.pdf",
}


class ChatRequest(Contract):
    session_id: OpaqueId
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class SessionResponse(Contract):
    session_id: OpaqueId
    reference_date: date | None = None
    data_labels: list[str] = Field(default_factory=lambda: ["local", "read-only", "synthetic"])


class Citation(Contract):
    citation_id: Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[\w.:-]+$")]
    source_id: SourceId
    page: Annotated[int, Field(ge=1)]
    title: Annotated[str, Field(max_length=300)] = ""
    section: Annotated[str | None, Field(max_length=300)] = None
    excerpt: Annotated[str, Field(min_length=1, max_length=2400)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    version: Annotated[str | None, Field(max_length=100)] = None


class ChartPayload(Contract):
    chart_id: OpaqueId
    title: Annotated[str, Field(max_length=300)] = "受控计算（synthetic）"
    source: Literal["synthetic"] = "synthetic"


class StartedPayload(Contract):
    status: Literal["generating"] = "generating"


class TextPayload(Contract):
    text: str
    provisional: Literal[True] = True


class ToolPayload(Contract):
    tool_call_id: str
    tool_name: ToolName
    summary: str
    duration_ms: int | None = None
    error_code: str | None = None
    # No arbitrary args/results: not even partial streamed model arguments are public.
    parameters_redacted: Literal[True] = True


class RetryPayload(Contract):
    tool_call_id: str
    attempt: Annotated[int, Field(ge=1, le=3)]
    reason: Literal["range_too_large", "invalid_arguments"]


class CompletedPayload(Contract):
    answer: str
    citation_ids: list[str]
    model_verified: bool


class FailedPayload(Contract):
    error: ErrorDetail


class RunStarted(Contract):
    type: Literal["run_started"] = "run_started"
    payload: StartedPayload = Field(default_factory=StartedPayload)


class TextDelta(Contract):
    type: Literal["text_delta"] = "text_delta"
    payload: TextPayload


class ToolEvent(Contract):
    type: Literal["tool_started", "tool_completed", "tool_error"]
    payload: ToolPayload


class RetryEvent(Contract):
    type: Literal["retry"] = "retry"
    payload: RetryPayload


class CitationEvent(Contract):
    type: Literal["citation"] = "citation"
    payload: Citation


class ChartEvent(Contract):
    type: Literal["chart"] = "chart"
    payload: ChartPayload


class RunCompleted(Contract):
    type: Literal["run_completed"] = "run_completed"
    payload: CompletedPayload


class RunError(Contract):
    type: Literal["run_error"] = "run_error"
    payload: FailedPayload


PublicEvent = Annotated[
    RunStarted | TextDelta | ToolEvent | RetryEvent | CitationEvent | ChartEvent
    | RunCompleted | RunError, Field(discriminator="type"),
]


class SSEEvent(Contract):
    run_id: OpaqueId
    seq: Annotated[int, Field(ge=1)]
    type: str
    time: datetime = Field(default_factory=utc_now)
    payload: dict[str, JsonValue]

    @classmethod
    def wrap(cls, run_id: str, seq: int, event: PublicEvent) -> SSEEvent:
        return cls(run_id=run_id, seq=seq, type=event.type,
                   payload=event.payload.model_dump(mode="json"))

    def encode(self) -> str:
        return f"id: {self.run_id}:{self.seq}\nevent: {self.type}\ndata: {self.model_dump_json()}\n\n"


class HealthResponse(Contract):
    status: Literal["healthy"] = "healthy"
    reference_date: date | None = None
    model_configured: bool
    model_verified: bool = False
    model_verified_at: datetime | None = None
    model_status: Literal["not_configured", "not_ready", "unverified", "verified"]
    data_sources_ready: bool = False
    runtime_validated: bool = False
    configuration_issues: list[str] = Field(default_factory=list)


class KnowledgePort(Protocol):
    ready: bool
    documents: dict[str, object]

    def search_documents(self, query: str, limit: int = 5) -> object: ...


class ToolPort(Protocol):
    def list_assets(self) -> object: ...
    def get_asset_status(self, asset_id: str) -> object: ...
    def query_telemetry(self, asset_id: str, metric: str,
                        from_date: str, to_date: str) -> object: ...
    def list_work_orders(self, asset_id: str | None = None) -> object: ...
    def get_work_order(self, work_order_id: str) -> object: ...
    def analyze_correlation(self, asset_id: str, from_date: str, to_date: str) -> object: ...


class ModelGateway(Protocol):
    """Application seam; prepare must not acquire tokens or contact a model."""

    verified: bool
    verified_at: datetime | None

    def prepare(self) -> None: ...
    def stream(self, message: str, session: object) -> AsyncIterator[PublicEvent]: ...
    async def close(self) -> None: ...


def document_paths(root: Path) -> dict[str, Path]:
    return {key: root / name for key, name in DOCUMENT_FILES.items()}
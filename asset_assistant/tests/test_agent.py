"""Explicit OFFLINE FunctionModel tests of the real SDK -> AzureModelGateway bridge.

No Azure client, credentials, network model or production fallback is used. The
unrelated work-order file is bypassed at construction; only original PDFs and
connector telemetry are read. No business data files are created or changed.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic_ai.messages import RetryPromptPart, ToolReturnPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from asset_assistant.agent import AzureModelGateway, public_failure
from asset_assistant.config import Settings
from asset_assistant.contracts import AppError, ErrorCode
from asset_assistant.knowledge import KnowledgeService
from asset_assistant.sessions import SessionStore
from asset_assistant.tools.service import ToolService, merge_points, split_windows


def offline_tools(root):
    """Real telemetry/analysis service, independent of replacement work orders."""
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if path.name == "replacement_work_orders.json":
            return "[]"  # Never read, expand or use unrelated work-order data.
        return original(path, *args, **kwargs)

    with patch.object(Path, "read_text", read_text):
        return ToolService(root)


def tool_delta(name, arguments, call_id):
    return {0: DeltaToolCall(name, json.dumps(arguments), tool_call_id=call_id)}


def returned(messages):
    return [part for message in messages for part in message.parts
            if isinstance(part, ToolReturnPart)]


def citation_answer(messages):
    citations = next(part.content for part in returned(messages)
                     if part.tool_name == "search_documents")
    assert citations, "The real PDF query must retrieve evidence."
    return f"离线 SDK 桥接测试；synthetic 数据，非真实模型验收。[{citations[0]['citation_id']}]"


@pytest.fixture
def gateway():
    settings = Settings()
    store = SessionStore(settings)
    return AzureModelGateway(settings, store, KnowledgeService(settings.root), offline_tools(settings.root))


async def collect(gateway, stream_function, session=None):
    gateway._model = FunctionModel(stream_function=stream_function, model_name="explicit-offline-test")
    session = session or gateway.store.create()
    events = []
    # Disables real providers even if the developer's environment is configured.
    with override_allow_model_requests(False):
        async for event in gateway.stream("离线能源调查", session):
            events.append(event)
    return events, session


async def test_real_pdf_citation_and_final_sdk_stream(gateway):
    async def model(messages, info):
        assert {tool.name for tool in info.function_tools} >= {"search_documents", "query_telemetry"}
        if not returned(messages):
            yield tool_delta("search_documents", {"query": "vibration measurement validity", "limit": 2}, "pdf")
        else:
            answer = citation_answer(messages)
            yield answer[:10]
            yield answer[10:]

    events, session = await collect(gateway, model)
    citations = [event.payload for event in events if event.type == "citation"]
    assert len(citations) == 2
    for citation in citations:
        assert citation.model_dump(mode="json") in gateway.knowledge.pages
        assert session.citations[citation.citation_id] == citation
    assert [(event.type, event.payload.tool_call_id) for event in events
            if event.type.startswith("tool_")] == [("tool_started", "pdf"), ("tool_completed", "pdf")]
    completed = events[-1]
    assert completed.type == "run_completed"
    assert completed.payload.citation_ids == [citations[0].citation_id]
    assert "".join(event.payload.text for event in events if event.type == "text_delta") == completed.payload.answer
    assert session.history == [("离线能源调查", completed.payload.answer)]
    assert gateway._client is None and gateway._credential is None


async def test_tool_phase_then_timeout_does_not_verify_model_or_save_answer(gateway):
    closed = asyncio.Event()
    reached = asyncio.Event()

    async def model(messages, info):
        if not returned(messages):
            yield tool_delta("search_documents", {"query": "vibration", "limit": 3}, "pdf")
        else:
            try:
                reached.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    gateway._model = FunctionModel(stream_function=model, model_name="explicit-offline-test")
    session = gateway.store.create()
    events = []

    async def consume():
        async for event in gateway.stream("离线超时测试", session):
            events.append(event)

    with override_allow_model_requests(False), pytest.raises(TimeoutError):
        await asyncio.wait_for(consume(), timeout=1)
    assert reached.is_set() and closed.is_set()
    assert len([event for event in events if event.type == "citation"]) == 3
    assert any(event.type == "tool_completed" for event in events)
    assert not any(event.type in {"text_delta", "run_completed"} for event in events)
    assert not gateway.verified and gateway.verified_at is None and not session.history


async def test_oversized_telemetry_sdk_error_then_adjacent_recovery(gateway):
    windows = split_windows("2026-02-14", "2026-08-14")
    actual_results = []

    async def model(messages, info):
        parts = returned(messages)
        if not parts:
            yield tool_delta("query_telemetry", dict(asset_id="WTG-114", metric="power_output_kw",
                             from_date="2026-02-14", to_date="2026-08-14"), "too-wide")
        elif len(parts) <= len(windows):
            assert parts[0].content["error"]["code"] == "range_too_large"
            start, end = windows[len(parts) - 1]
            yield tool_delta("query_telemetry", dict(asset_id="WTG-114", metric="power_output_kw",
                             from_date=start, to_date=end), f"window-{len(parts)}")
        elif parts[-1].tool_name != "search_documents":
            actual_results.extend(part.content for part in parts[1:])
            yield tool_delta("search_documents", {"query": "measurement validity", "limit": 1}, "pdf")
        else:
            points = merge_points(actual_results)
            assert len(points) == 90
            assert points[0]["ts"] == "2026-05-17" and points[-1]["ts"] == "2026-08-14"
            assert any(result["partial_coverage"] for result in actual_results)
            yield "请求六个月，但仅有原始数据实际覆盖；不补造历史。" + citation_answer(messages)

    events, _ = await collect(gateway, model)
    errors = [event.payload for event in events if event.type == "tool_error"]
    retries = [event.payload for event in events if event.type == "retry"]
    assert len(errors) == len(retries) == 1
    assert errors[0].tool_call_id == "too-wide" and errors[0].error_code == "range_too_large"
    assert retries[0].tool_call_id == "window-1"
    assert retries[0].reason == "range_too_large" and retries[0].attempt == 1
    completed_calls = [event.payload.tool_call_id for event in events if event.type == "tool_completed"]
    assert completed_calls == [*(f"window-{i + 1}" for i in range(len(windows))), "pdf"]
    assert events[-1].type == "run_completed"


async def test_actual_analysis_sdk_charts_and_session_bytes(gateway):
    analysis = None

    async def model(messages, info):
        nonlocal analysis
        parts = returned(messages)
        if not parts:
            yield tool_delta("analyze_correlation", dict(asset_id="WTG-114", from_date="2026-06-16",
                                                       to_date="2026-08-14"), "analysis")
        elif len(parts) == 1:
            analysis = parts[0].content
            assert analysis["counts"]["valid_n"] == 60
            assert "charts" not in analysis  # PNG must not enter the model context.
            yield tool_delta("search_documents", {"query": "measurement validity", "limit": 1}, "pdf")
        else:
            yield citation_answer(messages)

    events, session = await collect(gateway, model)
    charts = [event.payload.chart_id for event in events if event.type == "chart"]
    assert len(charts) == len(session.charts) == 2
    assert len(set(charts)) == 2
    for chart_id in charts:
        png = session.charts[chart_id]
        assert png.startswith(b"\x89PNG\r\n\x1a\n") and png.endswith(b"IEND\xaeB`\x82")
        assert len(png) > 1024
    assert not gateway.store.create().charts
    assert events[-1].type == "run_completed" and analysis is not None


@pytest.mark.parametrize("answer", ["没有引用", "伪造证据 [aeris_manual:forged:1:0]"])
async def test_unretrieved_or_missing_final_citation_rejected(gateway, answer):
    async def model(messages, info):
        yield answer

    session = gateway.store.create()
    with pytest.raises(AppError) as caught:
        await collect(gateway, model, session)
    assert caught.value.error.code == ErrorCode.CITATION_INVALID
    assert session.history == []


async def test_tool_budget_refuses_success_and_history(gateway):
    gateway.settings = gateway.settings.model_copy(update={"max_tool_calls": 1})

    async def model(messages, info):
        yield tool_delta("search_documents", {"query": "vibration", "limit": 1}, f"pdf-{len(messages)}")

    session = gateway.store.create()
    with pytest.raises(Exception) as caught:
        await collect(gateway, model, session)
    assert public_failure(caught.value).error.code == ErrorCode.BUDGET_EXCEEDED
    assert session.history == []


async def test_sdk_argument_validation_error_is_not_tool_success(gateway, caplog):
    async def model(messages, info):
        parts = [part for message in messages for part in message.parts if isinstance(part, RetryPromptPart)]
        if not parts:
            yield tool_delta("search_documents", {"query": "vibration", "limit": "PRIVATE_TOKEN_SECRET"}, "invalid")
        elif not returned(messages):
            yield tool_delta("search_documents", {"query": "vibration", "limit": 1}, "corrected")
        else:
            yield citation_answer(messages)

    events, _ = await collect(gateway, model)
    errors = [event.payload for event in events if event.type == "tool_error"]
    assert len(errors) == 1 and errors[0].tool_call_id == "invalid"
    assert errors[0].error_code == "invalid_arguments"
    retries = [event.payload for event in events if event.type == "retry"]
    assert len(retries) == 1 and retries[0].tool_call_id == "corrected"
    assert retries[0].reason == "invalid_arguments"
    assert "PRIVATE_TOKEN_SECRET" not in "".join(event.model_dump_json() for event in events)
    assert "PRIVATE_TOKEN_SECRET" not in caplog.text


async def test_other_session_citation_cannot_authorize_answer(gateway):
    owner = gateway.store.create()
    from asset_assistant.contracts import Citation

    citation = Citation.model_validate(gateway.knowledge.search_documents("vibration", limit=1)[0])
    gateway.store.add_citation(owner, citation)
    stranger = gateway.store.create()

    async def model(messages, info):
        yield f"不能借用另一会话的引用 [{citation.citation_id}]"

    with pytest.raises(AppError) as caught:
        await collect(gateway, model, stranger)
    assert caught.value.error.code == ErrorCode.CITATION_INVALID
    assert not stranger.history and not stranger.citations


@pytest.mark.parametrize("status, code", [
    (401, ErrorCode.MODEL_AUTH_FAILED), (403, ErrorCode.MODEL_ACCESS_DENIED),
    (404, ErrorCode.MODEL_DEPLOYMENT_NOT_FOUND), (429, ErrorCode.MODEL_RATE_LIMITED),
    (500, ErrorCode.MODEL_UNAVAILABLE),
])
def test_provider_failure_never_exports_private_details(status, code):
    error = RuntimeError("PRIVATE_TOKEN https://private.example/?api_key=SECRET")
    error.status_code = status
    public = public_failure(error)
    assert public.error.code == code
    assert "PRIVATE" not in str(public) and "SECRET" not in str(public)
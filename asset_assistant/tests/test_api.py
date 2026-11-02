import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from asset_assistant.app import create_app
from asset_assistant.config import Settings
from asset_assistant.contracts import TextDelta, TextPayload


def sse_events(response):
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert len({event["run_id"] for event in events}) == 1
    return events


@pytest.fixture
def app():
    return create_app(Settings())


async def test_health_and_missing_model(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        health = (await client.get("/api/health")).json()
        assert health["model_configured"] is False
        assert health["model_verified"] is False
        session = (await client.post("/api/sessions")).json()["session_id"]
        response = await client.post("/api/chat", json={"session_id": session, "message": "调查资产"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"
        assert (await client.delete(f"/api/sessions/{session}")).status_code == 204


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example"}, {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "cross-site"}, {"User-Agent": "Mozilla/5.0"},
    {"Origin": "http://localhost:8000"},
])
async def test_reject_cross_origin_and_dns_rebinding(app, headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        response = await client.post("/api/sessions", headers=headers)
        assert response.status_code == 403
        assert "error" in response.json()


async def test_same_origin_and_allowlists(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as client:
        response = await client.post("/api/sessions", headers={"Origin": "http://localhost:8000", "Sec-Fetch-Site": "same-origin"})
        assert response.status_code == 201
        assert (await client.get("/api/documents/insurance")).status_code == 404
        assert (await client.get("/static/FRONTEND.md")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404
        assert (await client.post("/api/chat", json={"session_id": response.json()["session_id"], "message": "   "})).status_code == 422


class FailingTestGateway:
    """Explicit offline test double; never selected by application configuration."""
    verified = False
    verified_at = None

    def prepare(self):
        pass

    async def close(self):
        pass

    async def stream(self, message, session):
        yield TextDelta(payload=TextPayload(text="生成中"))
        raise RuntimeError("DO_NOT_LEAK_PRIVATE_EXCEPTION")


async def test_post_stream_error_is_terminal_and_sanitized():
    app = create_app(Settings(), gateway=FailingTestGateway())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as client:
        session = (await client.post("/api/sessions")).json()["session_id"]
        response = await client.post("/api/chat", json={"session_id": session, "message": "检查"})
        assert response.status_code == 200
        assert "event: run_error" in response.text
        assert "event: run_completed" not in response.text
        assert "DO_NOT_LEAK" not in response.text
        assert not app.state.sessions.get(session).busy


def test_non_secret_configuration_contract():
    settings = Settings.from_env({
        "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
        "AZURE_OPENAI_DEPLOYMENT": "energy-deployment",
        "AZURE_OPENAI_MODEL": "verified-by-user-model-name",
        "AZURE_OPENAI_API_VERSION": "2026-01-01",
    })
    # Syntax/configuration only, never evidence that this API version or model exists.
    assert settings.model_configured
    assert not settings.model_copy(update={"azure_endpoint": "https://evil.example/"}).model_configured
    assert not settings.model_copy(update={"forbidden_credentials_present": True}).model_configured


@pytest.mark.parametrize("route, media_type, prefix", [
    ("/", "text/html", b"<!"),
    ("/static/app.js", "javascript", b""),
    ("/static/styles.css", "text/css", b""),
    ("/api/documents/aeris_manual", "application/pdf", b"%PDF-"),
    ("/api/documents/northgate_standards", "application/pdf", b"%PDF-"),
])
async def test_public_read_endpoints(app, route, media_type, prefix):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as client:
        response = await client.get(route)
        assert response.status_code == 200
        assert media_type in response.headers["content-type"]
        assert response.content.startswith(prefix) and response.content
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"


async def test_chart_route_session_isolation_and_delete(app):
    from test_agent import offline_tools

    # Real analysis PNG, not a fabricated success image or work-order data.
    settings = Settings()
    analysis = await asyncio.to_thread(offline_tools(settings.root).analyze_correlation,
                                      "WTG-114", "2026-06-16", "2026-08-14")
    png = analysis["charts"][0]["png"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as client:
        owner = (await client.post("/api/sessions")).json()["session_id"]
        other = (await client.post("/api/sessions")).json()["session_id"]
        chart_id = app.state.sessions.add_chart(app.state.sessions.get(owner), png)
        route = f"/api/sessions/{owner}/charts/{chart_id}"
        response = await client.get(route)
        assert response.status_code == 200 and response.content == png
        assert response.headers["content-type"] == "image/png"
        assert (await client.get(f"/api/sessions/{other}/charts/{chart_id}")).status_code == 404
        assert (await client.get(f"/api/sessions/{owner}/charts/{'z' * 43}")).status_code == 404
        assert (await client.get(f"/api/sessions/{owner}/charts/invalid")).status_code == 422
        assert (await client.delete(f"/api/sessions/{owner}")).status_code == 204
        assert (await client.get(route)).status_code == 404
        assert (await client.delete(f"/api/sessions/{owner}")).status_code == 404


class BlockingOfflineTestGateway(FailingTestGateway):
    """Explicit transport-only test gateway; no provider or fallback involved."""

    def __init__(self):
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream(self, message, session):
        try:
            self.started.set()
            yield TextDelta(payload=TextPayload(text="离线流取消测试"))
            await asyncio.Event().wait()
        finally:
            self.closed.set()


async def test_stream_timeout_is_terminal_and_releases_session():
    gateway = BlockingOfflineTestGateway()
    app = create_app(Settings(run_timeout_seconds=1), gateway=gateway)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as client:
        session_id = (await client.post("/api/sessions")).json()["session_id"]
        response = await asyncio.wait_for(client.post("/api/chat", json={
            "session_id": session_id, "message": "离线超时测试"}), timeout=5)
        assert response.status_code == 200
        events = sse_events(response)
        assert [event["type"] for event in events] == ["run_started", "text_delta", "run_error"]
        assert events[-1]["payload"]["error"]["code"] == "RUN_TIMEOUT"
        error = events[-1]["payload"]["error"]
        assert "1 秒整轮预算" in error["message"]
        assert "ASSET_ASSISTANT_RUN_TIMEOUT_SECONDS" in error["message"]
        assert "不会自动重放" in error["message"]
        assert error["details"]["timeout_scope"] == "run"
        assert error["details"]["elapsed_seconds"] >= 1
        assert error["details"]["run_timeout_seconds"] == 1
        assert error["details"]["tool_calls_started"] == error["details"]["citations_received"] == 0
        assert set(error["details"]) == {"timeout_scope", "elapsed_seconds", "run_timeout_seconds",
                         "tool_calls_started", "citations_received"}
        assert gateway.closed.is_set()
        session = app.state.sessions.get(session_id)
        assert not session.busy and session.task is None and not session.history


@pytest.mark.parametrize("cancel_method", ["delete-session", "cancel-request"])
async def test_running_stream_cancellation_and_busy_conflict(cancel_method):
    gateway = BlockingOfflineTestGateway()
    app = create_app(Settings(), gateway=gateway)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as client:
        session_id = (await client.post("/api/sessions")).json()["session_id"]
        session = app.state.sessions.get(session_id)
        body = {"session_id": session_id, "message": "离线取消测试"}
        request = asyncio.create_task(client.post("/api/chat", json=body))
        try:
            await asyncio.wait_for(gateway.started.wait(), timeout=3)
            conflict = await client.post("/api/chat", json=body)
            assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "CONFLICT"
            if cancel_method == "delete-session":
                assert (await client.delete(f"/api/sessions/{session_id}")).status_code == 204
                response = await asyncio.wait_for(request, timeout=3)
                assert "event: run_completed" not in response.text
                assert session.deleted and not session.charts and not session.citations
            else:
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            await asyncio.wait_for(gateway.closed.wait(), timeout=3)
            assert not session.busy and session.task is None and not session.history
        finally:
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)


async def test_sensitive_request_validation_is_redacted(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as client:
        response = await client.post("/api/chat", json={"session_id": "PRIVATE_TOKEN_SECRET", "message": "x"})
        assert response.status_code == 422
        assert "PRIVATE_TOKEN_SECRET" not in response.text
        oversized = await client.post("/api/chat", content=b"PRIVATE_TOKEN_SECRET" * 3000)
        assert oversized.status_code == 413 and "PRIVATE_TOKEN_SECRET" not in oversized.text


async def test_chat_success_with_explicit_offline_sdk_and_real_pdf():
    from pydantic_ai.models import override_allow_model_requests
    from pydantic_ai.models.function import FunctionModel

    from test_agent import citation_answer, offline_tools, returned, tool_delta

    settings = Settings(azure_endpoint="https://offline-test.openai.azure.com",
                        azure_deployment="offline-test", azure_model="offline-test",
                        azure_api_version="2026-01-01")
    app = create_app(settings)
    gateway = app.state.gateway
    gateway.tools = offline_tools(settings.root)

    async def model(messages, info):
        if not returned(messages):
            yield tool_delta("search_documents", {"query": "vibration", "limit": 1}, "pdf")
        else:
            yield citation_answer(messages)

    gateway._model = FunctionModel(stream_function=model, model_name="explicit-offline-api-test")
    with override_allow_model_requests(False):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost:8000") as client:
            session_id = (await client.post("/api/sessions")).json()["session_id"]
            response = await client.post("/api/chat", json={"session_id": session_id, "message": "检索振动依据"})
            assert response.status_code == 200 and "text/event-stream" in response.headers["content-type"]
            events = sse_events(response)
            assert events[0]["type"] == "run_started" and events[-1]["type"] == "run_completed"
            assert not any(event["type"] == "run_error" for event in events)
            citation = next(event["payload"] for event in events if event["type"] == "citation")
            assert events[-1]["payload"]["citation_ids"] == [citation["citation_id"]]
            assert app.state.sessions.get(session_id).history
            assert gateway._client is None and gateway._credential is None
            assert not app.state.sessions.get(session_id).busy
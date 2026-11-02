"""Actual SDK request serialization against MockTransport only; no CLI or network."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from azure.core.credentials import AccessToken
from openai import AsyncAzureOpenAI
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters, override_allow_model_requests
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.profiles.openai import openai_model_profile
from pydantic_ai.providers.azure import AzureProvider

from asset_assistant.agent import AzureModelGateway, public_failure
from asset_assistant.config import Settings
from asset_assistant.sessions import SessionStore


@pytest.mark.parametrize("responses,full_uri", [(True, True), (True, False), (False, False)])
@pytest.mark.parametrize("budgets", [{}, {"run_timeout_seconds": 120, "request_timeout_seconds": 300,
                                       "request_max_retries": 0},
                                   {"run_timeout_seconds": 1, "request_timeout_seconds": 600,
                                    "request_max_retries": 2}])
async def test_prepare_and_sdk_request_route(responses, full_uri, budgets):
    version = "2025-04-01-preview"
    root = "https://test.cognitiveservices.azure.com"
    settings = Settings(azure_endpoint=root + (f"/openai/responses?api-version={version}" if full_uri else ""),
                        azure_deployment="custom-energy-deployment", azure_model="gpt-5.4-pro",
                        azure_api_version="" if full_uri else version,
                        azure_api_mode="responses" if responses and not full_uri else "", **budgets)
    credential = SimpleNamespace(get_token=AsyncMock(return_value=AccessToken("offline-test-token", 4102444800)),
                                 close=AsyncMock())
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.host == "test.cognitiveservices.azure.com"
        expected = "/openai/responses" if responses else "/openai/deployments/custom-energy-deployment/chat/completions"
        assert request.url.path == expected
        assert list(request.url.params.multi_items()) == [("api-version", version)]
        assert request.headers["authorization"] == "Bearer offline-test-token"
        assert "api-key" not in request.headers
        body = json.loads(request.content)
        expected_timeout = min(settings.request_timeout_seconds, settings.run_timeout_seconds)
        assert request.extensions["timeout"] == {
            "connect": min(10, expected_timeout), "read": expected_timeout,
            "write": expected_timeout, "pool": expected_timeout,
        }
        # Responses SDK may include its default reasoning object. The app must
        # not set effort (in either protocol) just to work around a timeout.
        assert "effort" not in body.get("reasoning", {}) and "reasoning_effort" not in body
        assert body["model"] == "custom-energy-deployment"
        assert body["max_output_tokens" if responses else "max_completion_tokens"] == 128
        if responses:
            assert "input" in body and "messages" not in body
            data = {"id": "resp_offline", "object": "response", "created_at": 0, "status": "completed",
                    "model": "gpt-5.4-pro", "output": [{"id": "msg_offline", "type": "message",
                    "role": "assistant", "status": "completed", "content": [{"type": "output_text",
                    "text": "offline", "annotations": []}]}]}
        else:
            assert "messages" in body and "input" not in body
            data = {"id": "chat_offline", "object": "chat.completion", "created": 0, "model": "gpt-5.4-pro",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "offline"}}]}
        return httpx.Response(200, json=data)

    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    def client_factory(**kwargs):
        return AsyncAzureOpenAI(**kwargs, http_client=transport_client)

    gateway = AzureModelGateway(settings, SessionStore(settings), SimpleNamespace(ready=True), object())
    with patch("azure.identity.aio.AzureCliCredential", return_value=credential) as credential_class, \
            patch("openai.AsyncAzureOpenAI", side_effect=client_factory) as client_class:
        try:
            gateway.prepare()
            gateway.prepare()  # Idempotent; still no token or request during preparation.
            credential_class.assert_called_once_with(process_timeout=10)
            client_class.assert_called_once()
            assert client_class.call_args.kwargs["max_retries"] == settings.request_max_retries
            assert gateway._client.max_retries == settings.request_max_retries
            credential.get_token.assert_not_awaited()
            assert not requests
            assert isinstance(gateway._model, OpenAIResponsesModel if responses else OpenAIChatModel)
            profile = (openai_model_profile(settings.azure_model) if responses
                       else AzureProvider.model_profile(settings.azure_model))
            # Chat model initialization strips unsupported native tools. Compare
            # against the same SDK normalization of the *underlying* profile.
            expected_model = (OpenAIResponsesModel if responses else OpenAIChatModel)(
                settings.azure_deployment, provider=gateway._model._provider, profile=profile)
            assert gateway._model.profile == expected_model.profile
            assert gateway._model.profile["openai_supports_reasoning"] is True
            assert gateway._model.model_name == settings.azure_deployment
            with override_allow_model_requests(True):  # MockTransport is the ONLY transport.
                result = await gateway._model.request([ModelRequest(parts=[UserPromptPart("offline")])],
                                                      {"max_tokens": 128}, ModelRequestParameters())
            assert result.parts[0].content == "offline"
            assert len(requests) == 1
            credential.get_token.assert_awaited_once_with("https://cognitiveservices.azure.com/.default")
            assert not gateway.verified  # Serialization tests are not real model verification.
        finally:
            await gateway.close()
            await transport_client.aclose()
        credential.close.assert_awaited_once()


async def test_sdk_read_timeout_is_not_reported_as_whole_run_deadline():
    """Real SDK exception conversion; no retry/backoff, token provider or network."""
    from pydantic_ai.exceptions import ModelTimeoutError

    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("PRIVATE_SECRET", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as http_client:
        client = AsyncAzureOpenAI(azure_endpoint="https://test.openai.azure.com",
                                  api_version="2025-04-01-preview", api_key="offline-only",
                                  http_client=http_client, timeout=1, max_retries=0)
        model = OpenAIResponsesModel("gpt-5.4-pro", provider=AzureProvider(openai_client=client))
        try:
            with override_allow_model_requests(True), pytest.raises(ModelTimeoutError) as caught:
                await model.request([ModelRequest(parts=[UserPromptPart("offline")])],
                                    {"max_tokens": 128}, ModelRequestParameters())
            failure = public_failure(caught.value)
            assert failure.error.code == "RUN_TIMEOUT"
            assert "不代表已耗尽整轮预算" in failure.error.message
            assert "ASSET_ASSISTANT_REQUEST_TIMEOUT_SECONDS" in failure.error.message
            assert "PRIVATE_SECRET" not in failure.error.model_dump_json()
            assert failure.error.details is None and len(calls) == 1
        finally:
            await client.close()
"""Configuration security and persistence, with no developer .env reads."""

import io
import os
from unittest.mock import patch

import pytest
from dotenv import load_dotenv as real_load_dotenv

from asset_assistant import config
from asset_assistant.config import Settings

ROOT = "https://hub15989720988.cognitiveservices.azure.com"
VERSION = "2025-04-01-preview"
URI = ROOT + "/openai/responses?api-version=" + VERSION


def configured(endpoint=URI, **updates):
    return Settings(azure_endpoint=endpoint, azure_deployment="energy-prod",
                    azure_model="gpt-5.4-pro", **updates)


@pytest.mark.parametrize("host", ["example.openai.azure.com", "example.cognitiveservices.azure.com",
                                 "EXAMPLE.OPENAI.AZURE.COM:443", "a-b.cognitiveservices.azure.com"])
@pytest.mark.parametrize("path,mode", [("", "chat"), ("/", "chat"), ("/openai/responses", "responses")])
def test_safe_resource_hosts_and_normalization(host, path, mode):
    settings = configured("https://" + host + path, azure_api_version=VERSION)
    assert settings.model_configured
    assert settings.endpoint_configuration == ("https://" + host.lower().removesuffix(":443"), VERSION, mode)


def test_portal_responses_version_and_explicit_root_mode():
    settings = configured()
    assert settings.model_configured
    assert settings.endpoint_configuration == (ROOT, VERSION, "responses")
    assert configured(azure_api_version=VERSION).model_configured
    assert not configured(azure_api_version="2025-03-01-preview").model_configured
    assert not configured(azure_api_mode="chat").model_configured
    assert configured(ROOT, azure_api_version=VERSION, azure_api_mode="responses").endpoint_configuration == (
        ROOT, VERSION, "responses")


@pytest.mark.parametrize("endpoint", [
    "http://example.openai.azure.com", "https://openai.azure.com", "https://cognitiveservices.azure.com",
    "https://a.b.openai.azure.com", "https://example.openai.azure.com.evil.test",
    "https://evilopenai.azure.com", "https://-bad.openai.azure.com", "https://bad-.openai.azure.com",
    "https://127.0.0.1", "https://[::1]", "https://example.openai.azure.com:444",
    "https://example.openai.azure.com:0443", "https://example.openai.azure.com.",
    "https://user@example.openai.azure.com", "https://user:pass@example.openai.azure.com",
    "https://example.openai.azure.com\\@evil.test", "https://ex%61mple.openai.azure.com",
    ROOT + "/openai", ROOT + "/openai/v1/responses", ROOT + "/openai/responses/",
    ROOT + "/openai/deployments/name/responses", ROOT + "/a/../openai/responses",
    ROOT + "/%6fpenai/responses", ROOT + "/openai//responses", ROOT + "/openai/responses#",
    URI + "#fragment", URI + "&api-version=" + VERSION, URI + "&key=secret",
    URI + "&", ROOT + "?api-version=" + VERSION, ROOT + "?",
    ROOT + "/openai/responses?", ROOT + "/openai/responses?API-VERSION=" + VERSION,
    ROOT + "/openai/responses?api-version=2025%2D04%2D01-preview", ROOT + "/openai/responses\n",
    "https://exam\tple.openai.azure.com", "https://éxample.openai.azure.com",
])
def test_unsafe_endpoints_never_configured(endpoint):
    assert not configured(endpoint, azure_api_version=VERSION).model_configured


@pytest.mark.parametrize("version", ["", "latest", "2025-02-30", "2025-13-01", "2025-04-01-preview&x=1"])
def test_no_guessed_or_invalid_version(version):
    assert not configured(ROOT, azure_api_version=version).model_configured


def test_missing_invalid_mode_and_forbidden_credentials():
    assert not configured(azure_api_mode="invalid").model_configured
    for name in ("AZURE_OPENAI_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_AD_TOKEN"):
        assert "API_KEYS_OR_STATIC_TOKENS_NOT_ALLOWED" in Settings.from_env({name: "test-only"}).configuration_issues


def test_explicit_mapping_isolated_from_disk_and_process(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "must-not-inherit")
    with patch.object(config, "load_dotenv", side_effect=AssertionError("no disk access")):
        settings = Settings.from_env({"AZURE_OPENAI_ENDPOINT": URI, "AZURE_OPENAI_DEPLOYMENT": "mapped",
                                      "AZURE_OPENAI_MODEL": "gpt-5.4-pro", "AZURE_OPENAI_API_VERSION": ""})
        assert settings.model_configured and settings.azure_deployment == "mapped"
        assert Settings.from_env({}).azure_deployment == ""


def test_fixed_dotenv_path_process_precedence_and_no_interpolation():
    def memory_load(**kwargs):
        assert kwargs == {"dotenv_path": config.LOCAL_ENV_FILE, "override": False, "interpolate": False}
        return real_load_dotenv(stream=io.StringIO(
            f"AZURE_OPENAI_ENDPOINT={URI}\nAZURE_OPENAI_DEPLOYMENT=file\n"
            "AZURE_OPENAI_MODEL=gpt-5.4-pro\nAZURE_OPENAI_API_VERSION=\n"
            "ASSET_ASSISTANT_PORT=8100\nUNEXPANDED=${AZURE_OPENAI_DEPLOYMENT}\n"),
            override=False, interpolate=False)

    assert config.LOCAL_ENV_FILE == config.Path(config.__file__).resolve().parents[2] / ".env"
    with patch.dict(os.environ, {"AZURE_OPENAI_DEPLOYMENT": "process"}, clear=True), \
            patch.object(config, "load_dotenv", side_effect=memory_load):
        settings = Settings.from_env()
        assert settings.model_configured and settings.azure_deployment == "process" and settings.port == 8100
        assert os.environ["UNEXPANDED"] == "${AZURE_OPENAI_DEPLOYMENT}"
    with patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": ""}, clear=True), \
            patch.object(config, "load_dotenv", side_effect=memory_load):
        assert not Settings.from_env().model_configured
        assert os.environ["AZURE_OPENAI_ENDPOINT"] == ""


@pytest.mark.parametrize("field,name,default,minimum,maximum", [
    ("run_timeout_seconds", "ASSET_ASSISTANT_RUN_TIMEOUT_SECONDS", 600, 1, 1800),
    ("request_timeout_seconds", "ASSET_ASSISTANT_REQUEST_TIMEOUT_SECONDS", 300, 1, 600),
    ("request_max_retries", "ASSET_ASSISTANT_REQUEST_MAX_RETRIES", 1, 0, 2),
])
def test_bounded_timeout_environment_knobs(field, name, default, minimum, maximum):
    assert getattr(Settings.from_env({}), field) == default
    for value in (minimum, maximum):
        assert getattr(Settings.from_env({name: str(value)}), field) == value
    for value in ("", "PRIVATE_SECRET", "1.5", "nan", "inf", str(minimum - 1), str(maximum + 1)):
        assert getattr(Settings.from_env({name: value}), field) == default
    for value in (minimum - 1, maximum + 1):
        with pytest.raises(ValueError):
            Settings(**{field: value})


def test_timeout_environment_process_precedence_without_disk():
    def memory_load(**kwargs):
        return real_load_dotenv(stream=io.StringIO(
            "ASSET_ASSISTANT_RUN_TIMEOUT_SECONDS=900\nASSET_ASSISTANT_REQUEST_MAX_RETRIES=2\n"),
            override=False, interpolate=False)

    with patch.dict(os.environ, {"ASSET_ASSISTANT_RUN_TIMEOUT_SECONDS": "120",
                                "ASSET_ASSISTANT_REQUEST_MAX_RETRIES": "0"}, clear=True), \
            patch.object(config, "load_dotenv", side_effect=memory_load):
        settings = Settings.from_env()
        assert settings.run_timeout_seconds == 120 and settings.request_max_retries == 0
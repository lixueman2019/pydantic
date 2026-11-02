"""Non-secret local configuration; missing model settings do not prevent UI startup."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

LOCAL_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
API_VERSION_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:-preview)?"
RESOURCE_HOST_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.(?:openai|cognitiveservices)\.azure\.com(?::443)?"


def endpoint_configuration(endpoint: str, version: str, mode: str) -> tuple[str, str, str]:
    """Strict allowlist; never forward portal paths, arbitrary queries or credentials."""
    if mode not in ("", "chat", "responses"):
        raise ValueError("unsupported API mode")
    if any(ord(char) <= 32 or ord(char) >= 127 for char in endpoint) or "#" in endpoint:
        raise ValueError("unsafe endpoint")
    url = urlsplit(endpoint)
    if url.scheme != "https" or not re.fullmatch(RESOURCE_HOST_PATTERN, url.netloc, re.IGNORECASE):
        raise ValueError("unsafe resource host")
    if url.path not in ("", "/", "/openai/responses"):
        raise ValueError("unsupported API path")
    responses = url.path == "/openai/responses"
    uri_version = ""
    if "?" in endpoint:
        if not responses or not re.fullmatch("api-version=" + API_VERSION_PATTERN, url.query):
            raise ValueError("unsupported query")
        uri_version = url.query.removeprefix("api-version=")
    if uri_version and version and uri_version != version:
        raise ValueError("conflicting API versions")
    if responses and mode == "chat":
        raise ValueError("conflicting API mode")
    return f"https://{url.hostname}", version or uri_version, "responses" if responses else mode or "chat"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[3])
    port: int = Field(default=8000, ge=1024, le=65535)
    azure_endpoint: str = ""
    azure_deployment: str = ""
    azure_model: str = ""
    azure_api_version: str = ""
    azure_api_mode: str = ""
    forbidden_credentials_present: bool = False
    max_sessions: int = Field(default=64, ge=1, le=256)
    session_ttl_seconds: int = Field(default=1800, ge=1, le=86400)
    max_history_turns: int = Field(default=12, ge=1, le=24)
    max_history_chars: int = Field(default=64000, ge=1024, le=128000)
    max_charts: int = Field(default=8, ge=1, le=16)
    max_chart_bytes: int = Field(default=2_000_000, ge=1024, le=4_000_000)
    max_citations: int = Field(default=128, ge=1, le=256)
    max_tool_calls: int = Field(default=20, ge=1, le=20)
    run_timeout_seconds: int = Field(default=600, ge=1, le=1800)
    request_timeout_seconds: int = Field(default=300, ge=1, le=600)
    request_max_retries: int = Field(default=1, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=128, le=8192)
    max_total_tokens: int = Field(default=64000, ge=1024, le=128000)
    max_recoveries: int = Field(default=3, ge=0, le=3)

    @property
    def static_dir(self) -> Path:
        return Path(__file__).resolve().parent / "static"

    @property
    def configuration_issues(self) -> list[str]:
        issues = []
        for field, env in (
            (self.azure_endpoint, "AZURE_OPENAI_ENDPOINT"),
            (self.azure_deployment, "AZURE_OPENAI_DEPLOYMENT"),
            (self.azure_model, "AZURE_OPENAI_MODEL"),
        ):
            if not field:
                issues.append(env + "_MISSING")
        version = self.azure_api_version
        try:
            _, version, _ = self.endpoint_configuration
            valid = True
        except ValueError:
            valid = False
        if self.azure_endpoint and not valid:
            issues.append("AZURE_OPENAI_ENDPOINT_INVALID")
        if self.azure_deployment and not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", self.azure_deployment):
            issues.append("AZURE_OPENAI_DEPLOYMENT_INVALID")
        if not version:
            issues.append("AZURE_OPENAI_API_VERSION_MISSING")
        else:
            try:
                if not re.fullmatch(API_VERSION_PATTERN, version):
                    raise ValueError("invalid version")
                date.fromisoformat(version[:10])
            except ValueError:
                issues.append("AZURE_OPENAI_API_VERSION_INVALID")
        if self.forbidden_credentials_present:
            issues.append("API_KEYS_OR_STATIC_TOKENS_NOT_ALLOWED")
        return issues

    @property
    def endpoint_configuration(self) -> tuple[str, str, str]:
        return endpoint_configuration(self.azure_endpoint, self.azure_api_version, self.azure_api_mode)

    @property
    def model_configured(self) -> bool:
        return not self.configuration_issues

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        if env is None:
            # Fixed package-local path only; no cwd/parent discovery and no overrides.
            load_dotenv(dotenv_path=LOCAL_ENV_FILE, override=False, interpolate=False)
        values = os.environ if env is None else env
        names = {"azure_endpoint": "AZURE_OPENAI_ENDPOINT",
                 "azure_deployment": "AZURE_OPENAI_DEPLOYMENT",
                 "azure_model": "AZURE_OPENAI_MODEL",
                 "azure_api_version": "AZURE_OPENAI_API_VERSION",
                 "azure_api_mode": "AZURE_OPENAI_API_MODE"}
        config: dict[str, object] = {key: values.get(name, "").strip()
                                    for key, name in names.items()}
        config["forbidden_credentials_present"] = any(values.get(name) for name in (
            "AZURE_OPENAI_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_AD_TOKEN"))
        # Invalid non-model knobs use bounded defaults, without logging supplied values.
        for field, name, minimum, maximum in (
            ("port", "ASSET_ASSISTANT_PORT", 1024, 65535),
            ("run_timeout_seconds", "ASSET_ASSISTANT_RUN_TIMEOUT_SECONDS", 1, 1800),
            ("request_timeout_seconds", "ASSET_ASSISTANT_REQUEST_TIMEOUT_SECONDS", 1, 600),
            ("request_max_retries", "ASSET_ASSISTANT_REQUEST_MAX_RETRIES", 0, 2),
        ):
            try:
                value = int(values.get(name, ""))
                if minimum <= value <= maximum:
                    config[field] = value
            except ValueError:
                pass
        return cls.model_validate(config)
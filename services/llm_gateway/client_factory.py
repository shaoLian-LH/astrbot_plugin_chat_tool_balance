from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .contracts import FallbackReasonCode


@dataclass(frozen=True)
class ClientFactoryResult:
    provider_id: str
    client: Any | None
    reason_code: FallbackReasonCode | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.client is not None


class ResponsesClientFactory:
    def __init__(
        self,
        client_builder: Any | None = None,
    ) -> None:
        self.client_builder = client_builder or _default_openai_client_builder

    def create_client(self, provider_id: str) -> ClientFactoryResult:
        normalized_provider_id = str(provider_id or "").strip()
        if not normalized_provider_id:
            return ClientFactoryResult(
                provider_id=normalized_provider_id,
                client=None,
                reason_code=FallbackReasonCode.CAPABILITY_UNSUPPORTED,
                error="provider_id_required",
            )
        try:
            client = self.client_builder(normalized_provider_id)
        except (ImportError, ModuleNotFoundError) as exc:
            return ClientFactoryResult(
                provider_id=normalized_provider_id,
                client=None,
                reason_code=FallbackReasonCode.CAPABILITY_UNSUPPORTED,
                error=str(exc),
            )
        except Exception as exc:
            return ClientFactoryResult(
                provider_id=normalized_provider_id,
                client=None,
                reason_code=FallbackReasonCode.RESPONSES_PARSE_ERROR,
                error=str(exc),
            )

        if client is None:
            return ClientFactoryResult(
                provider_id=normalized_provider_id,
                client=None,
                reason_code=FallbackReasonCode.CAPABILITY_UNSUPPORTED,
                error="client_builder_returned_none",
            )
        return ClientFactoryResult(provider_id=normalized_provider_id, client=client)


def _default_openai_client_builder(_provider_id: str) -> Any:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "EMPTY")
    base_url = os.getenv("OPENAI_BASE_URL")
    timeout_seconds = _parse_timeout_seconds(os.getenv("OPENAI_TIMEOUT_SECONDS"), default=20.0)

    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": timeout_seconds,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _parse_timeout_seconds(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed

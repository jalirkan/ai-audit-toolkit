"""Adapters for real endpoints: Anthropic, and any OpenAI-compatible API.

Both are opt-in. Nothing constructs one unless the operator names the adapter
*and* the key is present in the environment (D-001). The mock remains the
default everywhere, including in the CLI.

Request and response shapes are exercised against injected fake transports, so
this module is fully covered by an offline test suite. What the tests cannot
verify is that the shapes still match a live API -- providers change them.
:func:`build_adapter` therefore fails loudly on an unexpected response rather
than silently producing an empty completion, which would otherwise show up as
a fascinating and entirely fictitious finding.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from adapters.base import AdapterError, ModelAdapter
from adapters.http import HttpModelAdapter, Transport
from adapters.mock import MockAdapter

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"

OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_KEY_ENV = "OPENAI_API_KEY"
OPENAI_BASE_URL_ENV = "OPENAI_BASE_URL"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

ADAPTER_MOCK = "mock"
ADAPTER_ANTHROPIC = "anthropic"
ADAPTER_OPENAI = "openai"
ADAPTER_NAMES = (ADAPTER_MOCK, ADAPTER_ANTHROPIC, ADAPTER_OPENAI)

__all__ = [
    "ANTHROPIC_KEY_ENV",
    "OPENAI_KEY_ENV",
    "OPENAI_BASE_URL_ENV",
    "ADAPTER_MOCK",
    "ADAPTER_ANTHROPIC",
    "ADAPTER_OPENAI",
    "ADAPTER_NAMES",
    "AnthropicAdapter",
    "OpenAICompatibleAdapter",
    "build_adapter",
]


class AnthropicAdapter(HttpModelAdapter):
    """Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        *,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        api_key: Optional[str] = None,
        base_url: str = ANTHROPIC_BASE_URL,
        params: Optional[Dict[str, Any]] = None,
        system: Optional[str] = None,
        transport: Optional[Transport] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key if api_key is not None else os.environ.get(ANTHROPIC_KEY_ENV, ""),
            base_url=base_url,
            params=params,
            system=system,
            transport=transport,
            **kwargs,
        )

    def endpoint(self) -> str:
        return f"{self.base_url}/v1/messages"

    def headers(self) -> Dict[str, str]:
        return {
            "content-type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def request_body(self, prompt: str, system: Optional[str]) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.params.get("max_tokens", 512),
            "temperature": self.params.get("temperature", 0.0),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        for optional in ("top_p", "top_k", "stop_sequences"):
            if optional in self.params:
                body[optional] = self.params[optional]
        return body

    def parse_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        blocks = payload.get("content")
        if not isinstance(blocks, list):
            raise AdapterError(
                "anthropic response had no content list; the API shape may have "
                f"changed. Keys present: {sorted(payload)}"
            )
        text = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = payload.get("usage") or {}
        reported: Dict[str, int] = {}
        if "input_tokens" in usage and usage["input_tokens"] is not None:
            reported["prompt_tokens"] = int(usage["input_tokens"])
        if "output_tokens" in usage and usage["output_tokens"] is not None:
            reported["completion_tokens"] = int(usage["output_tokens"])
        return {
            "text": text,
            "finish_reason": payload.get("stop_reason"),
            "request_id": payload.get("id"),
            "usage": reported,
        }


class OpenAICompatibleAdapter(HttpModelAdapter):
    """Any endpoint speaking the OpenAI chat-completions shape.

    Covers OpenAI itself and the many local and hosted servers that imitate it;
    point ``base_url`` at whichever one is under audit.
    """

    name = "openai-compatible"

    def __init__(
        self,
        *,
        model: str = DEFAULT_OPENAI_MODEL,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        system: Optional[str] = None,
        transport: Optional[Transport] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key if api_key is not None else os.environ.get(OPENAI_KEY_ENV, ""),
            base_url=base_url or os.environ.get(OPENAI_BASE_URL_ENV, OPENAI_BASE_URL),
            params=params,
            system=system,
            transport=transport,
            **kwargs,
        )

    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def headers(self) -> Dict[str, str]:
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self._api_key}",
        }

    def request_body(self, prompt: str, system: Optional[str]) -> Dict[str, Any]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.params.get("temperature", 0.0),
            "max_tokens": self.params.get("max_tokens", 512),
        }
        for optional in ("top_p", "seed", "stop"):
            if optional in self.params:
                body[optional] = self.params[optional]
        return body

    def parse_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AdapterError(
                "openai-compatible response had no choices; the API shape may "
                f"have changed. Keys present: {sorted(payload)}"
            )
        message = choices[0].get("message") or {}
        usage = payload.get("usage") or {}
        reported: Dict[str, int] = {}
        if "prompt_tokens" in usage and usage["prompt_tokens"] is not None:
            reported["prompt_tokens"] = int(usage["prompt_tokens"])
        if "completion_tokens" in usage and usage["completion_tokens"] is not None:
            reported["completion_tokens"] = int(usage["completion_tokens"])
        return {
            "text": message.get("content") or "",
            "finish_reason": choices[0].get("finish_reason"),
            "request_id": payload.get("id"),
            "usage": reported,
        }


def build_adapter(
    name: str = ADAPTER_MOCK,
    *,
    model: Optional[str] = None,
    system: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    transport: Optional[Transport] = None,
) -> ModelAdapter:
    """Construct an adapter by name. Defaults to the offline mock.

    Raises:
        ValueError: if a real adapter is requested without its key in the
            environment. Failing here is deliberate: silently falling back to
            the mock would produce evidence labelled as though it came from the
            real endpoint.
    """
    if name == ADAPTER_MOCK:
        return MockAdapter(
            model=model or "mock-deterministic-v1", system=system, params=params
        )

    if name == ADAPTER_ANTHROPIC:
        key = os.environ.get(ANTHROPIC_KEY_ENV, "")
        if not key and transport is None:
            raise ValueError(
                f"the anthropic adapter needs {ANTHROPIC_KEY_ENV} in the "
                "environment. Nothing here falls back to the mock, because "
                "evidence must never be labelled with an endpoint it did not "
                "come from."
            )
        return AnthropicAdapter(
            model=model or DEFAULT_ANTHROPIC_MODEL,
            api_key=key or "test-transport",
            system=system,
            params=params,
            transport=transport,
        )

    if name == ADAPTER_OPENAI:
        key = os.environ.get(OPENAI_KEY_ENV, "")
        if not key and transport is None:
            raise ValueError(
                f"the openai adapter needs {OPENAI_KEY_ENV} in the environment "
                f"(set {OPENAI_BASE_URL_ENV} too for a compatible server). "
                "Nothing here falls back to the mock."
            )
        return OpenAICompatibleAdapter(
            model=model or DEFAULT_OPENAI_MODEL,
            api_key=key or "test-transport",
            system=system,
            params=params,
            transport=transport,
        )

    raise ValueError(f"unknown adapter {name!r}; expected one of {list(ADAPTER_NAMES)}")

"""Shared plumbing for adapters that talk to a real endpoint over HTTP.

Built on ``urllib`` from the standard library rather than ``requests``, because
D-001 says a dependency has to earn its place and this one does not: the whole
requirement is one POST with JSON in and JSON out.

## The transport seam

Every request goes through a ``Transport`` callable. The default one uses
``urllib``; tests inject a fake that returns canned bytes. That is what lets the
suite exercise request construction, response parsing, error handling, and
retry behaviour without a network or a key, which D-001 requires. Nothing in
the test suite can accidentally reach the internet, because nothing in it
constructs the default transport.

## Keys are never rendered

The API key is held on the instance and put into request headers. It is not in
the fingerprint, the ``describe()`` string, the ``repr``, the recorded
``raw`` payload, or any error message. A test asserts it appears in none of
them -- an evidence journal that captured a credential would be worse than no
journal.

## Retries

A single 429 or 502 partway through a battery would otherwise turn a whole
procedure into error evidence. Requests are retried a bounded number of times
on retryable statuses with exponential backoff. The sleep function is
injectable so tests do not wait.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from adapters.base import AdapterError, ModelAdapter

#: ``(url, headers, body) -> (status, response_bytes)``
Transport = Callable[[str, Mapping[str, str], bytes], Tuple[int, bytes]]

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 1.0

#: Transient conditions worth retrying. Everything else fails immediately --
#: retrying a 400 just wastes the operator's time and money.
RETRYABLE_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})

__all__ = [
    "Transport",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_RETRIES",
    "RETRYABLE_STATUSES",
    "HttpModelAdapter",
    "urllib_transport",
]


def urllib_transport(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Transport:
    """The real transport. Constructed only when a real call is intended."""

    def send(url: str, headers: Mapping[str, str], body: bytes) -> Tuple[int, bytes]:
        request = urllib.request.Request(
            url, data=body, headers=dict(headers), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:  # pragma: no cover - needs network
            return exc.code, exc.read()
        except urllib.error.URLError as exc:  # pragma: no cover - needs network
            raise AdapterError(f"could not reach {url}: {exc.reason}") from None

    return send


class HttpModelAdapter(ModelAdapter):
    """Base for adapters that POST JSON to an endpoint.

    Subclasses supply the URL, headers, request body, and response parsing.
    """

    requires_network = True

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        params: Optional[Dict[str, Any]] = None,
        system: Optional[str] = None,
        transport: Optional[Transport] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep: Callable[[float], None] = None,  # type: ignore[assignment]
    ) -> None:
        if not api_key:
            raise ValueError(
                f"{type(self).__name__} requires an API key. This toolkit runs "
                "fully offline against the mock adapter; a real endpoint is "
                "opt-in and needs an explicit key."
            )
        super().__init__(model=model, params=params, system=system)
        # Held privately and never rendered. See the module docstring.
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._transport = transport
        if sleep is None:
            import time

            sleep = time.sleep
        self._sleep = sleep

    # -- subclass hooks -------------------------------------------------------

    def endpoint(self) -> str:
        raise NotImplementedError

    def headers(self) -> Dict[str, str]:
        raise NotImplementedError

    def request_body(self, prompt: str, system: Optional[str]) -> Dict[str, Any]:
        raise NotImplementedError

    def parse_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return ``{"text", "finish_reason", "usage", "request_id"}``."""
        raise NotImplementedError

    # -- request execution ----------------------------------------------------

    def _resolve_transport(self) -> Transport:
        if self._transport is None:
            # Built lazily so that merely constructing an adapter, which tests
            # and the CLI both do, never prepares a network path.
            self._transport = urllib_transport(self.timeout)
        return self._transport

    def post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """POST ``body`` and return the decoded JSON response."""
        transport = self._resolve_transport()
        encoded = json.dumps(body).encode("utf-8")
        url = self.endpoint()
        last_status = 0
        last_detail = ""

        for attempt in range(self.max_retries + 1):
            status, raw = transport(url, self.headers(), encoded)
            if 200 <= status < 300:
                try:
                    return json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise AdapterError(
                        f"{self.name} returned a {status} that was not JSON: {exc}"
                    ) from None

            last_status = status
            last_detail = self._error_detail(raw)
            if status not in RETRYABLE_STATUSES or attempt == self.max_retries:
                break
            self._sleep(DEFAULT_BACKOFF_SECONDS * (2**attempt))

        raise AdapterError(
            f"{self.name} request failed with status {last_status}"
            + (f": {last_detail}" if last_detail else "")
        )

    @staticmethod
    def _error_detail(raw: bytes) -> str:
        """Best-effort message from an error body, truncated and key-free."""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return raw.decode("utf-8", errors="replace")[:200]
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message", error))[:200]
        return str(error or payload)[:200]

    # -- ModelAdapter ---------------------------------------------------------

    def complete(self, prompt: str, *, system: Optional[str] = None):
        from time import perf_counter

        from adapters.base import ModelResponse

        effective_system = self.effective_system(system)
        started = perf_counter()
        payload = self.post(self.request_body(prompt, effective_system))
        latency_ms = (perf_counter() - started) * 1000.0

        parsed = self.parse_response(payload)
        return ModelResponse(
            text=parsed["text"],
            model=self.model,
            latency_ms=latency_ms,
            system=effective_system,
            finish_reason=parsed.get("finish_reason"),
            usage=parsed.get("usage") or {},
            request_id=parsed.get("request_id"),
            # Only non-sensitive metadata; never headers, never the key.
            raw={"endpoint": self.endpoint()},
        )

    def describe(self) -> str:
        return f"{self.name}:{self.model} (network, {self.fingerprint().short()})"

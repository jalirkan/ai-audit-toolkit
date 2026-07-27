"""Tests for the real-endpoint adapters, using injected fake transports.

No test here touches the network or reads a real key. The transport seam is
what makes that possible while still exercising request construction, response
parsing, error handling, and retries -- D-001 requires the suite to run fully
offline, and an adapter nobody can test offline is an adapter nobody tests.
"""

from __future__ import annotations

import json
import os
import unittest
from typing import Any, Dict, List, Mapping, Tuple
from unittest import mock

from adapters.base import AdapterError, ModelAdapter, assert_offline
from adapters.http import RETRYABLE_STATUSES, HttpModelAdapter
from adapters.mock import MockAdapter
from adapters.remote import (
    ADAPTER_ANTHROPIC,
    ADAPTER_MOCK,
    ADAPTER_OPENAI,
    ANTHROPIC_KEY_ENV,
    OPENAI_KEY_ENV,
    AnthropicAdapter,
    OpenAICompatibleAdapter,
    build_adapter,
)

SECRET = "sk-do-not-leak-me-0000"

ANTHROPIC_OK = {
    "id": "msg_01abc",
    "content": [{"type": "text", "text": "The answer is Paris."}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 12, "output_tokens": 5},
}

OPENAI_OK = {
    "id": "chatcmpl-01abc",
    "choices": [
        {"message": {"role": "assistant", "content": "The answer is Paris."},
         "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 5},
}


class RecordingTransport:
    """Fake transport that records calls and replays scripted responses."""

    def __init__(self, responses: List[Tuple[int, Any]]):
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes):
        self.calls.append(
            {"url": url, "headers": dict(headers), "body": json.loads(body)}
        )
        status, payload = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, raw


def anthropic(responses=None, **kwargs) -> AnthropicAdapter:
    transport = RecordingTransport(responses or [(200, ANTHROPIC_OK)])
    adapter = AnthropicAdapter(
        api_key=SECRET, transport=transport, sleep=lambda _: None, **kwargs
    )
    adapter.test_transport = transport  # type: ignore[attr-defined]
    return adapter


def openai(responses=None, **kwargs) -> OpenAICompatibleAdapter:
    transport = RecordingTransport(responses or [(200, OPENAI_OK)])
    adapter = OpenAICompatibleAdapter(
        api_key=SECRET, transport=transport, sleep=lambda _: None, **kwargs
    )
    adapter.test_transport = transport  # type: ignore[attr-defined]
    return adapter


class TestAnthropicAdapter(unittest.TestCase):
    def test_completes_and_parses(self):
        adapter = anthropic()
        response = adapter.complete("What is the capital of France?")
        self.assertEqual(response.text, "The answer is Paris.")
        self.assertEqual(response.finish_reason, "end_turn")
        self.assertEqual(response.request_id, "msg_01abc")
        self.assertEqual(response.usage["prompt_tokens"], 12)

    def test_posts_to_the_messages_endpoint(self):
        adapter = anthropic()
        adapter.complete("hello")
        call = adapter.test_transport.calls[0]
        self.assertTrue(call["url"].endswith("/v1/messages"))
        self.assertEqual(call["headers"]["anthropic-version"], "2023-06-01")

    def test_sends_the_prompt_as_a_user_message(self):
        adapter = anthropic()
        adapter.complete("hello")
        body = adapter.test_transport.calls[0]["body"]
        self.assertEqual(body["messages"], [{"role": "user", "content": "hello"}])

    def test_system_prompt_is_a_top_level_field(self):
        adapter = anthropic(system="be terse")
        adapter.complete("hello")
        self.assertEqual(adapter.test_transport.calls[0]["body"]["system"], "be terse")

    def test_per_call_system_overrides(self):
        adapter = anthropic(system="default")
        adapter.complete("hello", system="override")
        self.assertEqual(adapter.test_transport.calls[0]["body"]["system"], "override")

    def test_generation_parameters_are_sent(self):
        adapter = anthropic(params={"temperature": 0.3, "max_tokens": 64, "top_p": 0.9})
        adapter.complete("hello")
        body = adapter.test_transport.calls[0]["body"]
        self.assertEqual(body["temperature"], 0.3)
        self.assertEqual(body["max_tokens"], 64)
        self.assertEqual(body["top_p"], 0.9)

    def test_concatenates_multiple_text_blocks(self):
        payload = {
            "content": [
                {"type": "text", "text": "one "},
                {"type": "text", "text": "two"},
            ]
        }
        adapter = anthropic([(200, payload)])
        self.assertEqual(adapter.complete("x").text, "one two")

    def test_unexpected_shape_fails_loudly(self):
        # Silently returning "" would surface as a fascinating and entirely
        # fictitious finding.
        adapter = anthropic([(200, {"unexpected": True})])
        with self.assertRaises(AdapterError) as ctx:
            adapter.complete("x")
        self.assertIn("content", str(ctx.exception))


class TestOpenAIAdapter(unittest.TestCase):
    def test_completes_and_parses(self):
        response = openai().complete("What is the capital of France?")
        self.assertEqual(response.text, "The answer is Paris.")
        self.assertEqual(response.finish_reason, "stop")

    def test_posts_to_chat_completions(self):
        adapter = openai()
        adapter.complete("hello")
        self.assertTrue(
            adapter.test_transport.calls[0]["url"].endswith("/chat/completions")
        )

    def test_system_prompt_becomes_a_message(self):
        adapter = openai(system="be terse")
        adapter.complete("hello")
        messages = adapter.test_transport.calls[0]["body"]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "be terse"})
        self.assertEqual(messages[1]["role"], "user")

    def test_no_system_message_when_none_configured(self):
        adapter = openai()
        adapter.complete("hello")
        messages = adapter.test_transport.calls[0]["body"]["messages"]
        self.assertEqual(len(messages), 1)

    def test_base_url_is_configurable_for_compatible_servers(self):
        adapter = openai(base_url="http://localhost:8000/v1")
        adapter.complete("hello")
        self.assertEqual(
            adapter.test_transport.calls[0]["url"],
            "http://localhost:8000/v1/chat/completions",
        )

    def test_unexpected_shape_fails_loudly(self):
        adapter = openai([(200, {"choices": []})])
        with self.assertRaises(AdapterError):
            adapter.complete("x")

    def test_null_content_becomes_empty_string_not_none(self):
        payload = {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]}
        self.assertEqual(openai([(200, payload)]).complete("x").text, "")


class TestErrorsAndRetries(unittest.TestCase):
    def test_a_client_error_is_not_retried(self):
        adapter = anthropic([(400, {"error": {"message": "bad request"}})])
        with self.assertRaises(AdapterError) as ctx:
            adapter.complete("x")
        self.assertIn("400", str(ctx.exception))
        self.assertIn("bad request", str(ctx.exception))
        self.assertEqual(len(adapter.test_transport.calls), 1)

    def test_a_rate_limit_is_retried_then_succeeds(self):
        adapter = anthropic(
            [(429, {"error": {"message": "slow down"}}), (200, ANTHROPIC_OK)]
        )
        self.assertEqual(adapter.complete("x").text, "The answer is Paris.")
        self.assertEqual(len(adapter.test_transport.calls), 2)

    def test_retries_are_bounded(self):
        adapter = anthropic([(503, {"error": {"message": "unavailable"}})])
        with self.assertRaises(AdapterError):
            adapter.complete("x")
        self.assertEqual(len(adapter.test_transport.calls), 3)  # 1 + 2 retries

    def test_retryable_statuses_are_transient_ones_only(self):
        self.assertIn(429, RETRYABLE_STATUSES)
        self.assertIn(503, RETRYABLE_STATUSES)
        self.assertNotIn(400, RETRYABLE_STATUSES)
        self.assertNotIn(401, RETRYABLE_STATUSES)

    def test_non_json_success_body_is_an_error(self):
        adapter = anthropic([(200, b"<html>gateway</html>")])
        with self.assertRaises(AdapterError) as ctx:
            adapter.complete("x")
        self.assertIn("not JSON", str(ctx.exception))

    def test_backoff_sleeps_between_attempts(self):
        slept: List[float] = []
        transport = RecordingTransport([(429, {"error": "slow"}), (200, ANTHROPIC_OK)])
        adapter = AnthropicAdapter(
            api_key=SECRET, transport=transport, sleep=slept.append
        )
        adapter.complete("x")
        self.assertEqual(len(slept), 1)
        self.assertGreater(slept[0], 0)


class TestKeysAreNeverRendered(unittest.TestCase):
    """An evidence journal that captured a credential would be worse than none."""

    def _surfaces(self, adapter: HttpModelAdapter) -> List[str]:
        response = adapter.complete("hello")
        return [
            adapter.describe(),
            repr(adapter),
            json.dumps(adapter.fingerprint().to_dict()),
            json.dumps(response.raw),
            adapter.endpoint(),
        ]

    def test_anthropic_never_renders_the_key(self):
        for surface in self._surfaces(anthropic()):
            with self.subTest(surface=surface[:40]):
                self.assertNotIn(SECRET, surface)

    def test_openai_never_renders_the_key(self):
        for surface in self._surfaces(openai()):
            with self.subTest(surface=surface[:40]):
                self.assertNotIn(SECRET, surface)

    def test_the_key_is_absent_from_error_messages(self):
        adapter = anthropic([(401, {"error": {"message": "invalid key"}})])
        with self.assertRaises(AdapterError) as ctx:
            adapter.complete("x")
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_the_key_does_reach_the_request_headers(self):
        # The negative tests above would pass trivially if the key were never
        # used at all.
        adapter = anthropic()
        adapter.complete("x")
        self.assertEqual(adapter.test_transport.calls[0]["headers"]["x-api-key"], SECRET)

    def test_openai_sends_a_bearer_token(self):
        adapter = openai()
        adapter.complete("x")
        self.assertEqual(
            adapter.test_transport.calls[0]["headers"]["authorization"],
            f"Bearer {SECRET}",
        )


class TestBuildAdapter(unittest.TestCase):
    def test_defaults_to_the_offline_mock(self):
        adapter = build_adapter()
        self.assertIsInstance(adapter, MockAdapter)
        self.assertFalse(adapter.requires_network)
        assert_offline([adapter])

    def test_a_real_adapter_without_a_key_is_an_error_not_a_fallback(self):
        # Falling back would label evidence with an endpoint it never touched.
        with mock.patch.dict(os.environ, {}, clear=True):
            for name, env in (
                (ADAPTER_ANTHROPIC, ANTHROPIC_KEY_ENV),
                (ADAPTER_OPENAI, OPENAI_KEY_ENV),
            ):
                with self.subTest(adapter=name):
                    with self.assertRaises(ValueError) as ctx:
                        build_adapter(name)
                    self.assertIn(env, str(ctx.exception))

    def test_a_key_in_the_environment_builds_a_real_adapter(self):
        with mock.patch.dict(os.environ, {ANTHROPIC_KEY_ENV: SECRET}, clear=True):
            adapter = build_adapter(ADAPTER_ANTHROPIC, model="some-model")
        self.assertIsInstance(adapter, AnthropicAdapter)
        self.assertEqual(adapter.model, "some-model")
        self.assertTrue(adapter.requires_network)

    def test_unknown_adapter_lists_the_valid_names(self):
        with self.assertRaises(ValueError) as ctx:
            build_adapter("telepathy")
        self.assertIn("mock", str(ctx.exception))

    def test_mock_accepts_a_model_override(self):
        self.assertEqual(build_adapter(ADAPTER_MOCK, model="fixture-v3").model, "fixture-v3")


class TestAdapterContract(unittest.TestCase):
    def test_remote_adapters_declare_that_they_need_the_network(self):
        for adapter in (anthropic(), openai()):
            with self.subTest(adapter=adapter.name):
                self.assertTrue(adapter.requires_network)
                self.assertIsInstance(adapter, ModelAdapter)
                with self.assertRaises(AssertionError):
                    assert_offline([adapter])

    def test_fingerprints_distinguish_provider_and_model(self):
        self.assertNotEqual(
            anthropic().fingerprint().digest(), openai().fingerprint().digest()
        )
        self.assertNotEqual(
            anthropic(model="a").fingerprint().digest(),
            anthropic(model="b").fingerprint().digest(),
        )

    def test_an_empty_key_is_refused_at_construction(self):
        with self.assertRaises(ValueError) as ctx:
            AnthropicAdapter(api_key="")
        self.assertIn("opt-in", str(ctx.exception))

    def test_probes_run_against_a_remote_adapter_through_the_same_interface(self):
        from probes.consistency import ConsistencyCase, ConsistencyProbe

        adapter = anthropic()
        probe = ConsistencyProbe(
            cases=[
                ConsistencyCase(
                    id="c", paraphrases=tuple(f"ask {i}?" for i in range(20))
                )
            ]
        )
        [evidence] = probe.run(adapter)
        self.assertEqual(evidence.sample_size, 20)
        self.assertEqual(evidence.fingerprint.adapter, "anthropic")


if __name__ == "__main__":
    unittest.main()

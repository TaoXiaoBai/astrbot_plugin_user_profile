import asyncio
import time
import types
import unittest
from unittest.mock import AsyncMock

from age_model_retry import ModelCallFailure, error_code, generate_with_retry
from tests import test_age_protection as fixtures


class HTTPError(Exception):
    def __init__(self, status, text="upstream failure", delay=None):
        super().__init__(text)
        self.status_code = status
        self.response = types.SimpleNamespace(headers={} if delay is None else {"retry-after": str(delay)})


class RetryTests(unittest.IsolatedAsyncioTestCase):
    def context(self, responses):
        return types.SimpleNamespace(llm_generate=AsyncMock(side_effect=responses))

    def response(self, text="valid response"):
        return types.SimpleNamespace(completion_text=text)

    async def call(self, context, **config):
        return await generate_with_retry(context, "mock", "same evidence", {"age_protection_retry_delay": 0, **config}, time.monotonic() + 5)

    async def test_transient_errors_then_success(self):
        context = self.context([HTTPError(503), ConnectionError("temporary"), self.response()])
        response, stats = await self.call(context)
        self.assertEqual(response.completion_text, "valid response")
        self.assertEqual((stats["attempts"], stats["retries"]), (3, 2))
        self.assertEqual(context.llm_generate.await_count, 3)
        self.assertTrue(all(call.kwargs["prompt"] == "same evidence" for call in context.llm_generate.await_args_list))

    async def test_attempt_limit_and_disable(self):
        for extra, attempts in ((2, 3), (0, 1)):
            context = self.context([HTTPError(502)] * 5)
            with self.assertRaises(ModelCallFailure) as caught:
                await self.call(context, age_protection_retry_count=extra)
            self.assertEqual(context.llm_generate.await_count, attempts)
            self.assertEqual(caught.exception.details["retry_stop"], "attempt_limit")

    async def test_quota_auth_and_context_do_not_retry(self):
        for error, code in ((HTTPError(429, "Individual quota reached. PRIVATE_TOKEN"), "model_quota_exhausted"),
                            (HTTPError(401), "model_auth_error"), (HTTPError(400, "context_length_exceeded"), "model_context_limit")):
            context = self.context([error, self.response()])
            with self.assertRaises(ModelCallFailure) as caught:
                await self.call(context)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(context.llm_generate.await_count, 1)
            self.assertNotIn("PRIVATE_TOKEN", str(caught.exception.details))

    async def test_short_429_and_empty_retry(self):
        for first in (HTTPError(429, delay=0), self.response(" ")):
            context = self.context([first, self.response()])
            _, stats = await self.call(context)
            self.assertEqual(stats["retries"], 1)

    async def test_retry_after_and_total_budget_not_shortened(self):
        context = self.context([HTTPError(429, delay=90), self.response()])
        with self.assertRaises(ModelCallFailure) as caught:
            await self.call(context)
        self.assertEqual(caught.exception.details["retry_stop"], "retry_after_exceeds_budget")
        self.assertEqual(context.llm_generate.await_count, 1)
        context = self.context([HTTPError(503), self.response()])
        with self.assertRaises(ModelCallFailure) as caught:
            await generate_with_retry(context, "mock", "evidence", {"age_protection_retry_delay": 2}, time.monotonic() + .1)
        self.assertEqual(caught.exception.details["retry_stop"], "total_budget")
        self.assertEqual(context.llm_generate.await_count, 1)

    async def test_stale_and_cancellation_stop_retry(self):
        current = True
        async def fail(**kwargs):
            nonlocal current
            current = False
            raise HTTPError(503)
        context = types.SimpleNamespace(llm_generate=AsyncMock(side_effect=fail))
        with self.assertRaises(ModelCallFailure) as caught:
            await generate_with_retry(context, "mock", "evidence", {"age_protection_retry_delay": 0}, time.monotonic() + 5, is_current=lambda: current)
        self.assertEqual(caught.exception.code, "assessment_superseded")
        self.assertEqual(context.llm_generate.await_count, 1)
        entered = asyncio.Event()
        async def slow(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        context = types.SimpleNamespace(llm_generate=AsyncMock(side_effect=slow))
        task = asyncio.create_task(self.call(context))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(context.llm_generate.await_count, 1)

    async def test_unknown_and_adult_response_never_rerolled(self):
        for text in ('{"status":"unknown"}', '{"status":"ready","suspected_minor":false}'):
            context = self.context([self.response(text), self.response("different")])
            response, stats = await self.call(context)
            self.assertEqual(response.completion_text, text)
            self.assertEqual(stats["attempts"], 1)

    async def test_real_assessment_retries_and_failure_diagnostics(self):
        fixture = fixtures.AgeProtectionTests()
        plugin = fixture.make_plugin(history_scan_enabled=False, age_protection_use_qq_age=False, age_protection_retry_delay=0)
        calls = 0
        async def transient(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise HTTPError(503)
            return await fixture.reply(**kwargs)
        plugin.context.llm_generate.side_effect = transient
        result = await plugin.assess_age_protection("11111", fixture.event())
        self.assertTrue(result["decision_eligible"])
        self.assertEqual(result["diagnostics"]["model"]["attempts"], 2)
        self.assertEqual(result["diagnostics"]["model"]["retries"], 1)
        plugin.context.llm_generate.side_effect = HTTPError(429, "Individual quota reached. SECRET")
        failure = await plugin.assess_age_protection("11111", fixture.event("我今年15岁"))
        self.assertFalse(failure["decision_eligible"])
        self.assertEqual(failure["diagnostics"]["decision"]["reasons"], ["model_quota_exhausted"])
        self.assertNotIn("SECRET", str(failure))

    def test_classification_never_exposes_raw_text(self):
        self.assertEqual(error_code(asyncio.TimeoutError()), "model_timeout")
        self.assertEqual(error_code(ConnectionError("secret URL")), "model_connection_error")
        self.assertEqual(error_code(RuntimeError("unclassified secret")), "model_error")

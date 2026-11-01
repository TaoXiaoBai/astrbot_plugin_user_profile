"""Age-review transport retries, bounded by the existing overall deadline."""
import asyncio
import math
import random
import re
import time


RETRYABLE = frozenset({"model_rate_limited", "model_server_error", "model_connection_error",
                       "model_empty_response", "model_timeout"})


def error_code(exc):
    status = getattr(exc, "status_code", None)
    # Inspect only for classification; never expose or persist exception text/headers.
    try:
        text = str(exc)[:4000].lower()
    except Exception:
        text = ""
    if any(word in text for word in ("individual quota reached", "insufficient_quota", "quota exhausted",
                                    "quota exceeded", "insufficient balance", "credit balance", "billing_hard_limit")):
        return "model_quota_exhausted"
    if status in (401, 403) or re.search(r"(?:error code|http|status)[ :]*(?:401|403)\b", text):
        return "model_auth_error"
    if any(word in text for word in ("context_length_exceeded", "maximum context length", "context window", "input too long")):
        return "model_context_limit"
    if status == 429 or re.search(r"(?:error code|http|status)[ :]*429\b", text):
        return "model_rate_limited"
    if type(status) is int and 500 <= status <= 599:
        return "model_server_error"
    if re.search(r"(?:error code|http|status)[ :]*5\d\d\b", text):
        return "model_server_error"
    name = type(exc).__name__.lower()
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in name:
        return "model_timeout"
    if isinstance(exc, ConnectionError) or any(word in name for word in ("connection", "network", "connecterror")):
        return "model_connection_error"
    if "empty" in name and ("output" in name or "response" in name):
        return "model_empty_response"
    return "model_error"


def retry_after(exc):
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        value = headers.get("retry-after") if headers is not None else None
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


class ModelCallFailure(Exception):
    def __init__(self, code, details):
        super().__init__(code)
        self.code = code
        self.details = dict(details)


async def generate_with_retry(context, provider, prompt, config, deadline, *, is_current=lambda: True, progress=None):
    """Unknown/valid negative judgments are never rerolled; only transport failures retry."""
    extra = config.get("age_protection_retry_count", 2)
    extra = extra if type(extra) is int and 0 <= extra <= 4 else 2
    base = config.get("age_protection_retry_delay", 2)
    base = base if type(base) in (int, float) and math.isfinite(base) and 0 <= base <= 30 else 2
    details = dict(attempts=0, retries=0, max_attempts=extra + 1, retry_delays=[], last_error_code="", retry_stop="")
    if progress is not None:
        progress.update(details)
    for attempt in range(extra + 1):
        if not is_current():
            raise ModelCallFailure("assessment_superseded", details)
        if deadline - time.monotonic() <= 0:
            details["retry_stop"] = "total_budget"
            raise ModelCallFailure("model_timeout", details)
        details.update(attempts=attempt + 1, retries=attempt)
        if progress is not None:
            progress.update(details)
        try:
            response = await context.llm_generate(chat_provider_id=provider, prompt=prompt)
            completion = getattr(response, "completion_text", None)
            if isinstance(completion, str) and not completion.strip():
                raise ModelCallFailure("model_empty_response", details)
            return response, dict(details)
        except ModelCallFailure as exc:
            code, delay_header = exc.code, None
        except Exception as exc:
            code, delay_header = error_code(exc), retry_after(exc)
        details["last_error_code"] = code
        if code not in RETRYABLE or attempt == extra:
            details["retry_stop"] = "not_retryable" if code not in RETRYABLE else "attempt_limit"
            raise ModelCallFailure(code, details) from None
        delay = min(30.0, base * (2 ** attempt) + random.uniform(0, min(1.0, base * .25)))
        # A Retry-After beyond our cap/deadline must not be shortened into a hammering loop.
        if delay_header is not None:
            if delay_header > 30:
                details["retry_stop"] = "retry_after_exceeds_budget"
                raise ModelCallFailure(code, details) from None
            delay = max(delay, delay_header)
        if deadline - time.monotonic() <= delay + .1:
            details["retry_stop"] = "total_budget"
            raise ModelCallFailure(code, details) from None
        details["retry_delays"].append(round(delay, 3))
        if progress is not None:
            progress.update(details)
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")

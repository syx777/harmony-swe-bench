from __future__ import annotations
from typing import Callable, Dict, List, Optional, Tuple, TypeVar
from abc import ABC, abstractmethod
import os
import random
import time
import pydantic
import yaml

_T = TypeVar("_T")


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    """Extract a Retry-After value (seconds) from a 429/503 response, if present."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) or {}
    val = headers.get("retry-after") or headers.get("Retry-After")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with full jitter: random in [0, min(cap, base*2^attempt)]."""
    ceiling = min(cap, base * (2 ** attempt))
    return random.uniform(0, ceiling)


class LLMConfig(pydantic.BaseModel):
    provider: str
    model: str
    base_url: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 4096
    thinking_budget: Optional[int] = None
    max_retries: int = 4
    retry_base_delay: float = 2.0
    retry_max_delay: float = 30.0
    api_key_env: Dict[str, str] = {}


def load_config(path: str) -> LLMConfig:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return LLMConfig(**data)


class LLMClient(ABC):
    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        ...

    def complete_with_reasoning(self, system: str, user: str) -> Tuple[str, str]:
        # Default: no separate reasoning channel. Reasoning models override this.
        return self.complete(system, user), ""


class MockLLMClient(LLMClient):
    def __init__(self, responses: List[str], reasonings: Optional[List[str]] = None) -> None:
        self._responses = responses
        self._reasonings = reasonings
        self._i = 0

    def complete(self, system: str, user: str) -> str:
        r = self._responses[self._i % len(self._responses)]
        self._i += 1
        return r

    def complete_with_reasoning(self, system: str, user: str) -> Tuple[str, str]:
        idx = self._i
        content = self.complete(system, user)
        reasoning = ""
        if self._reasonings:
            reasoning = self._reasonings[idx % len(self._reasonings)]
        return content, reasoning


def _message_content(msg) -> str:
    """Best-effort text from a response. Some gateways route code into a `Write`
    tool_use block instead of a text block, so fall back to the largest string
    payload of any tool_use input (content/file_text/text/code)."""
    joined = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    best = joined
    for b in msg.content:
        if getattr(b, "type", "") == "tool_use":
            inp = getattr(b, "input", None)
            if isinstance(inp, dict):
                for k in ("content", "file_text", "text", "code"):
                    v = inp.get(k)
                    if isinstance(v, str) and len(v) > len(best):
                        best = v
    return best


class AnthropicClient(LLMClient):
    def __init__(self, cfg: LLMConfig, api_key: str) -> None:
        import anthropic
        kwargs = {}
        base = cfg.base_url or os.environ.get("ANTHROPIC_BASE_URL")
        if base:
            kwargs["base_url"] = base
        # Some Anthropic-compatible gateways authenticate with a Bearer token
        # (ANTHROPIC_AUTH_TOKEN), not an x-api-key. Prefer auth_token when present.
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if auth_token:
            kwargs["auth_token"] = auth_token
        else:
            kwargs["api_key"] = api_key
        self._client = anthropic.Anthropic(**kwargs)
        self._cfg = cfg

    def complete(self, system: str, user: str) -> str:
        last: Optional[Exception] = None
        # This gateway forces adaptive thinking + server-side tools by default, which
        # can yield an empty text block (the model routes into a tool_use turn).
        # Disabling thinking keeps the answer in the text block.
        extra = {"thinking": {"type": "disabled"}}
        for attempt in range(self._cfg.max_retries + 1):
            try:
                msg = self._client.messages.create(
                    model=self._cfg.model, max_tokens=self._cfg.max_tokens,
                    temperature=self._cfg.temperature, system=system,
                    messages=[{"role": "user", "content": user}],
                    extra_body=extra,
                )
                text = _message_content(msg)
                if text.strip():
                    return text
                last = RuntimeError("empty completion")
            except Exception as exc:  # rate-limit / timeout / transient gateway errors
                last = exc
            if attempt < self._cfg.max_retries:
                # This gateway enforces a tight per-minute cap; floor the wait so a
                # handful of retries reliably spans a fresh minute window.
                delay = _retry_after_seconds(last) or _backoff_delay(
                    attempt, self._cfg.retry_base_delay, self._cfg.retry_max_delay)
                time.sleep(max(delay, 12.0))
        raise RuntimeError(f"anthropic completion failed after retries: {last}")


class OpenAICompatClient(LLMClient):
    def __init__(self, cfg: LLMConfig, api_key: str) -> None:
        import openai
        kwargs = {"api_key": api_key, "timeout": 60.0, "max_retries": 0}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self._client = openai.OpenAI(**kwargs)
        self._cfg = cfg

    def _create(self, system: str, user: str):
        kwargs = dict(
            model=self._cfg.model, temperature=self._cfg.temperature,
            max_tokens=self._cfg.max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        if self._cfg.thinking_budget:
            # Reasoning models (kimi/glm/deepseek on OpenAI-compatible gateways)
            # accept a thinking budget via extra_body.
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled", "budget_tokens": self._cfg.thinking_budget}
            }
        return self._client.chat.completions.create(**kwargs)

    def _sleep(self, seconds: float) -> None:
        # Indirection so tests can patch sleeping without real delays.
        time.sleep(seconds)

    def _call_with_retry(self, attempt_fn: Callable[[], _T]) -> _T:
        # Gateways intermittently hang (client timeout), rate-limit (429), or
        # return an empty completion. Retry with exponential backoff + jitter,
        # honoring a Retry-After header when the gateway sends one (e.g. 429).
        last_err: Optional[Exception] = None
        for attempt in range(self._cfg.max_retries):
            try:
                result = attempt_fn()
                if result is not None:
                    return result
                last_err = RuntimeError("empty content")
            except Exception as exc:  # network/timeout/429/5xx are retryable
                last_err = exc
            if attempt < self._cfg.max_retries - 1:
                hinted = _retry_after_seconds(last_err) if isinstance(last_err, Exception) else None
                delay = hinted if hinted is not None else _backoff_delay(
                    attempt, self._cfg.retry_base_delay, self._cfg.retry_max_delay)
                self._sleep(delay)
        raise RuntimeError(f"LLM call failed after {self._cfg.max_retries} retries: {last_err}")

    def complete(self, system: str, user: str) -> str:
        def attempt() -> Optional[str]:
            resp = self._create(system, user)
            text = resp.choices[0].message.content or ""
            return text if text.strip() else None
        return self._call_with_retry(attempt)

    def complete_with_reasoning(self, system: str, user: str) -> Tuple[str, str]:
        def attempt() -> Optional[Tuple[str, str]]:
            resp = self._create(system, user)
            msg = resp.choices[0].message
            text = msg.content or ""
            reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None) or ""
            return (text, reasoning) if text.strip() else None
        return self._call_with_retry(attempt)


def build_client(cfg: LLMConfig) -> LLMClient:
    env_name = cfg.api_key_env.get(cfg.provider)
    key = os.environ.get(env_name) if env_name else None
    if not key and cfg.provider == "anthropic":
        # Some Anthropic-compatible gateways supply a Bearer token instead of an x-api-key.
        key = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not key:
        raise RuntimeError(f"Missing API key in env var {env_name!r} for provider {cfg.provider!r}")
    if cfg.provider == "anthropic":
        return AnthropicClient(cfg, key)
    if cfg.provider == "openai":
        return OpenAICompatClient(cfg, key)
    raise RuntimeError(f"Unknown provider: {cfg.provider}")

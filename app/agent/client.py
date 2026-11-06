"""OpenRouter chat client, with a seam for tests.

OpenRouter speaks the OpenAI chat-completions dialect, so this is a thin
`httpx` wrapper rather than a dependency on an SDK. Two behaviours matter:

- **Fallback is explicit.** A free model that is rate-limited is the normal
  case, not the exceptional one, so a second model is tried before giving up.
- **Failure is loud but harmless.** Every failure path raises
  `LLMUnavailable`, which the loop turns into the deterministic template. An
  explanation is never worth a 500.

`transport` and `sleep` are injectable so the whole loop can be tested without
a network or a key.

The timeout is deliberately short. A request that waits on a slow free model is
a request the user is staring at, and the deterministic template is already good
enough to ship — so a slow model loses to a fast fallback. Worst case is two
models at the timeout plus one short pause, about a minute, before the template
is served.

The token budget is not a style choice. A reasoning model spends `max_tokens` on
its thinking before it writes anything, and this one was measured spending 515
of 900 on reasoning and then running out of room mid-JSON. A truncated answer is
worse than a short one: it parses as nothing and the guardrail never sees it.
2000 leaves room for the reasoning plus the full explanation object, and a
truncated reply is now reported as truncated rather than as "no answer".
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class LLMUnavailable(RuntimeError):
    """No model could answer. The caller falls back to the template."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    parse_error: str | None = None


@dataclass
class ChatResult:
    content: str
    tool_calls: list
    model: str
    finish_reason: str = ""


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        fallback_model: str | list[str] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        transport=None,
        timeout: float = 30.0,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        sleep=time.sleep,
        on_request=None,
        clock=time.monotonic,
        cooldown: float = 60.0,
    ):
        self._api_key = (api_key or "").strip()
        # A chain, not a single substitute. A model id can be retired by the
        # provider at any time, and the deployment's environment can keep
        # advertising the retired one long after the code has moved on — which is
        # exactly what happened: the service's `LLM_FALLBACK_MODEL` still named a
        # model that returns 404, so every rate-limited primary became a
        # template. Carrying more than one candidate means a stale environment
        # costs one wasted request instead of the whole feature.
        fallbacks = (
            [fallback_model]
            if isinstance(fallback_model, str)
            else list(fallback_model or [])
        )
        self._models = [m for m in [model, *fallbacks] if m]
        self._dead: set[str] = set()
        # A model that just timed out or 503'd is usually still failing a second
        # later. One explanation is several requests, so without this the same
        # dead primary is paid for again on every step of the same answer — which
        # is how an explanation measured 99 seconds while the working model was
        # answering in a few. Cooled-down models are skipped only while another
        # candidate remains, so this can delay a request but never refuse one.
        self._cooldown_until: dict[str, float] = {}
        self._cooldown = cooldown
        self._clock = clock
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._sleep = sleep
        # Called once per HTTP request, before it is sent — the only honest place
        # to count what an explanation costs. A tool round trip, a fallback and a
        # repair are all real requests, and a budget that counted explanations
        # instead would be a guess about them.
        self._on_request = on_request

    @property
    def available(self) -> bool:
        return bool(self._api_key and self._models)

    @property
    def models(self) -> list:
        return list(self._models)

    def chat(self, messages: list, tools: list | None = None) -> ChatResult:
        if not self.available:
            raise LLMUnavailable("no OpenRouter key or model configured")

        now = self._clock()
        # Skip what is known broken, but never skip everything: a cooldown is a
        # preference, not a refusal, so if every candidate is cooling down the
        # request still goes out and the provider gets the last word.
        candidates = [
            m for m in self._models
            if m not in self._dead and self._cooldown_until.get(m, 0.0) <= now
        ] or [m for m in self._models if m not in self._dead]

        failures = []
        for index, model in enumerate(candidates):
            if index:
                # A free tier that just 429'd is usually still 429ing a second
                # later. A short pause is cheaper than burning both models.
                self._sleep(0.8)
            try:
                return self._call(model, messages, tools)
            except LLMUnavailable as exc:
                # A 404 means the id does not exist. Retrying it cannot start
                # working, so it is remembered for the life of the process.
                if "HTTP 404" in str(exc):
                    self._dead.add(model)
                else:
                    # Everything else may be transient, so it is only rested.
                    self._cooldown_until[model] = self._clock() + self._cooldown
                failures.append(f"{model}: {exc}")

        raise LLMUnavailable("; ".join(failures) or "every configured model is retired")

    def _call(self, model: str, messages: list, tools: list | None) -> ChatResult:
        body = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://feelens.local",
            "X-Title": "FeeLens",
        }

        try:
            if self._on_request is not None:
                self._on_request()
            with httpx.Client(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions", json=body, headers=headers
                )
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"transport error ({type(exc).__name__})") from exc

        if response.status_code != 200:
            raise LLMUnavailable(f"HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMUnavailable("response was not JSON") from exc

        # OpenRouter returns 200 with an error body when an upstream provider is
        # saturated ("ResourceExhausted: Worker local total request limit
        # reached"). Reporting that as "not a chat completion" would throw away
        # the only useful sentence in the response and make a provider problem
        # look like a parsing bug.
        if isinstance(data, dict) and data.get("error"):
            detail = data["error"]
            text = detail.get("message") if isinstance(detail, dict) else str(detail)
            raise LLMUnavailable(f"upstream error: {text or 'unspecified'}")

        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable("response was not a chat completion") from exc

        calls = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            name = function.get("name") or ""
            raw_args = function.get("arguments")
            arguments, error = {}, None
            if isinstance(raw_args, dict):
                arguments = raw_args
            elif isinstance(raw_args, str) and raw_args.strip():
                try:
                    parsed = json.loads(raw_args)
                except ValueError:
                    error = f"arguments were not valid JSON: {raw_args[:120]!r}"
                else:
                    if isinstance(parsed, dict):
                        arguments = parsed
                    else:
                        error = "arguments were JSON but not an object"
            calls.append(
                ToolCall(
                    id=raw.get("id") or f"call_{len(calls)}",
                    name=name,
                    arguments=arguments,
                    parse_error=error,
                )
            )

        return ChatResult(
            content=(message.get("content") or "").strip(),
            tool_calls=calls,
            model=data.get("model") or model,
            finish_reason=choice.get("finish_reason") or "",
        )

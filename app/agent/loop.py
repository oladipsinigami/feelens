"""The tool loop: give the model tools, take its answer, then check it.

Shape of a run:

    system + user  ->  model  ->  tool calls  ->  tool results  ->  model
                   ->  JSON answer  ->  guardrail  ->  (repair)  ->  answer

The loop has a hard step limit, and every exit that is not "validated model
answer" ends in the deterministic template. There is no path through this file
that returns unvalidated prose, and no path that raises because a free model was
busy. An explanation is a convenience; the numbers are the product.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from app.agent.client import LLMUnavailable
from app.agent.guardrails import validate
from app.agent.prompts import REPAIR_PROMPT, SYSTEM_PROMPT, user_prompt
from app.agent.template import template_explanation
from app.agent.tools import TOOL_SCHEMAS

MODEL = "model"
TEMPLATE = "template"


@dataclass
class Explanation:
    text: str
    cited_rules: list
    caveats: list
    source: str
    checks: dict
    trace: list = field(default_factory=list)
    model: str = ""
    note: str = ""
    cached: bool = False

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "cited_rules": list(self.cited_rules),
            "caveats": list(self.caveats),
            "source": self.source,
            "checks": dict(self.checks),
            "trace": list(self.trace),
            "model": self.model,
            "note": self.note,
            "cached": self.cached,
        }


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a reply, tolerating code fences."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _stop_note(result) -> str:
    """Why the model produced nothing usable, in the model's own vocabulary.

    `finish_reason` is the difference between "the provider returned an empty
    message" and "the reply was cut off at the token budget". Both end in the
    template, but only one of them is fixed by raising `max_tokens`, so the note
    has to say which one happened.
    """
    if result is None:
        return "the model returned no answer"
    reason = (result.finish_reason or "").strip()
    if reason == "length":
        return (
            "the model ran out of tokens before it finished answering "
            "(finish_reason: length)"
        )
    if reason:
        return f"the model returned no answer (finish_reason: {reason})"
    return "the model returned no answer"


class ExplanationAgent:
    def __init__(
        self,
        tools,
        client=None,
        max_steps: int = 4,
        max_repairs: int = 1,
        cache_size: int = 256,
    ):
        self._tools = tools
        self._client = client
        self._max_steps = max_steps
        self._max_repairs = max_repairs
        # A free model account gets 50 requests a day, and one explanation costs
        # two or three. The same quote asked twice is the same explanation, so
        # the second one costs nothing — which is most of a demo. Only validated
        # model answers are stored; a template is cheap to rebuild and should
        # not stop a later request from trying the model again.
        self._cache: dict = {}
        self._cache_size = cache_size

    def _cache_key(self, amount, corridor, payment, question):
        snapshot_date = ""
        try:
            snapshot_date = self._tools.get_fx_snapshot()["snapshot_date"]
        except Exception:
            # A cache key without the rate date is still correct within a
            # process: the snapshot only changes on restart.
            pass
        return (str(amount), corridor, payment, (question or "").strip(), snapshot_date)

    @property
    def client(self):
        return self._client

    @property
    def model_ready(self) -> bool:
        return bool(self._client is not None and getattr(self._client, "available", False))

    def explain(
        self,
        amount: str,
        corridor: str,
        payment: str,
        question: str | None = None,
    ) -> Explanation:
        quote = self._tools.get_quote(str(amount), corridor, payment)
        if "error" in quote:
            # The request itself is unpricable. Say so; do not explain nothing.
            return Explanation(
                text=quote["error"],
                cited_rules=[],
                caveats=[],
                source=TEMPLATE,
                checks={"ok": False, "violations": [quote["error"]]},
                trace=[],
                note="request could not be priced",
            )

        if not self.model_ready:
            # A question with no model is not a request that failed, it is one
            # that was never attempted. Saying so beats returning a generic
            # explanation that reads as an answer to what was asked.
            note = "no model configured"
            if question and question.strip():
                note = "no model configured; the question was not answered"
            return self._from_template(quote, note=note)

        key = self._cache_key(amount, corridor, payment, question)
        remembered = self._cache.get(key)
        if remembered is not None:
            return replace(remembered, cached=True)

        start = len(self._tools.calls)
        try:
            explanation = self._run_model(quote, amount, corridor, payment, question, start)
        except LLMUnavailable as exc:
            note = f"model unavailable: {exc}"
            if question and question.strip():
                note = f"{note}; the question was not answered"
            return self._from_template(quote, note=note, start=start)

        if explanation.source == MODEL:
            self._remember(key, explanation)
        return explanation

    def _remember(self, key, explanation) -> None:
        """Store a validated answer, dropping the oldest when the cache is full.

        Only model answers are stored. A template costs nothing to rebuild and
        caching it would keep the page on the template after the model came
        back — the one case where retrying is worth a request.
        """
        if len(self._cache) >= self._cache_size:
            for stale in list(self._cache)[: max(1, self._cache_size // 8)]:
                self._cache.pop(stale, None)
        self._cache[key] = explanation

    # -- model path -------------------------------------------------------

    def _run_model(self, quote, amount, corridor, payment, question, start) -> Explanation:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt(amount, corridor, payment, question)},
        ]
        seen: list = []

        result = None
        for _ in range(self._max_steps):
            result = self._client.chat(messages, TOOL_SCHEMAS)

            if not result.tool_calls:
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": result.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in result.tool_calls
                    ],
                }
            )

            for call in result.tool_calls:
                if call.parse_error:
                    payload = {"error": call.parse_error}
                else:
                    payload = self._tools.dispatch(call.name, call.arguments)
                seen.append(payload)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(payload),
                    }
                )
        else:
            # Ran out of steps with the model still calling tools.
            return self._from_template(
                quote, note="the model did not finish within the step limit", start=start
            )

        if result is None or not result.content:
            return self._from_template(quote, note=_stop_note(result), start=start)

        # A model that never called a tool has no numbers, and the guardrail
        # will say so — but name the cause here so the trace is readable.
        if not seen:
            return self._from_template(
                quote, note="the model answered without consulting a tool", start=start
            )

        answer = _extract_json(result.content)
        if answer is None and result.finish_reason == "length":
            # Asking again with the same budget would truncate in the same
            # place, and cost a request to prove it. The truncation is the
            # diagnosis; the template is the answer.
            return self._from_template(
                quote,
                note=(
                    "the model's reply was cut off at the token limit before "
                    "the JSON closed (finish_reason: length)"
                ),
                start=start,
            )
        if answer is None:
            answer = self._repair(
                messages, result.content, ["the reply was not the required JSON object"]
            )

        verdict = validate(answer, seen) if answer is not None else None

        repairs = 0
        while verdict is not None and not verdict.ok and repairs < self._max_repairs:
            repairs += 1
            answer = self._repair(messages, json.dumps(answer), verdict.violations)
            verdict = validate(answer, seen) if answer is not None else None

        if verdict is None or not verdict.ok:
            note = (
                "the model's answer failed validation: "
                + "; ".join((verdict.violations if verdict else ["no answer"]))
            )
            return self._from_template(quote, note=note, start=start)

        return Explanation(
            text=answer["explanation"].strip(),
            cited_rules=list(answer.get("cited_rules") or []),
            caveats=[str(item) for item in answer.get("caveats") or []],
            source=MODEL,
            checks=verdict.as_dict(),
            trace=self._trace(start),
            model=result.model,
        )

    def _repair(self, messages: list, previous: str, violations: list) -> dict | None:
        messages.append({"role": "assistant", "content": previous})
        messages.append(
            {
                "role": "user",
                "content": REPAIR_PROMPT.format(
                    violations="\n".join(f"- {item}" for item in violations)
                ),
            }
        )
        try:
            repaired = self._client.chat(messages, None)
        except LLMUnavailable:
            return None
        return _extract_json(repaired.content)

    # -- template path ----------------------------------------------------

    def _from_template(self, quote, note: str, start: int | None = None) -> Explanation:
        answer = template_explanation(quote)
        verdict = validate(answer, [quote])
        return Explanation(
            text=answer["explanation"],
            cited_rules=answer["cited_rules"],
            caveats=answer["caveats"],
            source=TEMPLATE,
            checks=verdict.as_dict(),
            trace=self._trace(start) if start is not None else [],
            note=note,
        )

    def _trace(self, start: int) -> list:
        return [
            {"name": call["name"], "arguments": call["arguments"]}
            for call in self._tools.calls[start:]
        ]

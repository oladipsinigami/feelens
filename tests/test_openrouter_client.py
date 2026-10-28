"""What the OpenRouter client does with responses that are not answers.

The interesting failures are not `HTTP 500`. They are the 200s that carry no
completion: OpenRouter returns `200` with an `{"error": ...}` body when an
upstream provider is saturated, and free models are saturated often. The first
version of this client reported that as "response was not a chat completion",
which made a provider problem look like a parsing bug and hid the only useful
sentence in the response.

Everything here runs against `httpx.MockTransport`, so no key and no network.
"""

import json

import httpx
import pytest

from app.agent.client import LLMUnavailable, OpenRouterClient

MESSAGES = [{"role": "user", "content": "price this"}]


def client(handler, model="primary/model:free", fallback="fallback/model:free"):
    return OpenRouterClient(
        "test-key", model, fallback, transport=httpx.MockTransport(handler)
    )


def completion(content="ok", tool_calls=None):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": content,
                                "tool_calls": tool_calls or []},
                    "finish_reason": "stop",
                }
            ],
            "model": "primary/model:free",
        },
    )


def test_a_normal_answer_is_parsed():
    result = client(lambda request: completion("445.93")).chat(MESSAGES)

    assert result.content == "445.93"
    assert result.tool_calls == []


def test_tool_calls_are_parsed_with_their_arguments():
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_quote", "arguments": json.dumps({"amount": "5000.00"})},
    }
    result = client(lambda request: completion("", [call])).chat(MESSAGES)

    assert result.tool_calls[0].name == "get_quote"
    assert result.tool_calls[0].arguments == {"amount": "5000.00"}


def test_an_upstream_error_body_is_reported_as_an_upstream_error():
    """The real shape: 200, no choices, an error naming the provider's problem."""
    body = {
        "id": "gen-1",
        "error": {
            "message": "Upstream error from Nvidia: ResourceExhausted: Worker local total request limit reached",
            "code": 502,
        },
    }
    with pytest.raises(LLMUnavailable) as caught:
        client(lambda request: httpx.Response(200, json=body)).chat(MESSAGES)

    message = str(caught.value)
    assert "upstream error" in message
    assert "ResourceExhausted" in message
    assert "not a chat completion" not in message


def test_an_error_body_that_is_a_bare_string_is_still_reported():
    body = {"error": "provider offline"}
    with pytest.raises(LLMUnavailable) as caught:
        client(lambda request: httpx.Response(200, json=body)).chat(MESSAGES)

    assert "provider offline" in str(caught.value)


def test_a_body_that_is_not_json_says_so():
    with pytest.raises(LLMUnavailable) as caught:
        client(lambda request: httpx.Response(200, text="<html>gateway</html>")).chat(MESSAGES)

    assert "not JSON" in str(caught.value)


def test_a_json_body_without_choices_is_not_silently_treated_as_empty_prose():
    with pytest.raises(LLMUnavailable) as caught:
        client(lambda request: httpx.Response(200, json={"id": "gen-1"})).chat(MESSAGES)

    assert "not a chat completion" in str(caught.value)


def test_a_rate_limit_is_reported_with_its_status():
    with pytest.raises(LLMUnavailable) as caught:
        client(lambda request: httpx.Response(429, json={"error": "slow down"})).chat(MESSAGES)

    assert "HTTP 429" in str(caught.value)


def test_the_finish_reason_survives_into_the_result():
    """The loop needs it to tell truncation from an empty answer."""
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": '{"explanation": "cut'},
                     "finish_reason": "length"}
                ],
                "model": "primary/model:free",
            },
        )

    result = client(handler).chat(MESSAGES)

    assert result.finish_reason == "length"


def test_the_token_budget_leaves_room_for_reasoning():
    """A reasoning model spends `max_tokens` thinking before it writes.

    Measured: 515 of 900 tokens on reasoning, then the JSON was cut off. A
    budget that only fits the answer is a budget that produces nothing, because
    a half-written JSON object parses as nothing at all.
    """
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return completion("ok")

    client(handler).chat(MESSAGES)

    assert seen["max_tokens"] >= 2000


def test_the_fallback_is_tried_and_the_reason_is_kept():
    """Both failures must survive into the message; the page shows it."""
    seen = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "primary/model:free":
            return httpx.Response(429, json={"error": "rate limited"})
        return completion("from the fallback")

    result = client(handler).chat(MESSAGES)

    assert seen == ["primary/model:free", "fallback/model:free"]
    assert result.content == "from the fallback"


def test_when_both_models_fail_the_error_names_both():
    def handler(request):
        model = json.loads(request.content)["model"]
        return httpx.Response(503 if "primary" in model else 502, json={"error": "down"})

    with pytest.raises(LLMUnavailable) as caught:
        client(handler).chat(MESSAGES)

    message = str(caught.value)
    assert "primary/model:free" in message
    assert "fallback/model:free" in message

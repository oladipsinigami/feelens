"""The three places a model name is written must agree, and be two models.

A wrong model name here does not fail loudly. The primary works, so the page
works; the fallback only runs when the primary is rate-limited — which on a free
tier is most of the time — and then it 404s and every explanation becomes the
template while `/api/health` still says a model is configured.

That is exactly what happened: `qwen/qwen3.8-27b:free` was in `.env`,
`.env.example` and `render.yaml`, and OpenRouter has never listed it. These tests
catch the offline half of that (drift between the files); `scripts/check_models.py`
catches the online half (a name that does not exist).
"""

from pathlib import Path

from app.agent.client import OpenRouterClient
from app.main import DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL

ROOT = Path(__file__).resolve().parent.parent


def test_primary_and_fallback_are_different_models():
    """A fallback that is the primary is not a fallback: it fails with it."""
    assert DEFAULT_MODEL != DEFAULT_FALLBACK_MODEL


def test_the_blueprint_deploys_the_models_the_code_defaults_to():
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert DEFAULT_MODEL in text
    assert DEFAULT_FALLBACK_MODEL in text


def test_the_example_env_names_the_models_the_code_defaults_to():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert DEFAULT_MODEL in text
    assert DEFAULT_FALLBACK_MODEL in text


def test_the_client_actually_holds_both_models_in_order():
    client = OpenRouterClient("test-key", DEFAULT_MODEL, DEFAULT_FALLBACK_MODEL)

    assert client.models == [DEFAULT_MODEL, DEFAULT_FALLBACK_MODEL]
    assert client.available is True


def test_no_key_means_no_client_and_no_pretence_of_one():
    client = OpenRouterClient("", DEFAULT_MODEL, DEFAULT_FALLBACK_MODEL)

    assert client.available is False


def test_the_fallback_survives_being_absent():
    """One model is a valid configuration; it is just less resilient."""
    client = OpenRouterClient("test-key", DEFAULT_MODEL, None)

    assert client.models == [DEFAULT_MODEL]

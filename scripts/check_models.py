"""Check that the configured models exist, are free, and can call tools.

This exists because of a real defect: the fallback was `qwen/qwen3.8-27b:free`,
which OpenRouter does not list. Nothing failed loudly. The primary model was
fine, so the page worked; the fallback only runs when the primary is
rate-limited, and then it 404s, `LLMUnavailable` is raised, and every explanation
becomes the template while `/api/health` still reports `model_configured: true`.
That is the worst kind of bug: invisible until the demo.

Three things are checked, because a model can exist and still be wrong here:

- **exists** — a 404 is a silent template.
- **free** — a `:free` suffix that starts billing is a bill.
- **tools** — the agent's contract is that the model calls tools and never
  invents a figure. A model that cannot call tools will answer without the
  payloads, fail the guardrail, and be replaced by the template every time.

No API key is needed: OpenRouter publishes the model list publicly.

    python scripts/check_models.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODELS_URL = "https://openrouter.ai/api/v1/models"


def configured() -> list[tuple[str, str]]:
    """What the app would actually use, env first then the code defaults."""
    from app.main import DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL

    primary = (os.environ.get("LLM_MODEL") or "").strip() or DEFAULT_MODEL
    fallback = (
        (os.environ.get("LLM_FALLBACK_MODEL") or "").strip() or DEFAULT_FALLBACK_MODEL
    )
    return [("primary", primary), ("fallback", fallback)]


def main() -> int:
    try:
        response = httpx.get(MODELS_URL, timeout=30.0)
        response.raise_for_status()
        listing = {m["id"]: m for m in response.json()["data"]}
    except httpx.HTTPError as exc:
        print(f"Could not reach OpenRouter ({type(exc).__name__}); nothing checked.")
        return 1

    failures = 0
    seen = set()
    print(f"{len(listing)} models listed by OpenRouter\n")

    for role, model_id in configured():
        if model_id in seen:
            print(f"{role:9} {model_id}\n          DUPLICATE: primary and fallback are the same model")
            failures += 1
            continue
        seen.add(model_id)

        model = listing.get(model_id)
        if model is None:
            print(f"{role:9} {model_id}\n          MISSING: not in OpenRouter's model list")
            failures += 1
            continue

        pricing = model.get("pricing") or {}
        prompt = float(pricing.get("prompt") or 0)
        completion = float(pricing.get("completion") or 0)
        free = prompt == 0 and completion == 0
        tools = "tools" in (model.get("supported_parameters") or [])

        problems = []
        if not free:
            problems.append(f"NOT FREE (prompt ${prompt}, completion ${completion} per token)")
        if not tools:
            problems.append("NO TOOL SUPPORT: the guardrail would reject its answers")

        status = "ok" if not problems else "FAIL"
        print(f"{role:9} {model_id}  [{status}]")
        print(f"          context {model.get('context_length')}, free={free}, tools={tools}")
        for problem in problems:
            print(f"          {problem}")
            failures += 1
        print()

    if failures:
        print(f"{failures} problem(s). Every explanation would fall back to the template.")
        return 1

    print("Both models exist, are free, and advertise tool support.")
    print("A key is still required to prove the live path: python scripts/check_agent.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

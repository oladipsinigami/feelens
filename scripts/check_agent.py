"""Spend one real model call and check what comes back.

Deliberately not part of the test suite: it needs a network, a key, and a
provider that may be rate-limited. Run it by hand after setting a key.

    python scripts/check_agent.py
    python scripts/check_agent.py --question "why is this so high?"

It prints the model's answer, the guardrail verdict, and the tool trace, so a
failure tells you *which* layer broke: no key, no model, a model that ignored
the tools, or a model that invented a figure and was caught.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.agent import ExplanationAgent, FeeTools, OpenRouterClient
from app.engine import load_fees, load_rates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", default="5000.00")
    parser.add_argument("--corridor", default="EUR")
    parser.add_argument("--payment", default="invoice_paypal")
    parser.add_argument("--question", default=None)
    parser.add_argument("--model", default=None, help="Override LLM_MODEL for this run")
    args = parser.parse_args()

    load_dotenv()

    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    model = args.model or (os.environ.get("LLM_MODEL") or "").strip()
    fallback = (os.environ.get("LLM_FALLBACK_MODEL") or "").strip() or None

    if not key:
        print("OPENROUTER_API_KEY is empty. Add it to .env first.")
        print("The app still works without it — it falls back to the template.")
        return 2

    print(f"primary model : {model or '(unset)'}")
    print(f"fallback      : {fallback or '(none)'}")
    print(f"question      : {args.question or '(none)'}")
    print()

    client = OpenRouterClient(key, model, fallback)
    agent = ExplanationAgent(FeeTools(load_fees(), load_rates()), client)

    explanation = agent.explain(
        args.amount, args.corridor, args.payment, args.question
    )
    body = explanation.as_dict()

    print(f"source        : {body['source']}")
    print(f"model used    : {body['model'] or '(none)'}")
    print(f"note          : {body['note'] or '(none)'}")
    print()
    print("--- answer ---")
    print(body["text"])
    print()
    print("--- caveats ---")
    for caveat in body["caveats"] or ["(none)"]:
        print(f"- {caveat}")
    print()
    print("--- guardrail ---")
    print(json.dumps(body["checks"], indent=2))
    print()
    print("--- tool trace ---")
    for step in body["trace"] or [{"name": "(no tools called)", "arguments": {}}]:
        print(f"- {step['name']}({json.dumps(step['arguments'])})")

    if body["source"] != "model":
        print()
        print("The model did not produce a usable answer; the template was served.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

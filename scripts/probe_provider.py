#!/usr/bin/env python3
"""Check that the configured providers answer on the three paths the agent actually uses.

Run this before a live example to find a wrong key, endpoint, or model id without opening a
browser. It exercises the real functions, so whatever passes here is what the agent will run:

1. Policy   -- model.choose on a synthetic element table (the chat or choice protocol)
2. Text     -- model.field_text, the TYPE_TEXT helper
3. Vision   -- GLMSupervisor.diagnose on a 1x1 JPEG, matching the screenshot format

These are real requests against a real provider, so they cost what three small calls cost.

    uv run --env-file .env python scripts/probe_provider.py
"""

import os
import sys
import traceback

from jev_ultrafast import model
from jev_ultrafast.browser import fingerprint
from jev_ultrafast.supervisor import GLMSupervisor

# A 1x1 black JPEG, the format Page.captureScreenshot hands the supervisor.
PIXEL_JPEG = (
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP/////////////////////////////////////////////////"
    "/////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAA"
    "AP/aAAgBAQABPxA="
)


def masked(key):
    return f"{key[:8]}...{key[-4:]} ({len(key)} chars)" if key and len(key) > 12 else "(not set)"


def sample_page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search this site",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def report(label, run):
    print(f"\n[{label}]")
    try:
        print(f"  OK   {run()}")
        return True
    except Exception as exc:
        print(f"  FAIL {type(exc).__name__}: {exc}")
        if os.environ.get("PROBE_TRACEBACK"):
            traceback.print_exc()
        return False


def probe_policy():
    decision = model.choose(sample_page(), "Search this site for a book", [])
    return (
        f"{decision['model']} chose {decision['operation']}"
        f"{' -> ' + decision['target'] if decision['target'] else ''}"
        f" in {decision['latency_ms']} ms"
    )


def probe_text():
    context = model.field_context(
        "Search this site for Godel's incompleteness theorems",
        {"label": "Search", "role": "textbox", "value": ""},
        {"title": "Search", "text": "Search this site"},
        [],
    )
    value, helper = model.field_text(context)
    return f"{helper['model']} wrote {value!r} in {helper['latency_ms']} ms"


def probe_vision(supervisor):
    result = supervisor.diagnose(PIXEL_JPEG, "Verify the supervisor endpoint accepts screenshots")
    return f"{supervisor.model} returned obstacle_type={result.get('obstacle_type')!r}"


def main():
    policy_endpoint = model.endpoint(
        os.environ.get("TYPESAFE_ENDPOINT") or os.environ.get("TYPESAFE_BASE_URL"),
        model.DEFAULT_TYPESAFE_ENDPOINT,
    )
    policy_model = os.environ.get("TYPESAFE_MODEL") or model.DEFAULT_TYPESAFE_MODEL
    native = model.choice_url(policy_endpoint, policy_model)
    protocol = f"native choice API at {native}" if native else "OpenAI-compatible chat"
    supervisor = GLMSupervisor()

    print("Configuration")
    print(f"  policy   {policy_endpoint} | {policy_model}")
    print(f"           protocol: {protocol}")
    print(f"           key: {masked(os.environ.get('TYPESAFE_API_KEY'))}")
    text_base = model.endpoint(os.environ.get("TEXT_MODEL_BASE_URL"), model.DEFAULT_TEXT_BASE_URL)
    print(f"  text     {text_base} | {os.environ.get('TEXT_MODEL') or model.DEFAULT_TEXT_MODEL}")
    print(f"           key: {masked(os.environ.get('TEXT_MODEL_API_KEY'))}")
    print(f"  vision   {supervisor.base_url} | {supervisor.model}")
    print(f"           key: {masked(supervisor.api_key)} | enabled: {supervisor.is_configured()}")

    results = [
        report("1/3 policy", probe_policy),
        report("2/3 text helper", probe_text),
    ]
    if supervisor.is_configured():
        results.append(report("3/3 vision supervisor", lambda: probe_vision(supervisor)))
    else:
        print("\n[3/3 vision supervisor]\n  SKIP disabled or no key; the agent runs without it")
    supervisor.close()

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    if failed:
        print("Re-run with PROBE_TRACEBACK=1 for full tracebacks.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

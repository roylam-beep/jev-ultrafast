"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import time
from urllib.parse import urlparse

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)

# The shipped configuration in .env.example. Keep these, the README, and the inspector in step.
DEFAULT_TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_TEXT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_TEXT_MODEL = "glm-5.3-flash"


class MissingFieldValue(ValueError):
    """The text helper honoured its contract and reported it has no value for this field."""

    def __init__(self, message, helper=None):
        super().__init__(message)
        self.helper = helper or {}


def endpoint(raw, default=""):
    """Every endpoint carries a bearer token, so none may come from an unvalidated setting."""
    url = (raw or default).rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Invalid endpoint URL scheme: {url or '(empty)'}")
    return url


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    typesafe_endpoint = endpoint(
        os.environ.get("TYPESAFE_ENDPOINT") or os.environ.get("TYPESAFE_BASE_URL"),
        DEFAULT_TYPESAFE_ENDPOINT,
    )
    result = post_json(typesafe_endpoint, os.environ["TYPESAFE_API_KEY"], body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = endpoint(os.environ.get("TEXT_MODEL_BASE_URL"), DEFAULT_TEXT_BASE_URL)
    model = os.environ.get("TEXT_MODEL") or DEFAULT_TEXT_MODEL

    host = (urlparse(base).hostname or "").lower()
    if host.endswith("openrouter.ai"):
        proto = "openrouter"
    elif host.endswith("bigmodel.cn") or host.endswith("z.ai"):
        proto = "zhipu"
    elif host == "api.deepseek.com" or host.endswith(".deepseek.com"):
        proto = "deepseek"
    else:
        proto = "generic"

    reasoning_setting = os.environ.get("TEXT_MODEL_REASONING", "").lower()
    if reasoning_setting == "none":
        reasoning = {"reasoning": {"enabled": False}}
    elif reasoning_setting == "omit":
        reasoning = {}
    elif reasoning_setting == "enabled":
        reasoning = {"thinking": {"type": "enabled"}}
    elif reasoning_setting == "disabled":
        reasoning = {"thinking": {"type": "disabled"}}
    elif proto == "zhipu":
        reasoning = {}
    elif proto == "deepseek":
        reasoning = {"thinking": {"type": "disabled"}}
    elif proto == "openrouter":
        reasoning = {"reasoning": {"effort": "low"}}
    else:
        reasoning = {}
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
    )
    helper = {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        if set(output) != {"text"}:
            raise ValueError()
        value = output["text"]
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    # TEXT_VALUE asks for {"text": null} when the goal supplies no value. Honour that answer;
    # the caller stops the run rather than typing a guess.
    if value is None:
        raise MissingFieldValue("Text helper reported no value for this field; nothing typed.", helper)
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("Text helper returned no valid field value; nothing typed.")
    return value, helper

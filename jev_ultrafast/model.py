"""The policy makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import time
from urllib.parse import urlparse

import httpx

from .questions import CHOICE_POLICY, NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)

# The shipped configuration in .env.example. Keep these, the README, and the inspector in step.
DEFAULT_TYPESAFE_ENDPOINT = "https://openrouter.ai/api/v1"
DEFAULT_TYPESAFE_MODEL = "typesafe/jev-1.13"
# TypeSafe's own choice API, for TYPESAFE_ENDPOINT when running Jev instead of a chat model.
DEFAULT_TYPESAFE_CHOICE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_TEXT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TEXT_MODEL = "z-ai/glm-5.3-flash"
# TypeSafe's own choice API answers every question in one constrained request. Any other
# OpenAI-compatible provider is reached through chat completions, which returns free-form
# JSON, so the answers it gives are validated against the same observed ids either way.
TYPESAFE_CHOICE_HOSTS = ("typesafe.ai",)


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


def uses_choice_api(url):
    """TypeSafe answers the whole question set natively; anything else goes through chat."""
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in TYPESAFE_CHOICE_HOSTS)


def provider(base):
    host = (urlparse(base).hostname or "").lower()
    if host.endswith("openrouter.ai"):
        return "openrouter"
    if host.endswith("bigmodel.cn") or host.endswith("z.ai"):
        return "zhipu"
    if host == "api.deepseek.com" or host.endswith(".deepseek.com"):
        return "deepseek"
    return "generic"


def reasoning_payload(proto, setting):
    """Each provider spells the thinking switch differently; an explicit setting always wins."""
    setting = (setting or "").lower()
    if setting == "none":
        return {"reasoning": {"enabled": False}}
    if setting == "omit":
        return {}
    if setting == "enabled":
        return {"thinking": {"type": "enabled"}}
    if setting == "disabled":
        return {"thinking": {"type": "disabled"}}
    if proto == "deepseek":
        return {"thinking": {"type": "disabled"}}
    if proto == "openrouter":
        return {"reasoning": {"effort": "low"}}
    return {}


def spread_probabilities(ids, choice, confidence):
    """A chat model reports one choice, not a distribution over every observed id.

    The inspector and traces expect a distribution, so the reported confidence stays on the
    selected id and the remainder is spread evenly. These are derived from one number, not
    measured per-element likelihoods -- docs/performance.md keeps the two sources apart.
    """
    ids = list(ids)
    if not ids:
        return {}
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    if not math.isfinite(confidence):
        confidence = 0.5
    # Below 1/len the selected id would stop being the maximum and fail validate_choice.
    confidence = min(1.0, max(confidence, 1.0 / len(ids)))
    others = [i for i in ids if i != choice]
    share = (1.0 - confidence) / len(others) if others else 0.0
    probabilities = dict.fromkeys(others, share)
    probabilities[choice] = confidence if others else 1.0
    return probabilities, confidence


def chat_answers(base, key, model, body, setting):
    """Answer every question in one chat request for providers without TypeSafe's choice API.

    Same contract as the choice API: one round trip, one key per question, and choices that
    must already exist in the observed criteria. validate_choice rejects anything else.
    """
    questions = body["questions"]
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
            **reasoning_payload(provider(base), setting),
            "messages": [
                {"role": "system", "content": CHOICE_POLICY},
                {
                    "role": "user",
                    "content": json.dumps({"state": body["state"], "questions": questions}, ensure_ascii=False),
                },
            ],
        },
    )
    try:
        content = json.loads(result["choices"][0]["message"]["content"])
        replies = content["answers"] if isinstance(content.get("answers"), dict) else content
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("Invalid TypeSafe response; no action executed.") from None
    answers = {}
    for name, question in questions.items():
        reply = replies.get(name)
        if not isinstance(reply, dict):
            continue
        # str() only normalises how JSON spelled an index; an unobserved id still fails below.
        choice = str(reply.get("choice"))
        ids = list(question["criteria"])
        if choice not in ids:
            continue
        probabilities, confidence = spread_probabilities(ids, choice, reply.get("confidence"))
        answers[name] = {"choice": choice, "probabilities": probabilities, "confidence": confidence}
    return {"answers": answers, "model": result.get("model", model), "usage": result.get("usage", {})}


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
        "model": os.environ.get("TYPESAFE_MODEL") or DEFAULT_TYPESAFE_MODEL,
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
    key = os.environ["TYPESAFE_API_KEY"]
    if uses_choice_api(typesafe_endpoint):
        result = post_json(typesafe_endpoint, key, body)
    else:
        result = chat_answers(
            typesafe_endpoint, key, body["model"], body, os.environ.get("TYPESAFE_MODEL_REASONING")
        )
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

    reasoning = reasoning_payload(provider(base), os.environ.get("TEXT_MODEL_REASONING"))
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

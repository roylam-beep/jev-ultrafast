"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import os
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("TYPESAFE_ENDPOINT", model.DEFAULT_TYPESAFE_CHOICE_ENDPOINT)
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("TYPESAFE_ENDPOINT", model.DEFAULT_TYPESAFE_CHOICE_ENDPOINT)
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("TYPESAFE_ENDPOINT", model.DEFAULT_TYPESAFE_CHOICE_ENDPOINT)
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_choice_url_routes_by_host_then_by_model():
    # TypeSafe's endpoint is already the full URL, whatever model runs on it.
    assert model.choice_url(model.DEFAULT_TYPESAFE_CHOICE_ENDPOINT, "jev-latest") == (
        model.DEFAULT_TYPESAFE_CHOICE_ENDPOINT
    )
    # One OpenRouter host, two protocols: a decisions model is rejected on /chat/completions.
    assert model.choice_url(model.DEFAULT_TYPESAFE_ENDPOINT, "typesafe/jev-1.13") == (
        model.OPENROUTER_DECISIONS_URL
    )
    assert model.choice_url(model.DEFAULT_TYPESAFE_ENDPOINT, "z-ai/glm-5.3-flash") == ""
    assert model.choice_url("https://api.deepseek.com", "typesafe/jev-1.13") == ""


def test_shipped_default_reaches_the_decisions_path_with_measured_probabilities(monkeypatch):
    calls = []

    def post(url, _key, body):
        calls.append((url, body))
        return {
            "model": "typesafe/jev-1.13-20260917",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2"], "2"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.delenv("TYPESAFE_ENDPOINT", raising=False)
    monkeypatch.delenv("TYPESAFE_MODEL", raising=False)
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert calls[0][0] == model.OPENROUTER_DECISIONS_URL
    # The question set travels as the body, not wrapped in chat messages.
    assert set(calls[0][1]["questions"]) == {"operation", "click_target", "type_text_target"}
    assert "messages" not in calls[0][1]
    assert d["operation"] == "TYPE_TEXT" and d["choice"] == "e1"


def chat_reply(answers):
    return {"choices": [{"message": {"content": json.dumps({"answers": answers})}}], "model": "router/model"}


def test_chat_policy_answers_every_head_in_one_request(monkeypatch):
    calls = []

    def post(url, _key, body):
        calls.append((url, body))
        return chat_reply(
            {
                "operation": {"choice": "TYPE_TEXT", "confidence": 0.8},
                "type_text_target": {"choice": "1", "confidence": 0.9},
                "click_target": {"choice": "2", "confidence": 0.4},
            }
        )

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.delenv("TYPESAFE_ENDPOINT", raising=False)
    # A chat model on the shipped endpoint: the decisions path keys on the model, not the host.
    monkeypatch.setenv("TYPESAFE_MODEL", "z-ai/glm-5.3-flash")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert calls[0][0] == model.DEFAULT_TYPESAFE_ENDPOINT + "/chat/completions"
    # The whole action space still travels in one request, as with the choice API.
    sent = json.loads(calls[0][1]["messages"][1]["content"])
    assert set(sent["questions"]) == {"operation", "click_target", "type_text_target"}
    assert d["operation"] == "TYPE_TEXT" and d["choice"] == "e1"
    assert abs(sum(d["target_probabilities"].values()) - 1) < 0.02


def test_chat_policy_rejects_a_key_the_page_never_offered(monkeypatch):
    def post(_url, _key, _body):
        return chat_reply(
            {
                "operation": {"choice": "CLICK", "confidence": 0.9},
                "click_target": {"choice": "999", "confidence": 0.9},
            }
        )

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.delenv("TYPESAFE_ENDPOINT", raising=False)
    # A chat model on the shipped endpoint: the decisions path keys on the model, not the host.
    monkeypatch.setenv("TYPESAFE_MODEL", "z-ai/glm-5.3-flash")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


@pytest.mark.parametrize(
    "content",
    ['{"answers": {"operation": "CLICK"}}', "not json", '{"answers": []}', '{"answers": {"operation": {}}}'],
)
def test_chat_policy_rejects_unusable_replies(monkeypatch, content):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.delenv("TYPESAFE_ENDPOINT", raising=False)
    # A chat model on the shipped endpoint: the decisions path keys on the model, not the host.
    monkeypatch.setenv("TYPESAFE_MODEL", "z-ai/glm-5.3-flash")
    monkeypatch.setattr(
        model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]})
    )
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


@pytest.mark.parametrize("confidence", [1.0, 0.5, 0.0, None, "high", float("nan")])
def test_spread_probabilities_always_survives_validation(confidence):
    ids = [str(i) for i in range(1, 8)]
    probabilities, resolved = model.spread_probabilities(ids, "3", confidence)
    model.validate_choice({"choice": "3", "confidence": resolved, "probabilities": probabilities}, ids)


def test_reasoning_setting_overrides_the_provider_default():
    assert model.reasoning_payload("openrouter", None) == {"reasoning": {"effort": "low"}}
    assert model.reasoning_payload("openrouter", "omit") == {}
    assert model.reasoning_payload("zhipu", None) == {}
    assert model.provider("https://openrouter.ai/api/v1") == "openrouter"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"exceptionDetails": {}, "result": {}}])
def test_interrupted_scroll_cannot_be_retried_as_stale(monkeypatch, response):
    """A wheel handler that navigates can destroy the context after the page moved."""
    import jev_ultrafast.browser as browser

    response = deepcopy(response)
    response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Scroll execution was interrupted"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "scroll_down", "kind": "scroll", "delta": 560,
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


def test_fingerprint_ignores_geometry():
    """Animation must not report page_changed; that would defeat the no-progress stop."""
    p = page()
    for action in p["actions"]:
        action["rect"] = {"x": 0, "y": 0, "w": 100, "h": 20}
    p["fingerprint"] = fingerprint(p)
    moved = deepcopy(p)
    for action in moved["actions"]:
        action["rect"] = {"x": 3, "y": 41, "w": 100, "h": 20}
    assert fingerprint(moved) == fingerprint(p)
    moved["actions"][2]["label"] = "Search"
    assert fingerprint(moved) != fingerprint(p)


def test_no_progress_stop_survives_a_moving_page(runner):
    """Three unchanged-but-animating steps still stop the run for the supervisor."""
    offset = iter(range(1, 100))

    def observe(screenshot=False):
        state = page()
        for action in state["actions"]:
            action["rect"] = {"x": next(offset), "y": 0, "w": 100, "h": 20}
        state["fingerprint"] = fingerprint(state)
        return state

    runner.state["browser"].observe.side_effect = observe
    runner.state["page"] = observe()
    for _ in range(3):
        runner.state["decision"] = decision("e3")
        runner.state["status"] = "predicted"
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert [h["page_changed"] for h in runner.state["history"]] == [False, False, False]
    assert runner.state["status"] == "blocked"


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":"   "}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_documented_null_answer_is_distinguished_from_malformed_output(monkeypatch):
    """TEXT_VALUE asks for {"text": null} when the goal supplies no value."""
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(
        model,
        "post_json",
        Mock(return_value={"choices": [{"message": {"content": '{"text":null}'}}], "usage": {"total_tokens": 7}}),
    )
    with pytest.raises(model.MissingFieldValue) as raised:
        model.field_text({"goal": "Find a flight"})
    assert raised.value.helper["usage"] == {"total_tokens": 7}


def test_missing_field_value_blocks_the_run_without_typing(runner, monkeypatch):
    # The agent binds field_text at import time.
    monkeypatch.setattr(
        loop, "field_text", Mock(side_effect=model.MissingFieldValue("nothing typed", {"model": "m"}))
    )
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked"
    runner.state["browser"].act.assert_not_called()
    assert runner.state["history"] == []
    assert runner.state["text_calls"] == [{"model": "m", "field": "Search", "value": None}]


@pytest.mark.parametrize("variable", ["TYPESAFE_ENDPOINT", "TEXT_MODEL_BASE_URL"])
def test_endpoints_reject_an_unvalidated_scheme(monkeypatch, variable):
    """Every endpoint receives a bearer token, so none may come from an unchecked setting."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv(variable, "file:///etc/passwd")
    monkeypatch.setattr(model, "post_json", Mock())
    with pytest.raises(ValueError, match="Invalid endpoint URL scheme"):
        if variable == "TEXT_MODEL_BASE_URL":
            model.field_text({"goal": "Find a flight"})
        else:
            model.choose(page(), "Find a flight", [])
    model.post_json.assert_not_called()


def test_empty_endpoint_setting_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "")
    assert model.endpoint(os.environ["TEXT_MODEL_BASE_URL"], model.DEFAULT_TEXT_BASE_URL) == (
        model.DEFAULT_TEXT_BASE_URL
    )
    assert model.endpoint("https://example.test/v1/", model.DEFAULT_TEXT_BASE_URL) == "https://example.test/v1"


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def decision_for(selected, operation="CLICK", target="1"):
    return {
        "choice": selected, "operation": operation, "target": target, "confidence": 0.9,
        "probabilities": {selected: 1.0}, "operation_probabilities": {operation: 1.0},
        "target_probabilities": {target: 1.0}, "target_confidence": 0.9,
        "raw_answers": {}, "model": "test", "usage": {}, "latency_ms": 1, "request": {},
    }


def test_an_unreachable_target_is_recorded_and_three_in_a_row_stop_the_run(monkeypatch):
    """Without the record the policy re-picks the same covered element until the budget dies."""
    from unittest.mock import patch

    from jev_ultrafast.browser import UnreachableTarget

    observed = page()
    with patch("jev_ultrafast.agent.Browser") as MockBrowser:
        browser = MockBrowser.return_value
        browser.observe.return_value = observed
        browser.fresh.return_value = True
        browser.act.side_effect = UnreachableTarget("covered")
        monkeypatch.setattr(loop, "choose", lambda *a, **k: decision_for("e3"))
        agent = loop.Agent("https://example.test/", "Find a book")
        try:
            for _ in range(3):
                agent.command("tick")
            history = agent.state["history"]
            assert [h["kind"] for h in history] == ["rejected"] * 3
            assert history[0]["action"] == "Rejected: Go"
            # The policy reads recent actions; a refusal must not look like a successful step.
            assert all(h["page_changed"] is False for h in history)
            assert agent.state["status"] == "blocked"
        finally:
            agent.close()


def test_a_stale_page_is_not_recorded_as_a_rejected_target(monkeypatch):
    """A decision that merely aged out says nothing about whether the target is reachable."""
    from unittest.mock import patch

    from jev_ultrafast.browser import StalePage

    with patch("jev_ultrafast.agent.Browser") as MockBrowser:
        browser = MockBrowser.return_value
        browser.observe.return_value = page()
        browser.fresh.return_value = True
        browser.act.side_effect = StalePage("the page moved on")
        monkeypatch.setattr(loop, "choose", lambda *a, **k: decision_for("e3"))
        agent = loop.Agent("https://example.test/", "Find a book")
        try:
            agent.command("tick")
            assert agent.state["history"] == []
            assert agent.state["status"] == "ready"
        finally:
            agent.close()


def guard_value(scope_text, name="Go"):
    """A guard as snapshot.js builds it: identity and state, then the scope's text last."""
    return [7, "button", name, None, None, None, None, False, None, None, None, None, "/go", scope_text]


def test_a_control_that_animates_its_own_surroundings_stays_operable(monkeypatch):
    """A marquee or ticker rewrites the scope text on a timer; that is not a substitution."""
    from jev_ultrafast.browser import Browser

    browser = Browser.__new__(Browser)
    observed = {"page_key": ["k"], "guards": {"20": guard_value("viewed 3 times")}}
    action = {"kind": "click", "node": 20}

    reads = iter([[["k"], guard_value("viewed 4 times")], [["k"], guard_value("viewed 5 times")]])
    monkeypatch.setattr(Browser, "_guard", lambda self, node: next(reads))
    assert browser.fresh(observed, action) is True


def test_a_replaced_target_is_still_rejected_when_the_scope_is_volatile(monkeypatch):
    """Falling back to identity must not forgive a different element in the same slot."""
    from jev_ultrafast.browser import Browser

    browser = Browser.__new__(Browser)
    observed = {"page_key": ["k"], "guards": {"20": guard_value("viewed 3 times", name="Go")}}
    action = {"kind": "click", "node": 20}

    reads = iter([[["k"], guard_value("viewed 4 times", name="Buy")],
                  [["k"], guard_value("viewed 5 times", name="Buy")]])
    monkeypatch.setattr(Browser, "_guard", lambda self, node: next(reads))
    assert browser.fresh(observed, action) is False


def test_a_settled_disagreement_is_still_a_stale_target(monkeypatch):
    """Two identical reads mean the page is not animating: the guard really did change."""
    from jev_ultrafast.browser import Browser

    browser = Browser.__new__(Browser)
    observed = {"page_key": ["k"], "guards": {"20": guard_value("Total $10")}}
    action = {"kind": "click", "node": 20}

    reads = iter([[["k"], guard_value("Total $99")], [["k"], guard_value("Total $99")]])
    monkeypatch.setattr(Browser, "_guard", lambda self, node: next(reads))
    assert browser.fresh(observed, action) is False


def test_progress_ignores_a_page_that_animates_itself():
    """A carousel rewrites the text every second; that is not the agent making progress."""
    from jev_ultrafast.browser import fingerprint, progress_key

    before = page()
    same_page_new_text = deepcopy(before)
    same_page_new_text["text"] = "Search — 1,284 people viewing right now"
    # fingerprint must notice (the observation really is different)...
    assert fingerprint(same_page_new_text) != fingerprint(before)
    # ...but nothing the agent did moved, so it is not progress.
    assert progress_key(same_page_new_text) == progress_key(before)


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("a filter rewrote the query string", lambda s: s.update(url=s["url"] + "?section=105")),
        ("the page scrolled", lambda s: s["scroll"].update(y=560)),
        ("a control appeared", lambda s: s["actions"].append(
            {"id": "e9", "kind": "click", "label": "South district", "node": 90})),
    ],
)
def test_progress_notices_what_an_action_actually_moves(label, mutate):
    from jev_ultrafast.browser import progress_key

    before = page()
    after = deepcopy(before)
    mutate(after)
    assert progress_key(after) != progress_key(before), label


def test_the_policy_is_told_where_it_is_and_what_each_step_did(monkeypatch):
    """Scroll position and the url of each past step: without them the policy is flying blind."""
    sent = {}

    def post(_url, _key, body):
        sent.update(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "click_target": choice(["1", "2"], "2"),
                "type_text_target": choice(["1"], "1"),
            },
        }

    state = page()
    state["scroll"] = {"y": 560, "height": 4200}
    state["h"] = 780
    history = [{"action": "South district", "kind": "click", "text": None,
                "page_changed": True, "url": "https://example.test/?section=105"}]

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("TYPESAFE_ENDPOINT", model.DEFAULT_TYPESAFE_CHOICE_ENDPOINT)
    monkeypatch.setattr(model, "post_json", post)
    model.choose(state, "Find a book", history)

    scroll = sent["state"]["page"]["scroll"]
    assert scroll == {"y": 560, "height": 4200, "viewport_height": 780}
    assert sent["state"]["recent_actions"][0]["url"] == "https://example.test/?section=105"


MARK_OPEN = "⟦"
MARK_CLOSE = "⟧"


def marked(*groups):
    """Page text as snapshot.js now emits it: a marker line, then that record's lines."""
    out = []
    for node, lines in groups:
        out.append(f"{MARK_OPEN}{node}{MARK_CLOSE}" if node else MARK_OPEN + MARK_CLOSE)
        out.extend(lines)
    return "\n".join(out)


def test_record_markers_move_the_fingerprint_but_not_progress():
    """Grouping is part of the observation, so it must age one out -- but it is not progress."""
    from jev_ultrafast.browser import fingerprint, progress_key

    before = page()
    before["text"] = marked((7, ["Widget A", "1,100"]), (8, ["Widget B", "2,200"]))
    regrouped = deepcopy(before)
    regrouped["text"] = marked((7, ["Widget A", "1,100", "Widget B", "2,200"]))

    assert fingerprint(regrouped) != fingerprint(before)
    assert progress_key(regrouped) == progress_key(before)


def test_records_need_no_place_in_the_fingerprint():
    """records is a pure view of text, so text alone carries the staleness signal.

    If this ever stops holding, records must join the marker -- otherwise the page could be
    regrouped while fresh() still reports the old observation as current.
    """
    from jev_ultrafast.browser import fingerprint

    base = page()
    base["text"] = marked((7, ["Widget A", "1,100"]))
    with_field = deepcopy(base)
    with_field["records"] = [{"node": 7, "lines": ["Widget A", "1,100"]}]
    assert fingerprint(with_field) == fingerprint(base)

    moved = deepcopy(base)
    moved["text"] = marked((9, ["Widget A", "1,100"]))
    assert fingerprint(moved) != fingerprint(base)


def test_the_text_helper_still_sees_a_bounded_page():
    long_text = marked((7, ["x" * 4000]), (8, ["y" * 4000]))
    context = model.field_context("goal", page()["actions"][0], {"title": "t", "text": long_text}, [])
    assert len(context["page"]["text"]) == 6000


def test_markers_do_not_break_the_independent_flight_check():
    """The only real parser of page text splits on lines; markers sit between them."""
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": marked((11, ["Track prices from Zürich to London departing 2026-09-20"])),
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]


def test_the_model_number_rule_reaches_every_head(monkeypatch):
    """NEXT_ACTION rides in each head's instructions, so both protocols carry the rule."""
    sent = {}

    def post(_url, _key, body):
        sent.update(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "click_target": choice(["1", "2"], "2"),
                "type_text_target": choice(["1"], "1"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("TYPESAFE_ENDPOINT", model.DEFAULT_TYPESAFE_CHOICE_ENDPOINT)
    monkeypatch.setattr(model, "post_json", post)
    model.choose(page(), "Find an S26", [])

    for name, question in sent["questions"].items():
        rules = question["instructions"]["rules"]
        text = rules if isinstance(rules, str) else "\n".join(rules)
        assert "S26 Ultra" in text, name
        assert MARK_OPEN in text, f"{name} does not explain the record marker"

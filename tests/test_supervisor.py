"""Hardened test suite for GLM-5.3-Flash Multimodal Visual Supervisor."""

import json
from unittest.mock import Mock, patch
from unittest.mock import call as mock_call

import httpx
import pytest

from jev_ultrafast.keyboard import key_definition
from jev_ultrafast.pointer import click_events, scroll_expression
from jev_ultrafast.supervisor import (
    ALLOWED_KEYS,
    SETTLE_PROBE,
    GLMSupervisor,
    _settle,
    screen_click_text,
    validate_recovery_action,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Clean all model environment variables for deterministic isolated tests."""
    for k in (
        "VISION_MODEL_API_KEY",
        "VISION_MODEL_BASE_URL",
        "VISION_MODEL",
        "TEXT_MODEL_API_KEY",
        "TEXT_MODEL_BASE_URL",
        "TEXT_MODEL",
        "VISION_SUPERVISOR_ENABLED",
    ):
        monkeypatch.delenv(k, raising=False)


def test_supervisor_initialization(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.setenv("TEXT_MODEL", "glm-5.3-flash")

    supervisor = GLMSupervisor()
    assert supervisor.is_configured()
    assert supervisor.api_key == "test-key"
    assert supervisor.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert supervisor.model == "glm-5.3-flash"
    supervisor.close()


def test_supervisor_disabled_via_env(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("VISION_SUPERVISOR_ENABLED", "false")
    supervisor = GLMSupervisor()
    assert not supervisor.is_configured()
    supervisor.close()


def test_context_manager():
    with GLMSupervisor(api_key="test-key") as supervisor:
        assert supervisor.is_configured()
        assert not supervisor.client.is_closed
    assert supervisor.client.is_closed


def test_diagnose_unconfigured():
    supervisor = GLMSupervisor(api_key="")
    res = supervisor.diagnose("fake_b64", "test goal")
    assert res["can_auto_recover"] is False
    assert res["action_type"] == "HUMAN_INTERVENTION"
    supervisor.close()


def test_diagnose_success():
    supervisor = GLMSupervisor(api_key="valid-key")

    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "obstacle_type": "BANNER_OVERLAY",
                            "stuck_reason": "Cookie consent dialog blocking interactions.",
                            "can_auto_recover": True,
                            "action_type": "CLICK_TEXT",
                            "target_text": "Accept all",
                            "key_name": None,
                            "scroll_delta": None,
                        }
                    )
                }
            }
        ]
    }

    with patch.object(supervisor.client, "post", return_value=mock_resp):
        res = supervisor.diagnose("fake_b64", "Find flights", [{"action": "click search", "operation": "CLICK"}])
        assert res["obstacle_type"] == "BANNER_OVERLAY"
        assert res["can_auto_recover"] is True
        assert res["action_type"] == "CLICK_TEXT"
        assert res["target_text"] == "Accept all"
    supervisor.close()


def test_diagnose_rejects_prompt_injection():
    """Security test: prompt injection attempt from web page must be rejected and recorded."""
    supervisor = GLMSupervisor(api_key="valid-key")

    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "obstacle_type": "BANNER_OVERLAY",
                            "stuck_reason": "Malicious injected instructions.",
                            "can_auto_recover": True,
                            "action_type": "CLICK_TEXT",
                            "target_text": "Delete my account now",
                        }
                    )
                }
            }
        ]
    }

    with patch.object(supervisor.client, "post", return_value=mock_resp):
        res = supervisor.diagnose("fake_b64", "Find flights")
        assert res["can_auto_recover"] is False
        assert res["action_type"] == "HUMAN_INTERVENTION"
        assert res["rejected_action_type"] == "CLICK_TEXT"
        assert res["rejected_target_text"] == "Delete my account now"
    supervisor.close()


def test_diagnose_returns_non_dict_json():
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": "[\"not\", \"a\", \"dict\"]"}}]}

    with patch.object(supervisor.client, "post", return_value=mock_resp):
        res = supervisor.diagnose("fake_b64", "Find flights")
        assert res["can_auto_recover"] is False
        assert res["action_type"] == "HUMAN_INTERVENTION"
    supervisor.close()


def test_diagnose_http_500():
    supervisor = GLMSupervisor(api_key="valid-key")
    req = httpx.Request("POST", "https://api.test")
    resp = httpx.Response(500, request=req, text="Internal Server Error")

    with patch.object(supervisor.client, "post", side_effect=httpx.HTTPStatusError("500", request=req, response=resp)):
        res = supervisor.diagnose("fake_b64", "Find flights")
        assert res["can_auto_recover"] is False
        assert res["action_type"] == "HUMAN_INTERVENTION"
        assert "500" in res["stuck_reason"]
    supervisor.close()


def test_verify_goal_achievement_fail_closed_on_error():
    supervisor = GLMSupervisor(api_key="valid-key")

    with patch.object(supervisor.client, "post", side_effect=RuntimeError("Connection dropped")):
        audit = supervisor.verify_goal_achievement("fake_b64", "Find flights")
        assert audit["satisfied"] is None
        assert audit["confidence"] == 0.0
        assert "UNAVAILABLE" in audit["explanation"]
    supervisor.close()


def test_verify_goal_achievement_success():
    supervisor = GLMSupervisor(api_key="valid-key")

    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "satisfied": True,
                            "confidence": 0.96,
                            "explanation": "Flight options from Zurich to London are clearly rendered.",
                        }
                    )
                }
            }
        ]
    }

    with patch.object(supervisor.client, "post", return_value=mock_resp):
        audit = supervisor.verify_goal_achievement("fake_b64", "Find flights")
        assert audit["satisfied"] is True
        assert audit["confidence"] == 0.96
    supervisor.close()


def test_verify_goal_achievement_string_false_never_passes():
    """B2 Fix: String 'false' must evaluate to boolean False, NOT True."""
    supervisor = GLMSupervisor(api_key="valid-key")

    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "satisfied": "false",
                            "confidence": 0.95,
                            "explanation": "No flight options visible.",
                        }
                    )
                }
            }
        ]
    }

    with patch.object(supervisor.client, "post", return_value=mock_resp):
        audit = supervisor.verify_goal_achievement("fake_b64", "Find flights")
        assert audit["satisfied"] is False
        assert audit["confidence"] == 0.95
    supervisor.close()


def test_verify_goal_achievement_string_true_passes():
    """B2 Fix: String 'true' evaluates to True when confidence >= 0.70."""
    supervisor = GLMSupervisor(api_key="valid-key")

    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "satisfied": "true",
                            "confidence": 0.85,
                            "explanation": "Search results found.",
                        }
                    )
                }
            }
        ]
    }

    with patch.object(supervisor.client, "post", return_value=mock_resp):
        audit = supervisor.verify_goal_achievement("fake_b64", "Find flights")
        assert audit["satisfied"] is True
        assert audit["confidence"] == 0.88 or audit["confidence"] == 0.85
    supervisor.close()


def test_verify_goal_achievement_low_confidence_downgrades_to_none():
    """B2 Fix: Low confidence (< 0.70) must downgrade satisfied to None."""
    supervisor = GLMSupervisor(api_key="valid-key")

    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "satisfied": True,
                            "confidence": 0.65,
                            "explanation": "Uncertain if page completed.",
                        }
                    )
                }
            }
        ]
    }

    with patch.object(supervisor.client, "post", return_value=mock_resp):
        audit = supervisor.verify_goal_achievement("fake_b64", "Find flights")
        assert audit["satisfied"] is None
        assert audit["confidence"] == 0.65
    supervisor.close()


def test_verify_goal_achievement_invalid_type_downgrades_to_none():
    """B2 Fix: Non-boolean, non-recognized string values evaluate to None."""
    supervisor = GLMSupervisor(api_key="valid-key")

    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "satisfied": 42,
                            "confidence": 0.90,
                            "explanation": "Bad output.",
                        }
                    )
                }
            }
        ]
    }

    with patch.object(supervisor.client, "post", return_value=mock_resp):
        audit = supervisor.verify_goal_achievement("fake_b64", "Find flights")
        assert audit["satisfied"] is None
    supervisor.close()


def test_verify_goal_achievement_non_numeric_confidence():
    """P1: Verify non-numeric confidence (e.g. 'high') logs a warning and cleanly defaults to 0.0."""
    supervisor = GLMSupervisor(api_key="valid-key")

    mock_resp = Mock()
    mock_resp.raise_for_status = Mock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "satisfied": True,
                            "confidence": "high",
                            "explanation": "Flights are visible.",
                        }
                    )
                }
            }
        ]
    }

    with patch.object(supervisor.client, "post", return_value=mock_resp):
        audit = supervisor.verify_goal_achievement("fake_b64", "Find flights")
        assert audit["confidence"] == 0.0
        assert audit["satisfied"] is None  # Downgraded because 0.0 < 0.70
        assert audit["explanation"] == "Flights are visible."
    supervisor.close()


def test_apply_recovery_rejects_dangerous_action():
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = Mock()

    # Confused deputy click attempt
    danger_diag = {
        "can_auto_recover": True,
        "action_type": "CLICK_TEXT",
        "target_text": "Confirm payment and checkout",
    }
    assert supervisor.apply_recovery(mock_browser, danger_diag) is False
    assert mock_browser.call.call_count == 0

    # Unlisted action type
    eval_diag = {
        "can_auto_recover": True,
        "action_type": "EXECUTE_JAVASCRIPT",
    }
    assert supervisor.apply_recovery(mock_browser, eval_diag) is False
    supervisor.close()


def test_apply_recovery_keypress_with_virtual_key_code():
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = Mock()
    mock_browser.evaluate.return_value = "complete:1000"

    diagnosis = {
        "can_auto_recover": True,
        "action_type": "PRESS_KEY",
        "key_name": "Escape",
    }

    assert supervisor.apply_recovery(mock_browser, diagnosis) is True
    assert mock_browser.call.call_count == 2
    for event_type in ("keyDown", "keyUp"):
        mock_browser.call.assert_any_call(
            "Input.dispatchKeyEvent",
            type=event_type,
            key="Escape",
            code="Escape",
            windowsVirtualKeyCode=27,
            nativeVirtualKeyCode=27,
            modifiers=0,
        )
    supervisor.close()


def test_every_allowed_recovery_key_has_a_definition():
    """A key the policy allows must not dispatch as an unidentified keyCode 0."""
    for key in ALLOWED_KEYS:
        virtual_key, code, _text = key_definition(key)
        assert virtual_key, key
        assert code == key, key


def _scroll_browser(moved=True):
    mock_browser = Mock()
    mock_browser.evaluate.side_effect = (
        lambda expr: "complete:1000" if expr == SETTLE_PROBE else moved
    )
    return mock_browser


def test_apply_recovery_scroll_goes_through_the_synthetic_wheel():
    """A CDP mouseWheel is dropped on the background target the agent owns."""
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = _scroll_browser()

    diagnosis = {"can_auto_recover": True, "action_type": "SCROLL", "scroll_delta": 450}

    assert supervisor.apply_recovery(mock_browser, diagnosis) is True
    assert mock_browser.call.call_count == 0, "no Input.dispatchMouseEvent for a wheel"
    expression = next(
        c.args[0] for c in mock_browser.evaluate.call_args_list if c.args[0] != SETTLE_PROBE
    )
    assert expression == scroll_expression(450)
    assert "innerWidth / 2" in expression and "innerHeight / 2" in expression


def test_apply_recovery_scroll_reports_true_even_when_the_page_holds_still():
    """A page that preventDefaults the wheel is not a failed recovery action."""
    supervisor = GLMSupervisor(api_key="valid-key")
    assert supervisor.apply_recovery(
        _scroll_browser(moved=False),
        {"can_auto_recover": True, "action_type": "SCROLL", "scroll_delta": 300},
    ) is True
    supervisor.close()


def _click_text_browser(resolved):
    """A browser whose CLICK_TEXT resolver returns coordinates plus the element's own text."""
    mock_browser = Mock()
    mock_browser.evaluate.side_effect = (
        lambda expr: "complete:1000" if expr == SETTLE_PROBE else {"x": 200, "y": 300, "text": resolved}
    )
    return mock_browser


def test_apply_recovery_click_text_success():
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = _click_text_browser("Close")

    diagnosis = {
        "can_auto_recover": True,
        "action_type": "CLICK_TEXT",
        "target_text": "Close",
    }

    assert supervisor.apply_recovery(mock_browser, diagnosis) is True
    press, release = click_events(200, 300)
    assert mock_browser.call.call_args_list == [
        mock_call("Input.dispatchMouseEvent", **press),
        mock_call("Input.dispatchMouseEvent", **release),
    ]
    supervisor.close()


def test_apply_recovery_click_text_evaluate_none():
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = Mock()
    mock_browser.evaluate.return_value = None

    diagnosis = {
        "can_auto_recover": True,
        "action_type": "CLICK_TEXT",
        "target_text": "Close",
    }

    assert supervisor.apply_recovery(mock_browser, diagnosis) is False
    assert mock_browser.call.call_count == 0
    supervisor.close()


@pytest.mark.parametrize(
    "target_text, resolved",
    [
        # "ok" clears every guard, but the DOM resolves it onto a purchase button.
        ("ok", "Book now"),
        ("好的", "好的，刪除帳戶"),
        ("close", "Close account and delete all data"),
        # Safe prefix, but the resolved element is not a dismiss control at all.
        ("continue", "Continue to checkout"),
    ],
)
def test_apply_recovery_click_text_screens_the_resolved_element(target_text, resolved):
    """The blacklist must screen the element clicked, not only the label the model proposed."""
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = _click_text_browser(resolved)

    diagnosis = {"can_auto_recover": True, "action_type": "CLICK_TEXT", "target_text": target_text}
    assert validate_recovery_action(diagnosis) is True, "proposal alone passes the policy"
    assert supervisor.apply_recovery(mock_browser, diagnosis) is False
    assert not [c for c in mock_browser.call.call_args_list if c.args[:1] == ("Input.dispatchMouseEvent",)]
    supervisor.close()


def test_apply_recovery_click_text_rejects_missing_resolved_text():
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = Mock()
    mock_browser.evaluate.side_effect = (
        lambda expr: "complete:1000" if expr == SETTLE_PROBE else {"x": 10, "y": 10}
    )
    diagnosis = {"can_auto_recover": True, "action_type": "CLICK_TEXT", "target_text": "Close"}
    assert supervisor.apply_recovery(mock_browser, diagnosis) is False
    assert mock_browser.call.call_count == 0
    supervisor.close()


def test_click_text_resolver_anchors_the_match():
    """A substring match lets a safe prefix select an unrelated element."""
    supervisor = GLMSupervisor(api_key="valid-key")
    captured = []

    def evaluate(expr):
        if expr == SETTLE_PROBE:
            return "complete:1000"
        captured.append(expr)
        return None

    mock_browser = Mock()
    mock_browser.evaluate.side_effect = evaluate
    supervisor.apply_recovery(
        mock_browser, {"can_auto_recover": True, "action_type": "CLICK_TEXT", "target_text": "OK"}
    )
    script = captured[0]
    assert "startsWith(text)" in script
    assert "includes(text)" not in script
    assert '"ok"' in script
    supervisor.close()


def test_screen_click_text_limits():
    assert screen_click_text("Accept all cookies", limit=40, source="t") is True
    assert screen_click_text("Accept all cookies", limit=5, source="t") is False
    assert screen_click_text(None, limit=40, source="t") is False
    assert screen_click_text("   ", limit=40, source="t") is False


def test_apply_recovery_reload_with_navigation_detection():
    """Verify RELOAD waits for new document timeOrigin before DOM settling."""
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = Mock()
    mock_browser.evaluate.side_effect = [
        1000.0,          # before reload
        2000.0,          # new document timeOrigin (> before)
        "complete:5000",  # _settle read 1
        "complete:5000",  # _settle read 2
        "complete:5000",  # _settle read 3 -> stable >= 2
    ]

    diagnosis = {
        "can_auto_recover": True,
        "action_type": "RELOAD",
    }

    assert supervisor.apply_recovery(mock_browser, diagnosis) is True
    mock_browser.call.assert_called_once_with("Page.reload")
    supervisor.close()


def test_settle_helper_dom_stability():
    """Verify DOM settle helper requires 2 consecutive identical complete matches."""
    mock_browser = Mock()

    # 3 identical reads (2 consecutive matches) returns True
    mock_browser.evaluate.side_effect = ["complete:1234", "complete:1234", "complete:1234"]
    assert _settle(mock_browser, max_timeout=0.5, interval=0.01) is True

    # Mutating reads reset stability counter
    mock_browser.evaluate.side_effect = [
        "loading:0",
        "complete:100",
        "complete:200",
        "complete:200",
        "complete:200",
    ]
    assert _settle(mock_browser, max_timeout=0.5, interval=0.01) is True

    # CDP / evaluate exception returns False and logs
    mock_browser.evaluate.side_effect = RuntimeError("CDP disconnect")
    assert _settle(mock_browser, max_timeout=0.2, interval=0.01) is False

    # Non-string evaluate returns False (no backdoor)
    mock_browser.evaluate.side_effect = None
    mock_browser.evaluate.return_value = {"x": 100}
    assert _settle(mock_browser, max_timeout=0.2, interval=0.01) is False


def test_settle_probe_does_not_serialise_the_dom():
    """Polling innerHTML serialises megabytes per read on a real page."""
    assert "innerHTML" not in SETTLE_PROBE
    assert "outerHTML" not in SETTLE_PROBE
    assert SETTLE_PROBE.index("document.readyState") < SETTLE_PROBE.index("join")

    mock_browser = Mock()
    mock_browser.evaluate.return_value = "complete:812:2400:Example"
    assert _settle(mock_browser, max_timeout=0.5, interval=0.01) is True
    assert {c.args[0] for c in mock_browser.evaluate.call_args_list} == {SETTLE_PROBE}


def test_validate_recovery_action_chinese_safe_patterns():
    """B1 Fix: Chinese safe recovery buttons must be accepted without \\b word boundary failure."""
    chinese_safe = [
        "同意並繼續",
        "同意全部",
        "接受全部",
        "接受全部 Cookie",
        "確定",
        "确定",
        "關閉視窗",
        "关闭",
        "知道了",
        "繼續操作",
        "继续",
        "稍後再說",
        "稍后提醒",
        "略過此步",
        "略过",
        "允許所有",
        "允许",
        "我知道了",
        "好的",
        "全部接受",
        "全部同意",
        "全部允許",
        "全部允许",
        "我同意",
        "我接受",
        "朕知道",
        "朕知道了",
    ]
    for text in chinese_safe:
        assert validate_recovery_action({"action_type": "CLICK_TEXT", "target_text": text}), f"Failed on: {text}"


def test_validate_recovery_action_chinese_dangerous_patterns_blocked():
    """B1 Security: Dangerous operations in Chinese must NEVER pass even if starting with safe prefix."""
    chinese_dangerous = [
        "同意並刪除帳戶",
        "同意並删除",
        "接受並付款",
        "確定結帳",
        "确定结账",
        "確定購買",
        "确定购买",
        "確定登出",
        "確定轉帳",
        "确定转账",
        "確定下單",
        "确定下单",
        "確定註銷",
        "全部接受並付款",
        "全部同意刪除",
        "我同意刪除帳戶",
        "我接受付款",
        "朕知道但要付款",
    ]
    for text in chinese_dangerous:
        assert not validate_recovery_action(
            {"action_type": "CLICK_TEXT", "target_text": text}
        ), f"Allowed dangerous: {text}"


def test_validate_recovery_action_english_patterns():
    english_safe = [
        "Accept all",
        "Accept and continue",
        "Agree to all cookies",
        "Allow cookies",
        "OK",
        "Okay",
        "Got it",
        "Close modal",
        "Dismiss banner",
        "Continue",
        "Confirm selection",
        "Not now",
        "Skip tutorial",
        "No thanks",
    ]
    for text in english_safe:
        assert validate_recovery_action({"action_type": "CLICK_TEXT", "target_text": text}), f"Failed on: {text}"

    english_dangerous = [
        "Accept and delete account",
        "Confirm payment",
        "Checkout now",
        "Buy item",
        "Transfer money",
        "Logout",
        "Sign out",
    ]
    for text in english_dangerous:
        assert not validate_recovery_action(
            {"action_type": "CLICK_TEXT", "target_text": text}
        ), f"Allowed dangerous: {text}"


def test_validate_recovery_action_edge_cases():
    assert not validate_recovery_action(None)
    assert not validate_recovery_action({})
    assert not validate_recovery_action({"action_type": "INVALID"})
    assert not validate_recovery_action({"action_type": "PRESS_KEY", "key_name": "F12"})
    assert not validate_recovery_action({"action_type": "CLICK_TEXT", "target_text": ""})
    assert not validate_recovery_action({"action_type": "CLICK_TEXT", "target_text": "a" * 50})
    assert not validate_recovery_action({"action_type": "SCROLL", "scroll_delta": "lots"})
    assert not validate_recovery_action({"action_type": "SCROLL", "scroll_delta": 99999})
    assert validate_recovery_action({"action_type": "RELOAD"})
    assert validate_recovery_action({"action_type": "HUMAN_INTERVENTION"})


def test_agent_resume_preserves_valid_operation_enum():
    """P1: Verify resume records valid operation enum WAIT and supervisor_action."""
    from jev_ultrafast.agent import Agent

    with patch("jev_ultrafast.agent.Browser") as MockBrowser:
        mock_instance = MockBrowser.return_value
        mock_instance.observe.return_value = {
            "url": "https://example.com",
            "actions": [{"id": "wait", "kind": "wait", "label": "Wait"}],
        }
        agent = Agent("https://example.com", "Test goal")
        agent.resume(reason="Cookie modal", action_type="CLICK_TEXT", note="Accept all")

        last_action = agent.state["history"][-1]
        assert last_action["operation"] == "WAIT"
        assert last_action["supervisor_action"] == "CLICK_TEXT"
        assert last_action["confidence"] is None
        assert last_action["probability"] is None
        assert agent.state["status"] == "ready"
        agent.close()


def test_an_empty_reply_names_its_cause_instead_of_a_json_type_error():
    """A reasoning model can spend the whole budget thinking and answer with content=None.

    json.loads on that raises "the JSON object must be str, bytes or bytearray, not NoneType",
    which the supervisor then reports as the page's obstacle -- its own failure dressed up as
    a diagnosis.
    """
    from jev_ultrafast.supervisor import _json_reply

    exhausted = {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}
    with pytest.raises(ValueError, match="no content"):
        _json_reply(exhausted, "Visual diagnosis")
    with pytest.raises(ValueError, match="finish_reason='length'"):
        _json_reply(exhausted, "Visual diagnosis")

    assert _json_reply({"choices": [{"message": {"content": '{"ok": true}'}}]}, "x") == {"ok": True}
    with pytest.raises(ValueError, match="no content"):
        _json_reply({}, "x")


@pytest.mark.parametrize(
    ("label", "allowed"),
    [
        # The most common dismiss control carries no word at all.
        ("×", True), ("✕", True), ("✗", True), ("X", True), ("x", True),
        ("同意", True), ("允許", True), ("Close", True), ("Not now", True),
        # One character only: anything longer must still earn its way past the word patterns.
        ("xx", False), ("X-ray machine", False),
        # Recovery dismisses obstacles; advancing the task is the policy's job, not this one.
        ("2房", False), ("搜尋", False), ("刷新列表", False),
        # And the confused-deputy list still wins over every pattern above.
        ("刪除", False), ("Buy now", False), ("Delete account", False), ("結帳", False),
    ],
)
def test_only_dismissal_labels_may_be_auto_clicked(label, allowed):
    from jev_ultrafast.supervisor import screen_click_text

    assert screen_click_text(label, limit=40, source="target_text") is allowed

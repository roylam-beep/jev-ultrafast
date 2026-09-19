"""Hardened test suite for GLM-5.3-Flash Multimodal Visual Supervisor."""

import json
from unittest.mock import Mock, patch

import httpx

from jev_ultrafast.supervisor import GLMSupervisor, validate_recovery_action


def test_supervisor_initialization(monkeypatch):
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
    """Security test: prompt injection attempt from web page must be rejected."""
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

    diagnosis = {
        "can_auto_recover": True,
        "action_type": "PRESS_KEY",
        "key_name": "Escape",
    }

    assert supervisor.apply_recovery(mock_browser, diagnosis) is True
    assert mock_browser.call.call_count == 2
    mock_browser.call.assert_any_call(
        "Input.dispatchKeyEvent",
        type="keyDown",
        key="Escape",
        code="Escape",
        windowsVirtualKeyCode=27,
        nativeVirtualKeyCode=27,
    )
    mock_browser.call.assert_any_call(
        "Input.dispatchKeyEvent",
        type="keyUp",
        key="Escape",
        code="Escape",
        windowsVirtualKeyCode=27,
        nativeVirtualKeyCode=27,
    )
    supervisor.close()


def test_apply_recovery_scroll_dynamic_viewport():
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = Mock()
    mock_browser.evaluate.return_value = {"x": 600, "y": 450}

    diagnosis = {
        "can_auto_recover": True,
        "action_type": "SCROLL",
        "scroll_delta": 450,
    }

    assert supervisor.apply_recovery(mock_browser, diagnosis) is True
    mock_browser.call.assert_called_once_with(
        "Input.dispatchMouseEvent", type="mouseWheel", x=600, y=450, deltaX=0, deltaY=450
    )
    supervisor.close()


def test_apply_recovery_click_text_success():
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = Mock()
    mock_browser.evaluate.return_value = {"x": 200, "y": 300}

    diagnosis = {
        "can_auto_recover": True,
        "action_type": "CLICK_TEXT",
        "target_text": "Close",
    }

    assert supervisor.apply_recovery(mock_browser, diagnosis) is True
    mock_browser.call.assert_any_call(
        "Input.dispatchMouseEvent", type="mousePressed", x=200, y=300, button="left", clickCount=1
    )
    mock_browser.call.assert_any_call(
        "Input.dispatchMouseEvent", type="mouseReleased", x=200, y=300, button="left", clickCount=1
    )
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


def test_validate_recovery_action_coverage():
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

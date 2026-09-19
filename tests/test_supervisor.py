"""Tests for GLM-5.3-Flash Multimodal Visual Supervisor."""

import json
from unittest.mock import Mock, patch

from jev_ultrafast.supervisor import GLMSupervisor


def test_supervisor_initialization(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test-key")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.setenv("TEXT_MODEL", "glm-5.3-flash")

    supervisor = GLMSupervisor()
    assert supervisor.is_configured()
    assert supervisor.api_key == "test-key"
    assert supervisor.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert supervisor.model == "glm-5.3-flash"


def test_diagnose_unconfigured():
    supervisor = GLMSupervisor(api_key="")
    res = supervisor.diagnose("fake_b64", "test goal")
    assert res["can_auto_recover"] is False
    assert res["action_type"] == "HUMAN_INTERVENTION"


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
        res = supervisor.diagnose("fake_b64", "Find flights", [{"action": "click search"}])
        assert res["obstacle_type"] == "BANNER_OVERLAY"
        assert res["can_auto_recover"] is True
        assert res["action_type"] == "CLICK_TEXT"
        assert res["target_text"] == "Accept all"


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


def test_apply_recovery_keypress():
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = Mock()

    diagnosis = {
        "can_auto_recover": True,
        "action_type": "PRESS_KEY",
        "key_name": "Escape",
    }

    assert supervisor.apply_recovery(mock_browser, diagnosis) is True
    assert mock_browser.call.call_count == 2
    mock_browser.call.assert_any_call("Input.dispatchKeyEvent", type="rawKeyDown", key="Escape", code="Escape")
    mock_browser.call.assert_any_call("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape")


def test_apply_recovery_scroll():
    supervisor = GLMSupervisor(api_key="valid-key")
    mock_browser = Mock()

    diagnosis = {
        "can_auto_recover": True,
        "action_type": "SCROLL",
        "scroll_delta": 450,
    }

    assert supervisor.apply_recovery(mock_browser, diagnosis) is True
    mock_browser.call.assert_called_once_with(
        "Input.dispatchMouseEvent", type="mouseWheel", x=550, y=400, deltaX=0, deltaY=450
    )


def test_apply_recovery_click_text():
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

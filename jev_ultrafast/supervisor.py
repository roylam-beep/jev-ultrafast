"""GLM-5.3-Flash Multimodal Visual Supervisor.

Provides visual diagnosis, deadlock recovery, and final outcome verification
for the ultra-fast Jev agent loop.
"""

import json
import logging
import os
import time

import httpx

logger = logging.getLogger("jev_ultrafast.supervisor")


class GLMSupervisor:
    """Supervises Jev execution using GLM-5.3-Flash's native multimodal intelligence."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = (
            api_key
            or os.environ.get("VISION_MODEL_API_KEY")
            or os.environ.get("TEXT_MODEL_API_KEY")
        )
        self.base_url = (
            base_url
            or os.environ.get("VISION_MODEL_BASE_URL")
            or os.environ.get("TEXT_MODEL_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        ).rstrip("/")
        self.model = (
            model
            or os.environ.get("VISION_MODEL")
            or os.environ.get("TEXT_MODEL", "glm-5.3-flash")
        )
        self.client = httpx.Client(timeout=45)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def diagnose(
        self,
        screenshot_b64: str,
        goal: str,
        recent_actions: list[dict] | None = None,
        error_hint: str | None = None,
    ) -> dict:
        """Diagnoses why the browser is stuck or blocked using GLM-5.3-Flash vision."""
        if not self.is_configured():
            return {
                "stuck_reason": "Supervisor API key not configured.",
                "can_auto_recover": False,
                "action_type": "HUMAN_INTERVENTION",
                "details": "Missing API key for visual diagnosis.",
            }

        actions_summary = (
            "\n".join([f"- {a.get('action', 'unknown')} ({a.get('kind', '')})" for a in (recent_actions or [])[-5:]])
            if recent_actions
            else "None"
        )

        system_prompt = (
            "You are an expert GUI and Web Automation diagnostician. "
            "An ultrafast browser agent was operating on the user's goal but has become STUCK or BLOCKED. "
            "Inspect the attached screenshot, the overall goal, and the recent actions. "
            "Identify the obstacle (such as an overlapping modal/cookie banner, CAPTCHA, hidden elements, "
            "validation error, or unclosed popup) and determine a recovery action.\n\n"
            "You MUST reply with ONLY a valid JSON object with the following schema:\n"
            "{\n"
            '  "obstacle_type": "BANNER_OVERLAY" | "CAPTCHA" | "VALIDATION_ERROR" | "DEADLOCK" | "UNKNOWN",\n'
            '  "stuck_reason": "<1-2 sentences explaining what is blocking the page>",\n'
            '  "can_auto_recover": true | false,\n'
            '  "action_type": "CLICK_TEXT" | "PRESS_KEY" | "SCROLL" | "RELOAD" | "HUMAN_INTERVENTION",\n'
            '  "target_text": "<exact button or text label to click if CLICK_TEXT, else null>",\n'
            '  "key_name": "<Escape or Enter if PRESS_KEY, else null>",\n'
            '  "scroll_delta": <number if SCROLL, else null>\n'
            "}"
        )

        user_content = [
            {
                "type": "text",
                "text": (
                    f"Overall Goal: {goal}\n"
                    f"Recent Actions:\n{actions_summary}\n"
                    f"Error Hint: {error_hint or 'Repeated stagnant state or BLOCKED'}\n\n"
                    "Analyze the screenshot and provide recovery instructions in JSON."
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"},
            },
        ]

        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }

        try:
            resp = self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            raw_content = data["choices"][0]["message"]["content"]
            return json.loads(raw_content)
        except Exception as e:
            logger.warning("GLM visual diagnosis error: %s", e)
            return {
                "obstacle_type": "UNKNOWN",
                "stuck_reason": f"Visual diagnosis API call failed: {e}",
                "can_auto_recover": False,
                "action_type": "HUMAN_INTERVENTION",
            }

    def verify_goal_achievement(self, screenshot_b64: str, goal: str) -> dict:
        """Verifies if the final visible screenshot truly satisfies the goal."""
        if not self.is_configured():
            return {
                "satisfied": True,
                "confidence": 0.5,
                "explanation": "Supervisor not configured; passed by default.",
            }

        system_prompt = (
            "You are a rigorous quality assurance auditor for browser tasks. "
            "An automated agent claims it has FINISHED the user goal. "
            "Check the attached screenshot and verify whether the requested information or state "
            "is VISIBLY present.\n\n"
            "You MUST reply with ONLY a JSON object:\n"
            "{\n"
            '  "satisfied": true | false,\n'
            '  "confidence": <float 0.0 to 1.0>,\n'
            '  "explanation": "<brief rationale of what is or is not visible>"\n'
            "}"
        )

        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Goal: {goal}\nVisually audit this result screenshot."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
                    ],
                },
            ],
        }

        try:
            resp = self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            raw_content = data["choices"][0]["message"]["content"]
            return json.loads(raw_content)
        except Exception as e:
            logger.warning("GLM goal verification error: %s", e)
            return {"satisfied": True, "confidence": 0.5, "explanation": f"Audit skipped due to error: {e}"}

    def apply_recovery(self, browser, diagnosis: dict) -> bool:
        """Applies the recommended recovery action directly into the browser session."""
        action_type = diagnosis.get("action_type")
        if not diagnosis.get("can_auto_recover") or not action_type:
            return False

        try:
            if action_type == "PRESS_KEY":
                key = diagnosis.get("key_name", "Escape")
                code = f"Key{key}" if len(key) == 1 else key
                browser.call("Input.dispatchKeyEvent", type="rawKeyDown", key=key, code=code)
                time.sleep(0.05)
                browser.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=code)
                return True

            elif action_type == "SCROLL":
                delta = diagnosis.get("scroll_delta", 300)
                browser.call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=400, deltaX=0, deltaY=delta)
                time.sleep(0.1)
                return True

            elif action_type == "CLICK_TEXT":
                target_text = diagnosis.get("target_text", "")
                if not target_text:
                    return False
                # Find element with matching text or aria-label and click its center
                script = f"""(() => {{
                    const text = {json.dumps(target_text.lower())};
                    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], span, div'));
                    for (const el of candidates) {{
                        const content = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
                        if (content.includes(text) && el.checkVisibility && el.checkVisibility()) {{
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {{
                                return {{ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }};
                            }}
                        }}
                    }}
                    return null;
                }})()"""
                coords = browser.evaluate(script)
                if coords:
                    browser.call(
                        "Input.dispatchMouseEvent",
                        type="mousePressed",
                        x=coords["x"],
                        y=coords["y"],
                        button="left",
                        clickCount=1,
                    )
                    time.sleep(0.05)
                    browser.call(
                        "Input.dispatchMouseEvent",
                        type="mouseReleased",
                        x=coords["x"],
                        y=coords["y"],
                        button="left",
                        clickCount=1,
                    )
                    return True

            elif action_type == "RELOAD":
                browser.call("Page.reload")
                time.sleep(1.0)
                return True

        except Exception as e:
            logger.warning("Failed to apply recovery action: %s", e)

        return False

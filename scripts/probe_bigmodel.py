#!/usr/bin/env python3
"""
Probe and verify Zhipu BigModel / OpenAI-compatible endpoint compatibility:
1. Text JSON completions
2. Multimodal vision format and parameters
"""

import os
import sys

import httpx


def probe():
    api_key = os.environ.get("TEXT_MODEL_API_KEY") or os.environ.get("VISION_MODEL_API_KEY")
    if not api_key:
        print("❌ Error: Please set TEXT_MODEL_API_KEY or VISION_MODEL_API_KEY in your environment.")
        sys.exit(1)

    base_url = (os.environ.get("TEXT_MODEL_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    model = os.environ.get("TEXT_MODEL") or "glm-5.3-flash"

    client = httpx.Client(timeout=20)
    print(f"📡 Probing endpoint: {base_url}/chat/completions (Model: {model})")

    # 1. Text JSON probe
    print("\n[1/2] Testing Text JSON completion...")
    try:
        resp = client.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "user", "content": 'Return a JSON with key "status" and value "ok"'}
                ],
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"✅ Text JSON Success: {data['choices'][0]['message']['content']}")
    except Exception as e:
        print(f"❌ Text JSON Failed: {e}")

    # 2. Vision probe with a 1x1 black pixel PNG data URI
    print("\n[2/2] Testing Multimodal Vision probe...")
    pixel_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    try:
        resp = client.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "max_tokens": 128,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": 'Respond with JSON {"vision": "verified"}'},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{pixel_png}"}},
                        ],
                    }
                ],
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"✅ Vision Probe Success: {data['choices'][0]['message']['content']}")
    except Exception as e:
        print(f"❌ Vision Probe Failed: {e}")


if __name__ == "__main__":
    probe()

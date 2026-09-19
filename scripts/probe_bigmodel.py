#!/usr/bin/env python3
"""
Probe and verify Zhipu BigModel / OpenAI-compatible endpoint compatibility:
1. Text JSON completions
2. Multimodal vision format with System Role and JPEG/PNG payloads
3. Error response diagnostics
"""

import os
import sys

import httpx


def probe():
    api_key = os.environ.get("TEXT_MODEL_API_KEY") or os.environ.get("VISION_MODEL_API_KEY")
    if not api_key:
        print("❌ Error: Please set TEXT_MODEL_API_KEY or VISION_MODEL_API_KEY in your environment.")
        sys.exit(1)

    base_url = (
        os.environ.get("VISION_MODEL_BASE_URL")
        or os.environ.get("TEXT_MODEL_BASE_URL")
        or "https://open.bigmodel.cn/api/paas/v4"
    ).rstrip("/")
    model = (
        os.environ.get("VISION_MODEL")
        or os.environ.get("TEXT_MODEL")
        or "glm-5.3-flash"
    )

    client = httpx.Client(timeout=25)
    print(f"📡 Probing endpoint: {base_url}/chat/completions (Model: {model})")

    # 1. Text JSON probe
    print("\n[1/3] Testing Text JSON completion...")
    try:
        resp = client.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "You are a helpful JSON generator."},
                    {"role": "user", "content": 'Return a JSON with key "status" and value "ok"'},
                ],
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"✅ Text JSON Success: {data['choices'][0]['message']['content']}")
    except httpx.HTTPStatusError as e:
        print(f"❌ Text JSON HTTP {e.response.status_code}: {e.response.text[:800]}")
    except Exception as e:
        print(f"❌ Text JSON Failed: {e}")

    # 1x1 black JPEG base64 (matching CDP Page.captureScreenshot format)
    pixel_jpeg = (
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP/////////////////////////////////////////////////"
        "/////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAA"
        "AP/aAAgBAQABPxA="
    )

    # 2. Vision probe with System Role + JPEG Data URI
    print("\n[2/3] Testing Multimodal Vision (System Role + JPEG Data URI)...")
    try:
        resp = client.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "max_tokens": 128,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an automated vision quality auditor. Output JSON.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": 'Respond with JSON {"vision": "verified"}'},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{pixel_jpeg}"},
                            },
                        ],
                    },
                ],
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"✅ Vision JPEG Probe Success: {data['choices'][0]['message']['content']}")
    except httpx.HTTPStatusError as e:
        print(f"❌ Vision JPEG HTTP {e.response.status_code}: {e.response.text[:800]}")
    except Exception as e:
        print(f"❌ Vision JPEG Failed: {e}")

    # 3. Vision probe with Pure Base64 (fallback verification)
    print("\n[3/3] Testing Multimodal Vision (Pure Base64 payload)...")
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
                            {"type": "text", "text": 'Respond with JSON {"pure_b64": "ok"}'},
                            {"type": "image_url", "image_url": {"url": pixel_jpeg}},
                        ],
                    }
                ],
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.is_success:
            print(f"✅ Pure Base64 Supported: {resp.json()['choices'][0]['message']['content']}")
        else:
            print(f"ℹ️  Pure Base64 not supported (HTTP {resp.status_code}), Data URI standard required.")
    except Exception as e:
        print(f"ℹ️  Pure Base64 probe note: {e}")


if __name__ == "__main__":
    probe()

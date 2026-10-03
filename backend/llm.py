import os
import json
import re

from google import genai


MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

_client = None


def get_client():
    global _client

    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured in backend/.env"
            )

        _client = genai.Client(api_key=api_key)

    return _client


def llm_json(prompt, max_tokens=4000):
    client = get_client()

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )

    text = (response.text or "").strip()

    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    try:
        return json.loads(text)

    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)

        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        raise ValueError(
            f"Model returned invalid JSON: {text[:1000]}"
        )
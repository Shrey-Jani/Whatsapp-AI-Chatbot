"""Provider-agnostic text completion. Set LLM_PROVIDER=gemini|groq in .env.

All LLM traffic funnels through complete() so swapping providers never touches business logic.
Callers are responsible for their own fallback — this raises on failure rather than guessing.
"""
import httpx

from .config import settings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def configured() -> bool:
    """True when the selected provider has a key set."""
    return bool(settings.groq_api_key if settings.llm_provider == "groq"
                else settings.gemini_api_key)


def complete(prompt: str) -> str:
    if settings.llm_provider == "groq":
        return _groq(prompt)
    return _gemini(prompt)


def _gemini(prompt: str) -> str:
    from google import genai
    client = genai.Client(api_key=settings.gemini_api_key)
    return client.models.generate_content(model=settings.gemini_model, contents=prompt).text


def _groq(prompt: str) -> str:
    """Groq speaks the OpenAI chat format — plain httpx, no extra SDK needed."""
    r = httpx.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={"model": settings.groq_model,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

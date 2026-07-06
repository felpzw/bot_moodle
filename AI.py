"""Core de respostas via Gemini.

Expõe uma única função pública:
    await responder(prompt) -> str
"""

from __future__ import annotations

import os

from google import genai
from google.genai import types

_SYSTEM_PROMPT = (
    "Você é um assistente acadêmico para a disciplina de Fenômenos de Transporte "
)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY não definido")

    _client = genai.Client(api_key=api_key)
    return _client


async def responder(prompt: str) -> str:
    client = _get_client()
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    resp = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=_SYSTEM_PROMPT),
    )
    return resp.text

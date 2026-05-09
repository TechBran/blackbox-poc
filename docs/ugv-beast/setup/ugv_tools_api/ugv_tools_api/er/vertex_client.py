"""Thin async wrapper around google.genai Vertex client with 429 surfacing."""
import asyncio
from typing import Any, Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from . import config


class RateLimitError(RuntimeError):
    pass


class VertexUnavailableError(RuntimeError):
    pass


_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    # API key (Gemini Developer API) wins over Vertex unless ER_USE_VERTEX=1.
    # BlackBox's ER 1.5 has always used the API key; the robotics-er preview
    # models are reliably available there. Vertex needs explicit allow-listing
    # per-project and the preview may 404 even when the key path works.
    global _client
    if _client is None:
        if config.GOOGLE_API_KEY and not config.USE_VERTEX:
            _client = genai.Client(api_key=config.GOOGLE_API_KEY)
        else:
            kwargs: dict[str, Any] = {"vertexai": True, "location": config.GOOGLE_CLOUD_LOCATION}
            if config.GOOGLE_CLOUD_PROJECT:
                kwargs["project"] = config.GOOGLE_CLOUD_PROJECT
            _client = genai.Client(**kwargs)
    return _client


async def generate_content_async(
    contents: list[Any],
    tools: list[Any],
    config_obj: Optional[genai_types.GenerateContentConfig] = None,
    timeout: float = config.VERTEX_RPC_TIMEOUT_S,
    model: str = config.ER_MODEL_ID,
):
    client = get_client()
    gen_config = config_obj or genai_types.GenerateContentConfig()
    if tools:
        gen_config.tools = tools

    def _call():
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=gen_config,
        )

    try:
        return await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout)
    except asyncio.TimeoutError as e:
        raise VertexUnavailableError(f"Vertex RPC timed out after {timeout}s") from e
    except genai_errors.APIError as e:
        code = getattr(e, "code", None) or getattr(e, "status_code", None)
        if code == 429 or "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e).upper():
            raise RateLimitError(str(e)) from e
        raise


async def list_models_async() -> list[str]:
    client = get_client()

    def _call():
        out = []
        for m in client.models.list():
            name = getattr(m, "name", None) or getattr(m, "model", None)
            if name:
                out.append(str(name))
        return out

    return await asyncio.to_thread(_call)

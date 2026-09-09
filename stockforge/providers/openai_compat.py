"""OpenAI-compatible vision backend.

This one class covers almost everything you would actually run locally:

  NVIDIA NIM      docker run ... nvcr.io/nim/...  ->  http://localhost:8000/v1
  vLLM            vllm serve <model> --port 8000  ->  http://localhost:8000/v1
  Ollama          ollama serve                    ->  http://localhost:11434/v1
  LM Studio       local server                    ->  http://localhost:1234/v1
  build.nvidia.com (hosted NIM)                   ->  https://integrate.api.nvidia.com/v1

They all speak the same chat-completions shape with base64 image_url parts, so
there is one implementation and a base URL. No vendor SDK, no lock-in, and
nothing to rewrite when you swap the model.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .base import ProviderError, VisionProvider, encode_image

log = logging.getLogger("stockforge.providers")


class OpenAICompatProvider(VisionProvider):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: int = 300,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        max_image_edge: int = 1280,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_image_edge = max_image_edge
        self.name = f"{model}@{self.base_url}"

    def chat(self, system: str, user_text: str, images: list[Path], **kw: Any) -> str:
        parts: list[dict] = []
        for path in images:
            b64, mime = encode_image(path, self.max_image_edge)
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        parts.append({"type": "text", "text": user_text})

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": parts},
            ],
            "temperature": kw.get("temperature", self.temperature),
            "max_tokens": kw.get("max_tokens", self.max_tokens),
        }
        # Servers that support it will honour this and save us a repair round.
        # Ones that do not ignore it, so it is safe to send unconditionally.
        if kw.get("json_mode", True):
            payload["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            # some servers reject response_format — retry once without it
            if exc.code == 400 and "response_format" in detail and kw.get("json_mode", True):
                return self.chat(system, user_text, images, **{**kw, "json_mode": False})
            raise ProviderError(f"{self.name} HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"{self.name} unreachable: {exc.reason}") from exc
        except (TimeoutError, OSError, ValueError) as exc:
            raise ProviderError(f"{self.name} could not read a response: {exc}") from exc

        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") == "length":
                budget = payload["max_tokens"]
                if not kw.get("_length_retry") and budget < 32768:
                    larger = min(32768, budget * 4)
                    log.warning("[%s] response reached %d tokens; retrying once with %d",
                                self.model, budget, larger)
                    return self.chat(system, user_text, images,
                                     **{**kw, "max_tokens": larger, "_length_retry": True})
                raise ProviderError(f"{self.model} reached its response limit before finishing. "
                                    "Increase SF_VISION_MAX_TOKENS or choose another model in Setup, then retry the design from Review.")
            if not isinstance(content, str) or not content.strip():
                raise ProviderError(f"{self.model} returned no final answer. "
                                    "Retry the design from Review or choose another model in Setup.")
            return content
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(f"{self.name} odd response: {str(body)[:300]}") from exc


def from_env(prefix: str = "SF_VISION") -> OpenAICompatProvider:
    """Build a provider from environment. Two knobs and you are running.

        SF_VISION_BASE_URL=http://localhost:8000/v1
        SF_VISION_MODEL=nvidia/llama-3.2-90b-vision-instruct
    """
    base = os.environ.get(f"{prefix}_BASE_URL", "http://localhost:8000/v1")
    model = os.environ.get(f"{prefix}_MODEL")
    if not model:
        raise ProviderError(f"set {prefix}_MODEL to the model your server is serving")
    return OpenAICompatProvider(
        base_url=base,
        model=model,
        api_key=os.environ.get(f"{prefix}_API_KEY"),
        timeout=int(os.environ.get(f"{prefix}_TIMEOUT", 300)),
        max_tokens=int(os.environ.get(f"{prefix}_MAX_TOKENS", 4096)),
        max_image_edge=int(os.environ.get(f"{prefix}_MAX_EDGE", 1280)),
    )

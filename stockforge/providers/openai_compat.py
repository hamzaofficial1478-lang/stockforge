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
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .base import ProviderError, VisionProvider, encode_image
from .registry import Backend, Preset, register

log = logging.getLogger("stockforge.providers")


def model_options(model: str) -> dict:
    """NVIDIA's Muse Glimmer sampling defaults, with bounded reasoning effort."""
    if model.lower() == "meta/muse-glimmer-30b":
        return {"temperature": 0.95, "top_p": 1.0, "reasoning_effort": "low"}
    return {}


# Codes worth trying again. A 500 from a hosted NIM usually means the worker
# behind it fell over on this particular request, not that the request was
# wrong — NVIDIA's own error says "internal error while making inference
# request" and nothing more. Retrying the identical request often works.
_TRANSIENT = {408, 409, 425, 429, 500, 502, 503, 504}


class _HTTPFail(Exception):
    def __init__(self, code: int, detail: str, retry_after: float | None = None):
        super().__init__(f"HTTP {code}: {detail}")
        self.code = code
        self.detail = detail
        self.retry_after = retry_after


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
        max_image_bytes: int = 180_000,
        retries: int = 2,
        backoff: float = 2.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_image_edge = max_image_edge
        self.max_image_bytes = max_image_bytes
        self.retries = retries
        self.backoff = backoff
        self.name = f"{model}@{self.base_url}"

    # -- one request over the wire ------------------------------------

    def _post(self, payload: dict) -> dict:
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
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                after = float(after) if after else None
            except ValueError:
                after = None
            raise _HTTPFail(exc.code, detail, after) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"{self.name} unreachable: {exc.reason}") from exc
        except (TimeoutError, OSError, ValueError) as exc:
            raise ProviderError(f"{self.name} could not read a response: {exc}") from exc

    def _payload(self, system: str, user_text: str, images: list[Path],
                 json_mode: bool, extras: bool, edge: int, budget: int,
                 temperature: float, max_tokens: int) -> dict:
        parts: list[dict] = []
        for path in images:
            b64, mime = encode_image(path, edge, budget)
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        parts.append({"type": "text", "text": user_text})

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": parts},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if extras:
            payload.update(model_options(self.model))
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    # -- the ladder ----------------------------------------------------

    def chat(self, system: str, user_text: str, images: list[Path], **kw: Any) -> str:
        temperature = kw.get("temperature", self.temperature)
        max_tokens = kw.get("max_tokens", self.max_tokens)
        json_mode = kw.get("json_mode", True)

        # Three rungs, each a smaller ask than the last. We only step down for
        # failures that might be about the request rather than about us: a 400
        # is a straight answer and is reported as one.
        rungs = [
            dict(json_mode=json_mode, extras=True, edge=self.max_image_edge,
                 budget=self.max_image_bytes),
            dict(json_mode=False, extras=False, edge=self.max_image_edge,
                 budget=self.max_image_bytes),
            dict(json_mode=False, extras=False, edge=min(self.max_image_edge, 640),
                 budget=min(self.max_image_bytes, 120_000)),
        ]
        if not json_mode:
            rungs = rungs[1:]

        started = time.monotonic()
        last: _HTTPFail | None = None

        for step, rung in enumerate(rungs):
            payload = self._payload(system, user_text, images,
                                    temperature=temperature, max_tokens=max_tokens, **rung)
            for attempt in range(self.retries + 1):
                try:
                    body = self._post(payload)
                except _HTTPFail as fail:
                    # A server that does not know response_format says so with a
                    # 400. That is not a fault, it is a dialect — drop it and
                    # carry on down the ladder.
                    if fail.code == 400 and "response_format" in fail.detail and rung["json_mode"]:
                        last = fail
                        break
                    if fail.code not in _TRANSIENT:
                        raise ProviderError(f"{self.name} HTTP {fail.code}: {fail.detail}") from fail
                    last = fail
                    if attempt < self.retries:
                        pause = fail.retry_after or self.backoff * (2 ** attempt)
                        log.warning("[%s] HTTP %d — retrying in %.0fs (attempt %d of %d)",
                                    self.model, fail.code, pause, attempt + 1, self.retries + 1)
                        time.sleep(pause)
                    continue
                else:
                    if step:
                        log.info("[%s] succeeded after stepping the request down", self.model)
                    return self._answer(body, payload, system, user_text, images, kw, started)
            if last and last.code in _TRANSIENT and step + 1 < len(rungs):
                log.warning("[%s] HTTP %d persisted — retrying with a smaller request",
                            self.model, last.code)

        detail = last.detail if last else "no response"
        code = last.code if last else 0
        raise ProviderError(
            f"{self.name} HTTP {code}: {detail}\n"
            f"This kept failing after {len(rungs)} attempts at decreasing size. "
            f"A 500 here is the server's own fault, not the design's — it is usually "
            f"the hosted endpoint being busy or the model being cold. Wait a few minutes "
            f"and retry the design from Review, or pick another model in Setup."
        )

    # -- reading the reply ---------------------------------------------

    def _answer(self, body: dict, payload: dict, system: str, user_text: str,
                images: list[Path], kw: dict, started: float) -> str:
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
            log.info("[%s] response in %.1fs; finish=%s; output tokens=%s",
                     self.model, time.monotonic() - started, choice.get("finish_reason", "unknown"),
                     (body.get("usage") or {}).get("completion_tokens", "unknown"))
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
        except (KeyError, IndexError, TypeError) as exc:
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
        max_image_bytes=int(os.environ.get(f"{prefix}_MAX_IMAGE_BYTES", 180_000)),
        retries=int(os.environ.get(f"{prefix}_RETRIES", 2)),
        backoff=float(os.environ.get(f"{prefix}_BACKOFF", 2.0)),
    )


# --------------------------------------------------------------------------
# what this backend is, for the registry
# --------------------------------------------------------------------------

# Addresses only. What a given model can do is not guessed at here — Setup's
# Test button sends a real image and finds out, which beats a table that goes
# stale the week after it is written. Hermes, Qwen, Llama, Mistral and the rest
# are models you run on one of these, not backends of their own.
PRESETS = (
    Preset("ollama", "Ollama — on this machine",
           "http://localhost:11434/v1",
           note="ollama pull llama3.2-vision, or hermes3 for the text role"),
    Preset("vllm", "vLLM or NVIDIA NIM — on this machine",
           "http://localhost:8000/v1"),
    Preset("lmstudio", "LM Studio — on this machine",
           "http://localhost:1234/v1"),
    Preset("openai", "OpenAI — GPT",
           "https://api.openai.com/v1", model="gpt-4o", needs_key=True),
    Preset("nvidia", "NVIDIA — hosted",
           "https://integrate.api.nvidia.com/v1", needs_key=True,
           note="caps an inline image at 180 kB; images are encoded to fit"),
    Preset("openrouter", "OpenRouter — many models, one key",
           "https://openrouter.ai/api/v1", needs_key=True,
           note="where to reach Hermes and most open models without hosting them"),
    Preset("together", "Together AI",
           "https://api.together.xyz/v1", needs_key=True),
    Preset("groq", "Groq",
           "https://api.groq.com/openai/v1", needs_key=True),
)

BACKEND = register(Backend(
    name="openai",
    label="OpenAI-compatible server",
    build=from_env,
    settings=("BASE_URL", "MAX_IMAGE_BYTES", "BACKOFF"),
    doc=("Anything speaking OpenAI's chat-completions shape — which is most "
         "things. Run a model yourself with Ollama, vLLM, NIM or LM Studio, or "
         "point it at a hosted service. This is the default and costs nothing "
         "when the model is your own."),
    presets=PRESETS,
))

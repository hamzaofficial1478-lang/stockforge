"""Claude as the brain, signed in rather than keyed in.

This is the answer to "can I just sign in with my Claude account instead of
pasting an API key". You can. The Anthropic SDK resolves credentials in a fixed
order and an API key is only the first of several:

    ANTHROPIC_API_KEY
    ANTHROPIC_AUTH_TOKEN
    the profile left behind by `ant auth login`      <- the sign-in route
    workload identity federation
    the default profile on disk

So `Anthropic()` with no arguments works after a one-off `ant auth login`,
which opens a browser, you sign in, and it writes a profile under
~/.config/anthropic/. Nothing is pasted into stockforge and no key is stored in
the workspace. That is the whole mechanism — there is no separate sign-in for
this program to build, and building one would be worse than using the one that
already exists.

Two honest caveats, because they matter and nothing else in the project will
tell you:

  * This is a paid API, billed to whatever your Anthropic account is set up
    for. It is not the same pool as a Claude.ai chat subscription and it does
    not become free by signing in rather than pasting a key. The sign-in
    removes the key handling, not the bill.
  * It is therefore a deliberate exception to the project's "no paid APIs"
    rule. The local OpenAI-compatible backend is still there, still the
    default, and nothing here changes it. This is a second door, for when the
    local model is down or is not good enough on a particular design.

Against 5,000 designs at five passes each that is a real bill, so the effort
level defaults low and every knob is an environment variable.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .base import ProviderError, VisionProvider, encode_image
from .registry import Backend, register

log = logging.getLogger("stockforge.providers.claude")

DEFAULT_MODEL = "claude-opus-5"

# Claude takes an image up to 5 MB. We stay well under: a design at 1280px is
# all the detail any of the analysis passes uses, and smaller is faster.
MAX_IMAGE_BYTES = 1_500_000


def available() -> tuple[bool, str]:
    """Is this backend usable right now? Returns (ok, what to do about it)."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "Run `pip install anthropic` to use Claude as the model."

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True, ""

    # No key set is not the same as no credentials. Look for a signed-in profile.
    for base in (os.environ.get("ANTHROPIC_CONFIG_DIR"),
                 Path.home() / ".config" / "anthropic"):
        if base and Path(base).exists() and any(Path(base).iterdir()):
            return True, ""

    return False, ("Sign in once with `ant auth login` — it opens a browser and "
                   "stores the result, so stockforge never handles a key. "
                   "Setting ANTHROPIC_API_KEY works too.")


class ClaudeProvider(VisionProvider):
    """Reads images and returns text, same contract as every other provider.

    The JSON repair loop in VisionProvider.structured() still applies. Claude
    rarely needs it, but the pipeline should not care which backend it is
    talking to, and one code path that always runs beats two that sometimes do.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 16000,
        effort: str = "low",
        max_image_edge: int = 1280,
        timeout: float = 300.0,
        retries: int = 2,
    ):
        try:
            import anthropic
        except ImportError as exc:                      # pragma: no cover
            raise ProviderError(
                "The Claude backend needs the Anthropic SDK: pip install anthropic"
            ) from exc

        ok, fix = available()
        if not ok:
            raise ProviderError(f"Claude is not signed in. {fix}")

        # No api_key argument on purpose. Passing one here would shadow the
        # signed-in profile, which is exactly the behaviour we are avoiding.
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(timeout=timeout, max_retries=retries)
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.max_image_edge = max_image_edge
        self.name = f"{model}@anthropic"

    def chat(self, system: str, user_text: str, images: list[Path], **kw: Any) -> str:
        blocks: list[dict] = []
        for path in images:
            b64, mime = encode_image(path, self.max_image_edge, MAX_IMAGE_BYTES)
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": b64},
            })
        blocks.append({"type": "text", "text": user_text})

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=kw.get("max_tokens", self.max_tokens),
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": kw.get("effort", self.effort)},
                messages=[{"role": "user", "content": blocks}],
            )
        except self._anthropic.AuthenticationError as exc:
            raise ProviderError(
                "Claude rejected the sign-in. Run `ant auth login` again, or set "
                "ANTHROPIC_API_KEY."
            ) from exc
        except self._anthropic.RateLimitError as exc:
            raise ProviderError(
                f"{self.name} is rate limited. The worker's breather is the lever "
                f"for this — raise it on the Queue screen and start again."
            ) from exc
        except self._anthropic.APIStatusError as exc:
            raise ProviderError(f"{self.name} HTTP {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise ProviderError(f"{self.name} unreachable: {exc}") from exc

        # A refusal comes back as a normal 200 with no usable content, so it has
        # to be checked before the content is read or it looks like an empty
        # reply and gets retried forever.
        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "explanation", "") or ""
            raise ProviderError(
                f"{self.name} declined this design{': ' + detail if detail else ''}. "
                "It goes to Review rather than being retried."
            )

        text = "".join(b.text for b in response.content if b.type == "text")
        if not text.strip():
            raise ProviderError(f"{self.name} returned no text. Retry the design from Review.")

        usage = response.usage
        log.info("[%s] in=%s out=%s", self.model,
                 getattr(usage, "input_tokens", "?"), getattr(usage, "output_tokens", "?"))
        return text


def from_env(prefix: str = "SF_VISION") -> ClaudeProvider:
    """Build from environment. With a signed-in profile this needs nothing set.

        SF_VISION_BACKEND=claude
        SF_VISION_MODEL=claude-opus-5        # optional, this is the default
        SF_VISION_EFFORT=low                 # low | medium | high | xhigh | max
    """
    return ClaudeProvider(
        model=os.environ.get(f"{prefix}_MODEL") or DEFAULT_MODEL,
        max_tokens=int(os.environ.get(f"{prefix}_MAX_TOKENS", 16000)),
        effort=os.environ.get(f"{prefix}_EFFORT", "low"),
        max_image_edge=int(os.environ.get(f"{prefix}_MAX_EDGE", 1280)),
        timeout=float(os.environ.get(f"{prefix}_TIMEOUT", 300)),
        retries=int(os.environ.get(f"{prefix}_RETRIES", 2)),
    )


BACKEND = register(Backend(
    name="claude",
    label="Claude — sign in, nothing to install",
    build=from_env,
    ready=available,
    settings=("EFFORT",),
    doc=("Anthropic's own API, which does not speak the OpenAI shape. Sign in "
         "once with `ant auth login` and no key is stored anywhere. It is a "
         "paid API billed to your Anthropic account — the sign-in saves the "
         "key handling, not the bill."),
))

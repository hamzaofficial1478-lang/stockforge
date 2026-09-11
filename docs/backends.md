# Adding a model backend

stockforge does not care which model reads your designs. It asks for structured
data and gets a validated object back. Two backends ship with it, and adding a
third takes no change to any file in the program.

## First: you probably don't need one

Most things speak OpenAI's chat-completions shape, and the `openai` backend
covers all of them. **GPT, Hermes, Qwen, Llama, Mistral, DeepSeek and the rest
are models, not backends.** You run them on a server that already speaks that
shape:

| You want | Backend | Server address | Model |
|---|---|---|---|
| GPT | `openai` | `https://api.openai.com/v1` | `gpt-4o` |
| Hermes, on your own machine | `openai` | `http://localhost:11434/v1` | `hermes3` |
| Hermes, hosted | `openai` | `https://openrouter.ai/api/v1` | `nousresearch/hermes-3-llama-3.1-405b` |
| Anything on your own card | `openai` | `http://localhost:8000/v1` | whatever you loaded |
| Claude | `claude` | — | `claude-opus-5` |

The Setup screen lists these under **Use a different model service**, and the
Use-this button fills the address in for you.

One thing worth knowing: the **vision** role needs a model that can actually see
an image, and plenty of good models can't. Hermes 3 is text-only, for instance —
excellent for the titles-and-keywords role, no use for reading a design. Don't
take anyone's word for it, including this file: the **Test it** button on a
connection sends a real image with a colour and a number in it and checks the
answer. That is the only claim about a model worth trusting.

## When you do need one

Write your own when the service speaks a different shape — Gemini, Bedrock, an
in-house endpoint, a research model with its own client.

A backend is one module with a `BACKEND` in it. It can live anywhere your Python
can import from; it does not go in the stockforge package and you do not fork
anything.

```python
# acme_gemini.py
"""Gemini for stockforge."""

from pathlib import Path

from stockforge.providers.base import ProviderError, VisionProvider, encode_image
from stockforge.providers.registry import Backend, register
import os


class GeminiProvider(VisionProvider):
    def __init__(self, model: str, api_key: str):
        import google.generativeai as genai       # your dependency, not ours

        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)
        self.name = f"{model}@gemini"

    def chat(self, system: str, user_text: str, images: list[Path], **kw) -> str:
        """Take a system prompt, some text and some images. Return the model's
        reply as a string. That is the entire contract."""
        parts = [system, user_text]
        for path in images:
            b64, mime = encode_image(path, max_edge=1280, max_bytes=1_500_000)
            parts.append({"mime_type": mime, "data": b64})
        try:
            return self._model.generate_content(parts).text
        except Exception as exc:                  # map failures to ours
            raise ProviderError(f"{self.name}: {exc}") from exc


def build(prefix: str = "SF_VISION") -> GeminiProvider:
    """prefix is SF_VISION or SF_REASON — read your settings under it, so the
    two roles can be pointed at different models."""
    key = os.environ.get(f"{prefix}_API_KEY")
    if not key:
        raise ProviderError(f"Set {prefix}_API_KEY to a Gemini key.")
    return GeminiProvider(os.environ.get(f"{prefix}_MODEL") or "gemini-2.0-flash", key)


def ready() -> tuple[bool, str]:
    """Can this run right now? If not, say exactly what to do about it — this
    string is what the Setup screen shows the user."""
    try:
        import google.generativeai  # noqa: F401
    except ImportError:
        return False, "Run `pip install google-generativeai`."
    if not os.environ.get("SF_VISION_API_KEY"):
        return False, "Set SF_VISION_API_KEY to a Gemini key."
    return True, ""


BACKEND = register(Backend(
    name="gemini",
    label="Google Gemini",
    build=build,
    ready=ready,
    doc="Gemini through Google's own SDK.",
))
```

Then point a setting at the module:

```bash
SF_VISION_BACKEND=acme_gemini
SF_VISION_MODEL=gemini-2.0-flash
SF_VISION_API_KEY=...
```

Or type `acme_gemini` into the backend box on Setup. It appears in the menu from
then on, the health check reports on it, and every analysis pass goes through it.

## The contract, in full

**`chat(system, user_text, images, **kw) -> str`** is the only method you must
write. Everything else is done for you:

- `structured()` on the base class handles the JSON. It builds the prompt, adds
  a compact schema, extracts JSON from whatever prose your model wraps it in,
  validates against the pydantic model, and hands validation errors back for up
  to two repair rounds. You return a string; the pipeline gets a checked object.
- `encode_image(path, max_edge, max_bytes)` gives you base64 and a mime type,
  shrinking to fit a byte budget. Use it rather than reading the file yourself —
  hosted endpoints have limits and the failures are unhelpful when you cross
  them.
- Raise `ProviderError` for anything that goes wrong, with a message that says
  what to do. It goes in front of the user, in Review, next to the design that
  failed.

**`Backend` fields:**

| field | what it is |
|---|---|
| `name` | short id, lowercase |
| `label` | what the Setup menu shows |
| `build(prefix)` | returns your provider; `prefix` is `SF_VISION` or `SF_REASON` |
| `ready()` | `(True, "")` or `(False, "what to do about it")` |
| `settings` | extra setting suffixes you read, e.g. `("REGION", "PROJECT")` |
| `doc` | a sentence under the menu |
| `presets` | `Preset(...)` entries, if yours has several known addresses |

**Declare `settings` if you read your own.** Providers are built once and
reused, and the cache rebuilds when a watched setting changes. A setting you
read but do not declare means changing it does nothing at all, silently — which
is a bug that takes a long time to find. If your backend reads
`SF_VISION_REGION`, put `("REGION",)` in `settings`.

**Registering an existing name replaces it.** `name="openai"` means yours is
used wherever the built-in would have been, which is how you swap the client out
without forking.

## Testing yours

`tests/test_backends.py` writes backend modules to a temp directory and loads
them, so there is a working pattern to copy. The short version:

```python
def test_my_backend_answers():
    from stockforge import providers
    os.environ["SF_VISION_BACKEND"] = "acme_gemini"
    providers.reset()
    assert providers.vision().chat("s", "u", []) == "..."
```

`providers.set_provider("vision", YourFake())` pins a provider for tests that
should never make a real call, and `providers.reset()` clears everything.

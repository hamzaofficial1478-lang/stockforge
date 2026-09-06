"""The analysis passes, driven the way a real server drives them.

Everything about the analysis was tested one level above the wire: the tests
stub structured(), so the five prompts, the schema hint sent with them, the
JSON that comes back, the repair loop and the assembly into a DesignSpec were
never exercised together. A local model that answers in a markdown fence, or
omits a field, or sends a string where a number belongs — all of it ordinary
behaviour for a small model — had never been through this code.

So these run the real provider against a real HTTP server and script the
replies. What they cannot check is a model's judgement; what they do check is
that a plausible reply survives the whole journey into a spec, and that the
ordinary ways a small model misbehaves are recovered rather than fatal.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import pytest

from stockforge.providers.base import ProviderError
from stockforge.providers.openai_compat import OpenAICompatProvider
from stockforge.schema import DesignSpec, RasterElement
from stockforge.stages import analyse as A


@pytest.fixture
def listing(tmp_path):
    """A flat export and a staged photograph, as a real listing has."""
    flat = tmp_path / "flat.jpg"
    art = np.full((1400, 1000, 3), 245, dtype=np.uint8)
    cv2.putText(art, "AMELIA", (150, 600), cv2.FONT_HERSHEY_SIMPLEX, 3, (40, 40, 40), 8)
    cv2.imwrite(str(flat), art)

    mock = tmp_path / "mockup.jpg"
    cv2.imwrite(str(mock), np.full((1000, 1500, 3), 180, dtype=np.uint8))
    return [flat, mock]


class _Model:
    """A server that answers whichever schema the prompt asks for.

    Dispatching on the schema rather than on call order, because the order the
    passes run in is the code's business and a positional script would break
    every time one moved.
    """

    def __init__(self, answers: dict, overrides: list | None = None):
        self.answers = answers
        self.overrides = list(overrides or [])     # consumed first, in order
        self.prompts: list[dict] = []
        self.asked: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.prompts.append(body)
                asked = body["messages"][1]["content"][-1]["text"]
                name = next((k for k in outer.answers if f'"{k}"' in asked), None)
                outer.asked.append(name or "?")
                if outer.overrides:
                    text = outer.overrides.pop(0)
                else:
                    text = outer.answers.get(name, "{}")
                raw = json.dumps(
                    {"choices": [{"message": {"content": text}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def provider(self):
        return OpenAICompatProvider(
            f"http://127.0.0.1:{self.httpd.server_address[1]}/v1", "a-model", timeout=30)


# --- what a plausible model actually says --------------------------------

SURVEY = json.dumps({
    "category": "invitation", "occasion": "wedding",
    "style_tags": ["botanical", "minimal"],
    "surfaces": [{"name": "front", "image_index": 0, "width_mm": 127, "height_mm": 178}],
    "mockup_indices": [1], "confidence": 0.86, "notes": ""})

PALETTE = json.dumps({
    "assignments": [{"hex": "#faf6f0", "role": "background"},
                    {"hex": "#2b2b28", "role": "ink"},
                    {"hex": "#7d8f6e", "role": "accent"}],
    "temperature": "warm", "contrast": "high"})

TYPE = json.dumps({
    "elements": [{"kind": "text", "role": "title", "content": "Amelia & Jonah",
                  "box": {"x": 0.1, "y": 0.35, "w": 0.8, "h": 0.14},
                  "font": {"category": "serif", "weight": 400, "contrast": "high"},
                  "size_ratio": 0.05, "placeholder": True}],
    "grid": {"margin_x": 0.09, "margin_y": 0.08},
    "type_pairing": [{"category": "serif", "weight": 400},
                     {"category": "sans", "weight": 400}]})

STRUCT = json.dumps({
    "background": {"treatment": "solid"},
    "shapes": [{"kind": "shape", "primitive": "rect",
                "box": {"x": 0.05, "y": 0.05, "w": 0.9, "h": 0.9}}],
    "motifs": [{"kind": "motif", "motif": "botanical",
                "description": "a sprig of eucalyptus, oval leaves on a slender stem",
                "box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}}],
    "rasters": [],
    "motif_vocabulary": ["eucalyptus sprig"]})

PROV = json.dumps({"built_with": "illustrator", "raster_elements": [],
                   "third_party_suspected": False, "reason": ""})

ANSWERS = {"Survey": SURVEY, "PaletteRead": PALETTE, "Provenance": PROV,
           "TypeRead": TYPE, "StructureRead": STRUCT}


def test_a_whole_design_is_read_over_the_wire(listing):
    """Every pass, through the real request path, into a validated spec."""
    with _Model(ANSWERS) as m:
        spec = A.analyse(listing, asset_id="a1", provider=m.provider,
                         design_id="d1", mockups={1})

    assert isinstance(spec, DesignSpec)
    assert spec.dna.occasion == "wedding"
    assert spec.pages[0].name == "front"
    assert [t.content for t in spec.texts()] == ["Amelia & Jonah"]
    assert spec.motifs()[0].description.startswith("a sprig of eucalyptus")
    assert spec.publishable is True


def test_every_pass_is_sent_its_schema(listing):
    """The reply is validated against the model, so the model has to be in the
    prompt. A pass that asks for JSON without saying which JSON gets whatever
    the model felt like."""
    with _Model(ANSWERS) as m:
        A.analyse(listing, asset_id="a1", provider=m.provider, mockups={1})

    for body in m.prompts:
        asked = body["messages"][1]["content"][-1]["text"]
        assert "must validate against this schema" in asked
        assert '"properties"' in asked, "the schema hint was empty"


def test_the_surface_is_read_from_the_flat_not_the_photograph(listing):
    """The survey says which image shows which surface. Reading the staged
    photograph instead gives worse colour and worse text."""
    with _Model(ANSWERS) as m:
        spec = A.analyse(listing, asset_id="a1", provider=m.provider, mockups={1})
    assert spec.pages[0].source_image == str(listing[0])


# --- the ways a small model misbehaves -----------------------------------

def test_a_reply_wrapped_in_a_markdown_fence_is_still_read(listing):
    """Small models do this constantly, however plainly you ask them not to."""
    with _Model(ANSWERS, overrides=[f"```json\n{SURVEY}\n```"]) as m:
        spec = A.analyse(listing, asset_id="a1", provider=m.provider, mockups={1})
    assert spec.dna.occasion == "wedding"


def test_a_reply_with_prose_around_it_is_still_read(listing):
    with _Model(ANSWERS, overrides=[
            f"Sure! Here is the analysis:\n{SURVEY}\nHope that helps."]) as m:
        spec = A.analyse(listing, asset_id="a1", provider=m.provider, mockups={1})
    assert spec.dna.category == "invitation"


def test_an_invalid_reply_is_sent_back_with_the_errors_and_recovered(listing):
    """One bad reply must not lose the design. The retry has to carry the
    validation errors, or the model has no idea what to change."""
    broken = json.dumps({"category": "invitation", "occasion": "wedding",
                         "surfaces": "not a list", "confidence": 0.8})
    with _Model(ANSWERS, overrides=[broken]) as m:
        spec = A.analyse(listing, asset_id="a1", provider=m.provider, mockups={1})

    assert spec.dna.occasion == "wedding"
    repair = m.prompts[1]["messages"][1]["content"][-1]["text"]
    assert "was not valid" in repair
    # The schema hint names every field, so looking for "surfaces" would pass
    # whether or not the errors were included. This phrasing only comes from
    # pydantic, so it is there if and only if the errors were sent back.
    assert "validation error" in repair, \
        "the retry did not carry the errors, so the model is guessing again"
    assert "surfaces" in repair.split("Return corrected JSON only")[0], \
        "the errors did not name the field that was wrong"


def test_it_gives_up_rather_than_looping_forever(listing):
    with _Model(ANSWERS, overrides=["nonsense"] * 12) as m:
        with pytest.raises(ProviderError) as exc:
            A.analyse(listing, asset_id="a1", provider=m.provider, mockups={1})
    assert "could not produce valid" in str(exc.value)
    assert len(m.prompts) <= 4, f"{len(m.prompts)} attempts for one pass"


# --- the raster path, over the wire --------------------------------------

def test_a_reported_photographic_area_becomes_a_placed_element(listing):
    """The structure pass reporting a raster has to reach the page as one,
    and take the design out of the sellable pile on the way."""
    with_raster = json.dumps({
        "background": {"treatment": "solid"}, "shapes": [], "motifs": [],
        "rasters": [{"kind": "raster", "description": "a painted autumn wood",
                     "box": {"x": 0, "y": 0.4, "w": 1, "h": 0.6},
                     "source": {"x": 0, "y": 0.4, "w": 1, "h": 0.6}}],
        "motif_vocabulary": []})

    with _Model({**ANSWERS, "StructureRead": with_raster}) as m:
        spec = A.analyse(listing, asset_id="a1", provider=m.provider, mockups={1})

    rasters = spec.rasters()
    assert len(rasters) == 1
    assert isinstance(rasters[0], RasterElement)
    assert spec.publishable is False, "a placed photograph is not ours to sell"
    assert any("painted autumn wood" in w for w in spec.warnings)


# --- prompts and schemas must not drift apart ----------------------------

@pytest.mark.parametrize("prompt,model", [
    (A.SURVEY_SYSTEM, A.Survey),
    (A.TYPE_SYSTEM, A.TypeRead),
    (A.STRUCTURE_SYSTEM, A.StructureRead),
])
def test_every_list_the_model_must_fill_is_specified_somewhere(prompt, model):
    """A field added to a schema and explained nowhere is a field the model is
    given as a bare name and expected to fill well. It is told what to put in
    one of two places — the prompt, or the field's own description — and either
    will do, but neither is how a pass quietly starts returning an empty list.

    motif_vocabulary was exactly that, and the mixing stage reads it.
    """
    for name, field in model.model_fields.items():
        if "list" not in str(field.annotation):
            continue
        named = name.rstrip("s") in prompt.lower()
        assert named or field.description, (
            f"{model.__name__}.{name} is neither asked for in the prompt nor "
            f"described in the schema, so nothing tells the model to fill it")


def test_the_schema_sent_to_the_model_is_always_valid_json():
    """It used to end with [:6000]. StructureRead is 7466 characters and is the
    pass that decides everything drawn on the page, so the model was told to
    satisfy a schema chopped off mid-object."""
    from stockforge.providers.base import schema_hint
    from stockforge.schema import Provenance

    for model in (A.Survey, A.PaletteRead, A.TypeRead, A.StructureRead, Provenance):
        hint = schema_hint(model)
        json.loads(hint)                       # raises if it was truncated
        assert model.__name__ in hint, f"{model.__name__} lost its own name"
        assert '"properties"' in hint

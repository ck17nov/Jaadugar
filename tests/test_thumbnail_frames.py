"""Choosing the base frame. Both failures here shipped a real thumbnail.

`_frame_interest` picks between candidate frames of one video. It weighted
DETAIL at 70%, and noise has maximal detail - every pixel is an edge - so a
motion-blurred grey frame from a stock clip beat every clean shot in a
72-scene finance explainer, twice, on two different videos. The chosen
thumbnail was grey static.

The variant scorer cannot catch this: it measures text legibility, subject
box and edge safety, and it rated the grey static 82/100 - HIGHER than the
usable thumbnail that replaced it. So the ordering has to be right here,
before the scorer sees anything.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from engine.thumbnail.generator import (MIN_FRAME_DETAIL,
                                        MIN_FRAME_STRUCTURE,
                                        _frame_interest)


def _save(image: Image.Image, tmp_path, name: str):
    path = tmp_path / f"{name}.jpg"
    image.save(path, "JPEG", quality=92)
    return path


def _noise(tmp_path, name="noise"):
    """Uniformly busy: what motion blur and video noise look like."""
    image = Image.new("L", (480, 270))
    pixels = image.load()
    # Deterministic pseudo-noise; Math.random equivalents are not available
    # and a fixed pattern is better for a test anyway.
    for y in range(270):
        for x in range(480):
            pixels[x, y] = (x * 7919 + y * 104729) % 256
    return _save(image.convert("RGB"), tmp_path, name)


def _flat(tmp_path, name="flat"):
    """A fade or a plain wall."""
    return _save(Image.new("RGB", (480, 270), (118, 118, 118)), tmp_path, name)


def _subject(tmp_path, name="subject"):
    """Busy in one place, calm elsewhere - a thing on a background."""
    image = Image.new("RGB", (480, 270), (150, 150, 148))
    draw = ImageDraw.Draw(image)
    draw.ellipse((150, 80, 330, 200), fill=(40, 40, 44))
    draw.ellipse((175, 100, 240, 150), fill=(210, 190, 90))
    for i in range(8):
        draw.line((160 + i * 20, 90, 170 + i * 20, 190), fill=(90, 90, 96),
                  width=3)
    return _save(image, tmp_path, name)


def test_noise_is_rejected_outright(tmp_path):
    """The exact failure: grey static chosen over clean shots."""
    assert _frame_interest(_noise(tmp_path)) == 0.0


def test_a_flat_frame_is_rejected(tmp_path):
    """A fade or a wall was already meant to score low."""
    assert _frame_interest(_flat(tmp_path)) == 0.0


def test_a_subject_beats_noise(tmp_path):
    """The ordering that matters, since max() picks the winner."""
    subject = _frame_interest(_subject(tmp_path))
    noise = _frame_interest(_noise(tmp_path))
    assert subject > noise
    assert subject > 0.0


def test_a_subject_clears_the_structure_floor(tmp_path):
    """If it did not, every frame would be rejected and nothing chosen."""
    assert _frame_interest(_subject(tmp_path)) > 0.0
    assert 0.0 < MIN_FRAME_STRUCTURE < 1.0
    # Clear of JPEG artefacts (about 1.4) and under real content (about 4.4).
    assert 1.4 < MIN_FRAME_DETAIL < 4.4


def test_an_unreadable_file_sorts_last(tmp_path):
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    assert _frame_interest(broken) < 0.0


def test_the_score_is_finite_and_bounded(tmp_path):
    for path in (_subject(tmp_path), _flat(tmp_path, "f2"),
                 _noise(tmp_path, "n2")):
        value = _frame_interest(path)
        assert math.isfinite(value)
        assert -1.0 <= value <= 1.0


# ---------------------------------------------------------------------------
# Which scenes are offered at all
# ---------------------------------------------------------------------------
def test_only_representative_scenes_are_offered(tmp_path, monkeypatch):
    """Scanning all 72 scenes finds the busiest frame, not the subject.

    A finance explainer whose own briefs asked for coins, jars and fact
    sheets got a thumbnail of men weaving baskets, because that clip was the
    only crowded frame among seventy clean desks.
    """
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    from engine.core.models import Scene, Script
    from engine.pipeline import Pipeline

    pipe = Pipeline()
    try:
        scenes = []
        for i, role in enumerate(["hook", "context", "value", "value",
                                  "payoff", "cta"]):
            asset = tmp_path / f"a{i}.jpg"
            Image.new("RGB", (64, 64), (100, 100, 100)).save(asset)
            scenes.append(Scene(index=i, narration="x", role=role,
                                asset_path=str(asset)))
        script = Script()
        script.scenes = [s.to_dict() for s in scenes]

        offered = pipe._thumbnail_sources(script)
        assert len(offered) == 2, offered
        assert {p.name for p in offered} == {"a0.jpg", "a4.jpg"}
    finally:
        pipe.close()


def test_everything_is_offered_when_no_scene_has_a_preferred_role(
        tmp_path, monkeypatch):
    """A shape whose beats map differently must still get a thumbnail."""
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    from engine.core.models import Scene, Script
    from engine.pipeline import Pipeline

    pipe = Pipeline()
    try:
        scenes = []
        for i in range(3):
            asset = tmp_path / f"b{i}.jpg"
            Image.new("RGB", (64, 64), (90, 90, 90)).save(asset)
            scenes.append(Scene(index=i, narration="x", role="value",
                                asset_path=str(asset)))
        script = Script()
        script.scenes = [s.to_dict() for s in scenes]
        assert len(pipe._thumbnail_sources(script)) == 3
    finally:
        pipe.close()


def test_a_missing_asset_is_never_offered(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    from engine.core.models import Scene, Script
    from engine.pipeline import Pipeline

    pipe = Pipeline()
    try:
        script = Script()
        script.scenes = [
            Scene(index=0, narration="x", role="hook",
                  asset_path=str(tmp_path / "gone.jpg")).to_dict()]
        assert pipe._thumbnail_sources(script) == []
    finally:
        pipe.close()

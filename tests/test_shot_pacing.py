"""A beat is a span of narration, not a single picture.

Measured on a real Short (job_4a2b82cf2635): 51.77 seconds, 7 scenes, one
image each - so every still was held 7.4 seconds while drifting at about a
third of a pixel per frame. That is a slideshow, and it is the largest part
of "it doesn't look engaging".

The narration is untouched. The beat keeps its measured span and its
authored brief; the span is now covered by two or three FRAMINGS of the
same moment.
"""
from __future__ import annotations

import pytest

from engine.core.models import Scene
from engine.pipeline import Pipeline

# The real measured spans from the rendered Short.
REAL_SPANS = [6.42, 9.53, 7.38, 8.92, 6.22, 7.00, 7.28]


@pytest.fixture()
def pipe(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("AUTOTUBE_DRY_RUN", "1")
    return Pipeline()


def _scenes(spans) -> list[Scene]:
    return [Scene(index=i, narration=f"line {i}",
                  visual_prompt=f"brief {i}", duration=d)
            for i, d in enumerate(spans)]


# ---------------------------------------------------------------------------
# The planning
# ---------------------------------------------------------------------------
def test_a_long_beat_is_covered_by_several_shots(pipe):
    plan = pipe._plan_shots(_scenes(REAL_SPANS))
    shots = sum(len(s) for _, s in plan)
    assert shots == 21, shots
    mean_hold = sum(REAL_SPANS) / shots
    assert 2.0 <= mean_hold <= 3.0, f"{mean_hold:.2f}s per shot"


def test_a_short_beat_keeps_one_image(pipe):
    """The opposite failure - a flicker - is not an improvement."""
    plan = pipe._plan_shots(_scenes([1.6]))
    assert len(plan[0][1]) == 1


def test_a_beat_with_no_measured_duration_keeps_one_image(pipe):
    plan = pipe._plan_shots(_scenes([0.0]))
    assert len(plan[0][1]) == 1


def test_the_shares_add_up_to_the_beat(pipe):
    """The picture must not drift from the voice."""
    for scene, shots in pipe._plan_shots(_scenes(REAL_SPANS)):
        assert abs(sum(s.duration for s in shots) - scene.duration) < 0.001


def test_every_shot_gets_its_own_seed_input(pipe):
    """The image seed comes from the scene index and the prompt.

    Without a distinct index AND a distinct prompt, the three shots of a
    beat would be the same picture generated three times.
    """
    _, shots = pipe._plan_shots(_scenes([9.0]))[0]
    assert len({s.index for s in shots}) == len(shots)
    assert len({s.visual_prompt for s in shots}) == len(shots)


def test_the_first_shot_uses_the_authored_brief_unchanged(pipe):
    """The brief is the thing a human wrote. Shot two onwards vary it."""
    _, shots = pipe._plan_shots(_scenes([9.0]))[0]
    assert shots[0].visual_prompt == "brief 0"
    assert all("brief 0," in s.visual_prompt for s in shots[1:])


def test_consecutive_shots_move_differently(pipe):
    """Three stills drifting the same way reads as one long drift."""
    _, shots = pipe._plan_shots(_scenes([9.0]))[0]
    assert len({s.motion for s in shots}) > 1


def test_the_title_card_is_not_repeated_on_every_shot(pipe):
    scenes = _scenes([9.0])
    scenes[0].on_screen_text = "9%"
    _, shots = pipe._plan_shots(scenes)[0]
    assert shots[0].on_screen_text == "9%"
    assert all(s.on_screen_text == "" for s in shots[1:])


def test_the_caption_follows_every_shot_of_its_beat(pipe):
    """Captions are timed in absolute seconds, but a shot that lost its
    caption text would break the coverage floor in stage_render."""
    scenes = _scenes([9.0])
    scenes[0].caption_text = "एक पंक्ति"
    _, shots = pipe._plan_shots(scenes)[0]
    assert all(s.caption_text == "एक पंक्ति" for s in shots)


# ---------------------------------------------------------------------------
# Collecting the results back
# ---------------------------------------------------------------------------
def test_the_first_shot_stays_the_scene_asset(pipe):
    """The thumbnail picker and everything else reading one image per scene
    must keep working."""
    scenes = _scenes([9.0])
    plan = pipe._plan_shots(scenes)
    for _, shots in plan:
        for i, shot in enumerate(shots):
            shot.asset_path = f"/tmp/img_{i}.jpg"
    pipe._collect_shots(plan)

    assert scenes[0].asset_path == "/tmp/img_0.jpg"
    assert scenes[0].extra_assets == ["/tmp/img_1.jpg", "/tmp/img_2.jpg"]
    assert scenes[0].shot_paths() == ["/tmp/img_0.jpg", "/tmp/img_1.jpg",
                                      "/tmp/img_2.jpg"]


def test_a_scene_with_no_generated_shots_is_left_alone(pipe):
    scenes = _scenes([9.0])
    scenes[0].asset_path = "/tmp/already.jpg"
    plan = pipe._plan_shots(scenes)        # shots have no asset_path
    pipe._collect_shots(plan)
    assert scenes[0].asset_path == "/tmp/already.jpg"
    assert scenes[0].extra_assets == []


def test_shot_paths_skips_blanks():
    scene = Scene(index=0, narration="x", asset_path="a.jpg",
                  extra_assets=["", "b.jpg"])
    assert scene.shot_paths() == ["a.jpg", "b.jpg"]


def test_the_scene_round_trips_through_a_dict():
    """It goes to job.json and comes back."""
    scene = Scene(index=0, narration="x", asset_path="a.jpg",
                  extra_assets=["b.jpg"])
    back = Scene.from_dict(scene.to_dict())
    assert back.extra_assets == ["b.jpg"]
    assert back.shot_paths() == ["a.jpg", "b.jpg"]

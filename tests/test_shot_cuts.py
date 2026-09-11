"""A shot change is a CUT. Only a scene change is a cross-fade.

Splitting each beat into several shots fixed the seven-second hold, and
introduced two problems nobody was looking for.

The visible one: every shot boundary became a cross-fade, so two angles on
the same moment dissolved into each other. That does not read as a
transition, it reads as a mistake.

The expensive one: the ffmpeg filter chain went from one xfade node per
scene to one per shot, and EVERY frame of the video passes through every
node in that chain, single threaded. Measured on a real 50-second Short
with nineteen shots: 503 seconds of CPU and still running. The same video
needs six xfade nodes, not eighteen.

The fix is a `groups` list saying which scene each clip belongs to. Runs of
clips inside one scene are concatenated - free, no pixel work - and only the
scene streams are cross-faded. The thing that must not break is the
duration arithmetic: the audio, the captions and the chapter marks are all
timed in absolute seconds and know nothing about clips, so a video that
comes out even 100ms short drifts against its own narration.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.core.config import load_config
from engine.video.compose import VideoComposer


@pytest.fixture()
def composer(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    cfg = load_config()
    cfg.set("video.transition_duration", 0.65)
    return VideoComposer(cfg)


def _shots(per_scene: list[int], scene_seconds: float = 8.0):
    """durations and groups for a video with these shots per scene."""
    durations: list[float] = []
    groups: list[int] = []
    for scene, count in enumerate(per_scene):
        for _ in range(count):
            durations.append(scene_seconds / count)
            groups.append(scene)
    return durations, groups


def _paths(n: int) -> list[Path]:
    return [Path(f"clip_{i:02d}.mp4") for i in range(n)]


# ==========================================================================
class TestDurationIsExact:
    """The only thing that must never break."""

    @pytest.mark.parametrize("per_scene", [
        [3, 3, 3, 3, 3, 2, 2],          # the real 19-shot Short
        [1, 1, 1, 1],                   # one shot per scene, as before
        [4],                            # a single scene, all cuts
        [1, 5, 1],
        [2, 2],
    ])
    def test_grouped_output_length_equals_the_narration(self, composer,
                                                        per_scene):
        durations, groups = _shots(per_scene)
        lengths = composer._clip_lengths(durations, groups)
        fades = len(per_scene) - 1
        produced = sum(lengths) - fades * composer.transition_dur
        assert produced == pytest.approx(sum(durations), abs=1e-9)

    @pytest.mark.parametrize("per_scene", [[3, 3, 2], [1, 1, 1], [2]])
    def test_ungrouped_still_works_the_old_way(self, composer, per_scene):
        """`groups=None` must keep the previous behaviour exactly - the
        long-form path pre-stitches segments and passes None."""
        durations, _ = _shots(per_scene)
        lengths = composer._clip_lengths(durations)
        fades = len(durations) - 1
        produced = sum(lengths) - fades * composer.transition_dur
        assert produced == pytest.approx(sum(durations), abs=1e-9)

    def test_a_single_clip_is_untouched(self, composer):
        assert composer._clip_lengths([4.2]) == [4.2]
        assert composer._clip_lengths([4.2], [0]) == [4.2]


# ==========================================================================
class TestOnlySceneBoundariesFade:
    def test_padding_appears_only_at_scene_boundaries(self, composer):
        """A clip in the middle of a scene is padded not at all; a clip
        either side of a scene boundary gets half a transition."""
        durations, groups = _shots([3, 3], scene_seconds=6.0)  # 2s each
        lengths = composer._clip_lengths(durations, groups)
        t = composer.transition_dur
        # clip 0: first of scene 0, no fade before, cut after -> no padding
        assert lengths[0] == pytest.approx(2.0)
        # clip 1: inside scene 0, cuts both sides -> no padding
        assert lengths[1] == pytest.approx(2.0)
        # clip 2: last of scene 0, fades into scene 1 -> half a transition
        assert lengths[2] == pytest.approx(2.0 + t / 2)
        # clip 3: first of scene 1, fades in -> half a transition
        assert lengths[3] == pytest.approx(2.0 + t / 2)
        assert lengths[5] == pytest.approx(2.0)

    def test_the_chain_is_as_long_as_the_scene_count(self, composer):
        durations, groups = _shots([3, 3, 3, 3, 3, 2, 2])
        lengths = composer._clip_lengths(durations, groups)
        chain, _ = composer._xfade_chain(_paths(len(durations)), lengths,
                                         groups)
        assert chain.count("xfade=") == 6, "one per scene boundary, not 18"
        assert chain.count("concat=") == 7, "one per multi-shot scene"

    def test_without_groups_every_boundary_fades(self, composer):
        """The regression this guards: 19 clips meant 18 cross-fades."""
        durations, _ = _shots([3, 3, 3, 3, 3, 2, 2])
        lengths = composer._clip_lengths(durations)
        chain, _ = composer._xfade_chain(_paths(len(durations)), lengths)
        assert chain.count("xfade=") == 18
        assert "concat=" not in chain

    def test_one_shot_per_scene_needs_no_concat(self, composer):
        durations, groups = _shots([1, 1, 1, 1])
        lengths = composer._clip_lengths(durations, groups)
        chain, _ = composer._xfade_chain(_paths(4), lengths, groups)
        assert chain.count("xfade=") == 3
        assert "concat=" not in chain

    def test_a_single_scene_of_several_shots_is_all_cuts(self, composer):
        durations, groups = _shots([4])
        lengths = composer._clip_lengths(durations, groups)
        chain, label = composer._xfade_chain(_paths(4), lengths, groups)
        assert "xfade=" not in chain
        assert chain.count("concat=") == 1
        assert label == "[vout]", "the concat has to reach the output"

    def test_the_chain_ends_at_the_label_it_returns(self, composer):
        """A dangling label is an ffmpeg error, not a wrong-looking video."""
        for per_scene in ([3, 3, 2], [1, 1], [4], [1, 3]):
            durations, groups = _shots(per_scene)
            lengths = composer._clip_lengths(durations, groups)
            chain, label = composer._xfade_chain(
                _paths(len(durations)), lengths, groups)
            assert chain.rstrip().endswith(label), (per_scene, chain[-60:])

    def test_every_input_is_consumed(self, composer):
        """A clip left out of the graph is a shot missing from the video."""
        durations, groups = _shots([3, 2, 4])
        lengths = composer._clip_lengths(durations, groups)
        chain, _ = composer._xfade_chain(_paths(len(durations)), lengths,
                                         groups)
        for i in range(len(durations)):
            assert f"[{i}:v]" in chain, f"clip {i} never reaches the graph"


# ==========================================================================
def test_the_pipeline_passes_the_scene_index_through():
    """Structural: the grouping is useless if the caller does not supply it,
    and the symptom - a slow render with dissolves between shots - looks
    like nothing in particular."""
    import inspect

    from engine.pipeline import Pipeline

    source = inspect.getsource(Pipeline.stage_render)
    assert "shot_groups" in source
    assert "groups=shot_groups" in source
    assert source.count("groups=shot_groups") == 2, \
        "both render_scene_clips and finalize need it - the clip lengths " \
        "and the filter graph have to agree about which boundaries fade"

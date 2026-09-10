"""The app's hardcoded copies must agree with the backend's own catalogues.

Every finding here was a DIVERGENCE, not a crash: the app kept a hand-written
duplicate of something the backend already serves, nobody updated it, and the
screen then offered - or failed to offer - something the backend disagreed
with. The Topic dropdown is the one that bit: AI, science and code were folded
into Technical and four topics were added, and the app's copy kept the old
shape, so the four newly requested topics were missing from the list the
operator sees first.

Parsing Kotlin with a regex is crude and deliberate. The alternative is
noticing by hand, which is what already failed.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from engine.core.groups import GROUPS
from engine.core.languages import CAPTIONS, VOICES

# VOICES / CAPTIONS hold Language objects; the codes are what cross the wire.
VOICE_CODES = [v.code for v in VOICES]
CAPTION_CODES = [c.code for c in CAPTIONS]

ANDROID = Path("android/app/src/main/java/com/autotube/ai")
CREATE = ANDROID / "ui/screens/CreateAutomationScreen.kt"

pytestmark = pytest.mark.skipif(
    not CREATE.exists(), reason="the Android sources are not in this checkout")


def _kotlin_list(name: str, source: str) -> list[str]:
    """The string literals of a top-level `val <name> = listOf(...)`."""
    body = source.split(f"val {name} = ")[1]
    depth, end = 0, 0
    for index, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    return re.findall(r'"([^"]+)"', body[:end])


@pytest.fixture(scope="module")
def create_screen() -> str:
    return CREATE.read_text(encoding="utf-8")


def test_the_offline_topic_list_matches_the_backend(create_screen):
    """It is only a fallback now, but a fallback that lies is still a lie.

    When the backend is unreachable the group selector is hidden, so this
    list is the ONLY way to pick a topic - and it was missing Excel, MS
    Office, phone and laptop launches, and buying advice.
    """
    listed = _kotlin_list("NICHE_OPTIONS", create_screen)
    real = [topic for group in GROUPS for topic in group.topics]
    assert sorted(listed) == sorted(real), (
        f"only in the app: {sorted(set(listed) - set(real))}; "
        f"only in the backend: {sorted(set(real) - set(listed))}")


def test_the_language_list_matches_the_backend(create_screen):
    """Four voices, and the app must offer exactly those four."""
    codes = _kotlin_list("LANGUAGES", create_screen)[0::2]
    assert sorted(codes) == sorted(VOICE_CODES), (
        f"app offers {sorted(codes)}, backend supports {sorted(VOICE_CODES)}")


@pytest.mark.parametrize("retired", ["ta", "te", "bn"])
def test_a_removed_language_is_offered_by_neither(create_screen, retired):
    """Asked for directly: keep only Indian English, English, Hindi, Hinglish."""
    assert retired not in VOICE_CODES
    assert retired not in CAPTION_CODES
    codes = _kotlin_list("LANGUAGES", create_screen)[0::2]
    assert retired not in codes


def test_the_kids_niche_list_matches_the_child_directed_groups(create_screen):
    """The app sets Made for Kids from this list plus the group flag.

    The list alone was the bug: a CUSTOM topic under the Kids group matched
    nothing here, so an "all ages" age band cleared the flag and a children's
    story published as general-audience content.
    """
    listed = {n.lower() for n in _kotlin_list("KIDS_NICHES", create_screen)}
    declared = {t.lower() for g in GROUPS if g.child_directed for t in g.topics}
    assert listed == declared, (
        f"only in the app: {sorted(listed - declared)}; "
        f"only in the backend: {sorted(declared - listed)}")


def test_the_app_reads_the_group_flag_rather_than_only_the_list(create_screen):
    """Structural, because a custom topic can never be in the list."""
    assert "childDirected" in create_screen, (
        "the Create screen must consult the group's child_directed flag, not "
        "only its hardcoded kids-topic list")


def test_every_offered_voice_has_a_voice_configured():
    """A language the app offers but TTS cannot speak correctly.

    Hinglish had no entry in the config tables, so `_lookup` fell back to the
    base code "hi" and handed back the DEVANAGARI Hindi voice - which is
    precisely what the curated map exists to avoid, and it never ran because
    resolve_voice short-circuits on a non-empty id.
    """
    from engine.core.config import load_config
    from engine.tts.engine import VoiceEngine

    engine = VoiceEngine(load_config())
    for code in VOICE_CODES:
        for gender in ("female", "male"):
            spec = engine.voice_spec(code, "energetic", gender=gender)
            assert spec.voice_id, f"{code}/{gender} has no voice"
            if code == "hi-Latn":
                # Latin script: an Indian-English voice reads it, a Hindi one
                # mispronounces it.
                assert spec.voice_id.startswith("en-IN"), \
                    f"Hinglish is voiced by {spec.voice_id}"
            elif code == "hi":
                assert spec.voice_id.startswith("hi-"), spec.voice_id
            else:
                assert spec.voice_id.startswith("en-"), spec.voice_id

"""The holes an end-to-end audit found in the kids safety gate.

All three were real and two were introduced the same day, by the
photography exemption written for a toy-camera story. 857 of the 1193
banked entries are child-directed, so this is the largest exposure in the
project and every one of these is a regression test, not a nicety.
"""
from __future__ import annotations

import glob
import json
import os
import tempfile
from pathlib import Path

import pytest

from engine.quality.gate import violence_in


# ==========================================================================
class TestInflectionsAreCovered:
    """The strong vocabulary was bare stems and nothing said so: `stab` was
    listed, "stabbed" was not; `shoot` was, "shooting" was not; `murder`
    was, "murdered" was not. No exemption was involved - the most natural
    past tense of almost every word in the list matched nothing at all.
    """

    @pytest.mark.parametrize("text", [
        "the wolf stabbed him",
        "he was stabbing the door",
        "a shooting broke out",
        "the man murdered him",
        "he was killing the plants",
        "the soldier strangled him",
        "blood was bleeding everywhere",
        "two deaths were reported",
        "a gunshot echoed",
    ])
    def test_an_inflected_form_still_blocks(self, text):
        assert violence_in(text), f"leaked: {text!r}"

    @pytest.mark.parametrize("text", [
        "a warm ward in the barn",       # war\\w* would match both
        "the stable door was open",      # stab\\w* would match this
        "she warned him gently",
        "the warden waved",
    ])
    def test_the_stems_are_not_greedy(self, text):
        """Spelled out rather than stemmed on purpose. `stab\\w*` matches
        "stable" and `war\\w*` matches "warm", "ward" and "warn" - all three
        belong in a bedtime story."""
        assert not violence_in(text), f"false positive: {text!r}"


# ==========================================================================
class TestAnExemptionCoversOnlyItsOwnWord:
    """The photography clause can span 60 characters, and the caller used to
    exempt EVERY strong word inside that span. One innocent camera reference
    therefore cleared anything violent sitting beside it in the same
    sentence. Named groups now scope the exemption to the word that earned
    it.
    """

    def test_a_camera_does_not_clear_an_unrelated_killing(self):
        assert violence_in("he was killed, then she took a photo of the lens")

    def test_a_camera_does_not_clear_a_knife(self):
        assert violence_in("she raised the knife and the camera was rolling")

    def test_the_photography_reading_itself_still_passes(self):
        """The control: the entry this exemption was written for."""
        assert not violence_in(
            "Shoot with Kavya as she tries to twist the rotating lens")


# ==========================================================================
class TestShootingAPersonIsNeverPhotography:
    """A camera word within 60 characters used to be an unconditional
    licence. The weapon veto only knew weapon nouns, so a sentence with no
    gun in it passed."""

    @pytest.mark.parametrize("text", [
        "shoot him with the camera nearby",
        "shoot the boy while the camera films",
        "shoot her, then take a picture",
        "shoot at them as the photographer watched",
        "shoot the dog and film it",
        "shoot someone for the photo",
    ])
    def test_a_person_as_the_object_blocks(self, text):
        assert violence_in(text), f"leaked: {text!r}"

    @pytest.mark.parametrize("text", [
        "Shoot with Kavya as she tries to twist the rotating lens",
        "shoot a photo of the flowers",
        "the camera was ready to shoot the sunset",
    ])
    def test_photographing_a_thing_still_passes(self, text):
        assert not violence_in(text), f"false positive: {text!r}"


# ==========================================================================
class TestTheHardeningCostsNothing:
    """The measurement that decides whether the above is safe to ship.

    A safety gate that rejects the owner's own catalogue is a gate that gets
    turned off. Zero of 1193 when this was written.
    """

    def test_no_banked_entry_trips_the_gate(self):
        os.environ.setdefault("AUTOTUBE_WORKSPACE", tempfile.mkdtemp())
        from engine.content.bank import BankEntry
        from engine.content.bank_import import unsafe

        hits, scanned = [], 0
        for path in sorted(glob.glob("banks/*.jsonl")):
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                scanned += 1
                entry = BankEntry.from_dict(json.loads(line))
                entry.recompute()
                for problem in (unsafe(entry) or []):
                    hits.append((Path(path).name, entry.entry_id,
                                 str(problem)[:100]))
        assert scanned > 1000, f"only scanned {scanned} - bank not found?"
        assert not hits, f"{len(hits)} of {scanned} now flag: {hits[:5]}"


# ==========================================================================
class TestPublishPathCannotBeWalkedAround:

    def test_the_cli_upload_checks_status_and_quality(self):
        """`autotube upload --job <id>` called publish_now directly - the
        same function the approval flow uses AFTER the gates have run. So it
        would publish a job the quality gate REJECTED, or one still
        mid-render, to a public channel. The gates were not bypassed by a
        bug; they were simply not on this path."""
        src = Path("backend/cli.py").read_text(encoding="utf-8")
        start = src.index("def upload(")
        body = src[start:src.index("publish_now(video_job", start)]
        assert "JobStatus" in body, "status is not consulted"
        assert "quality" in body, "the stored quality verdict is not read"
        assert "--force" in body, "no explicit override for the operator"

    def test_a_request_can_raise_the_quality_bar_but_not_lower_it(self):
        """min_quality_score came off the wire and REPLACED the configured
        minimum in either direction, so a client posting 1 lowered the
        publish bar from 80 to 1 for that run."""
        src = Path("engine/pipeline.py").read_text(encoding="utf-8")
        assert "if requested_minimum > previous_minimum:" in src, (
            "the requested minimum must only ever raise the bar")
        assert "if requested_minimum:\n            self.quality_gate.minimum" \
            not in src, "the unconditional override is back"

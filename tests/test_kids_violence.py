"""The kids violence check must not fire on a dead battery.

Found by importing a real batch: a Hindi bedtime story about a boy learning
what the dark sounds like was rejected because "he finds a torch, but the
battery is dead" and "the flame dies". Neither is violent content, and a
gate that blocks ordinary bedtime stories is a gate the operator learns to
ignore.

The opposite failure matters more, so both directions are pinned here. The
words also have to keep working for the case they exist for.
"""
from __future__ import annotations

import pytest

from engine.quality.gate import violence_in

BENIGN = [
    # The two that were actually rejected.
    "he finds a torch, but the battery is dead. The corners stay black.",
    "Vivaan lights a lantern. Wind blows, the flame dies.",
    # Everything else that ordinarily gets described as dead.
    "his phone went dead halfway home",
    "the batteries were dead so the clock stopped",
    "they reached a dead end in the lane",
    "there was dead silence in the courtyard",
    "the music dies away and the room is still",
    "the candle died in the wind",
    "the lamp is dead again",
    "she pulled off the dead leaves",
    # And ordinary story prose that must never trip anything.
    "Naina pressed her thumb down the middle until it opened",
    "Tara wanted to win the sack race more than anything",
]

VIOLENT = [
    # A living thing, which is the case the words exist for.
    "the bird is dead",
    "the puppy dies at the end",
    "his grandfather died last winter",
    "the plant died because nobody watered it",
    # The strong words, decisive on their own.
    "he had a knife in his hand",
    "there was blood on the step",
    "a war between two kingdoms",
    "the hunter had a gun",
    # A benign use does NOT excuse a real one in the same text.
    "the flame dies, and then the dog dies too",
    "the battery is dead and there was blood everywhere",
]


@pytest.mark.parametrize("text", BENIGN)
def test_ordinary_prose_is_not_violence(text):
    assert violence_in(text) is False, text


@pytest.mark.parametrize("text", VIOLENT)
def test_real_violence_still_fires(text):
    assert violence_in(text) is True, text


def test_died_is_covered_at_all():
    """It was missing from the original pattern entirely.

    The list was (kill|blood|die|dead|death|gun|knife|weapon|war), so "the dog
    died" matched nothing - the one inflection a story would actually use.
    """
    assert violence_in("the dog died") is True


def test_the_import_gate_and_the_render_gate_agree():
    """They must, or an entry imports clean and is blocked after a render.

    That is the exact failure the import-time check was added to prevent, so
    it would be a poor thing to reintroduce by letting the two drift.
    """
    from engine.content.bank_import import unsafe
    from tests.test_bank import kids_entry

    entry = kids_entry()
    entry.scenes[3].narration = ("Then Milo found a torch, but the battery "
                                 "was dead. Slow and slow, up we go.")
    entry.recompute()
    assert [label for label, _ in unsafe(entry)] == []

    entry.scenes[3].narration = ("Then Milo found a knife. "
                                 "Slow and slow, up we go.")
    entry.recompute()
    assert "violence" in [label for label, _ in unsafe(entry)]

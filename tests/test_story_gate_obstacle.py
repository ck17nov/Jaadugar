"""`has_obstacle` has to recognise an obstacle, not a contrast word.

It was a negation-and-contrast matcher - `but | however | still | couldn't |
didn't | fails | stuck` - and concrete writing needs none of those. Measured
on 43 narrative entries of the real bank, it flagged 18 as "nothing goes
wrong anywhere", including:

    "The crack split wide open. The can emptied onto the path. The tap
     was locked."
    "The ball bounced into the drain. The game stopped. No bat for anybody."
    "Two more hands land on Priya's drum. Three want one. The beat breaks."

Each of those is precisely the obstacle the beat table asks for - a second
person who wants it too, a limit appearing, the attempt breaking something.

The damage was not the false alarm. Two authors were told that the fix was
to add the word "but" to scene two, and one of them did it. A check that can
be satisfied by inserting a filler conjunction teaches the opposite of what
it exists for, which is exactly how the old beat table produced nine
body-ache obstacles in a row.
"""
from __future__ import annotations

import pytest

from engine.content.story_gate import evaluate

BEATS = ["want", "attempt", "obstacle", "turn", "resolve", "refrain",
         "refrain", "refrain"]


def _has_obstacle(narrations: list[str]) -> bool:
    beats = BEATS[:len(narrations)]
    report = evaluate(narrations, beats=beats)
    found = [f for f in report.findings if f.check == "has_obstacle"]
    assert found, "the check must always report"
    return found[0].passed


def _story(obstacle: str) -> list[str]:
    """A story whose ONLY candidate obstacle is the line under test."""
    return [
        "Tara wanted the last mango on the branch.",
        "She stretched up on her toes and reached.",
        obstacle,
        "She hooked her skipping rope over the branch and pulled it low.",
        "The mango dropped into her hands, warm from the sun.",
        "One hop, one reach, one mango.",
        "One hop, one reach, one mango.",
        "One hop, one reach, one mango.",
    ]


class TestSomebodyElseTakesIt:
    """The commonest real obstacle in this bank, and named in the beat
    table: "a second person who wants it too"."""

    @pytest.mark.parametrize("line", [
        "The taller cousin claimed the whole branch.",
        "A bigger boy took the fruit picker.",
        "The last mango sits in another girl's fist.",
        "Her brother grabbed the stool first.",
        "Someone else wants it too.",
    ])
    def test_it_counts(self, line):
        assert _has_obstacle(_story(line)) is True, line


class TestTheThingBreaksOrEnds:
    @pytest.mark.parametrize("line", [
        "The branch snapped in her hand.",
        "The crack split wide open and the can emptied onto the path.",
        "The water ran out halfway up the hill.",
        "The tap was locked for the afternoon.",
        "The shadow shrank to the size of a pea.",
        "The words vanish under the wax.",
    ])
    def test_it_counts(self, line):
        assert _has_obstacle(_story(line)) is True, line


class TestALimitAppears:
    @pytest.mark.parametrize("line", [
        "Only one mango was left on the whole tree.",
        "There was not enough rope to reach.",
        "Her count drops to zero.",
        "The game stopped and nobody had a turn.",
    ])
    def test_it_counts(self, line):
        assert _has_obstacle(_story(line)) is True, line


class TestHindi:
    """The Hindi list was five contrast words and three failures, so a
    Devanagari story describing a real mishap scored zero."""

    @pytest.mark.parametrize("line", [
        "दोनों नावें सँकरी नाली में फँस गईं।",
        "बाल्टी छत के पार लुढ़की।",
        "दादी की बारी ख़ाली रही।",         # NUKTA: खाली was listed, ख़ाली was not
        "कविता बीच में रुकी।",              # INFLECTION: रुक गया was listed, रुकी was not
        "गुड्डू की नाव भीगने लगी।",
        "उसका खिलौना टूट गया।",
    ])
    def test_it_counts(self, line):
        story = [
            "तारा को डाल का आख़िरी आम चाहिए था।",
            "उसने पंजों पर खड़े होकर हाथ बढ़ाया।",
            line,
            "उसने रस्सी डाल पर फँसाकर उसे नीचे खींच लिया।",
            "आम उसके हाथ में आ गिरा, धूप से गरम।",
            "एक उछाल, एक हाथ, एक आम।",
            "एक उछाल, एक हाथ, एक आम।",
            "एक उछाल, एक हाथ, एक आम।",
        ]
        assert _has_obstacle(story) is True, line


class TestItStillCatchesAStoryWithNoObstacle:
    """The point of widening it is accuracy, not permissiveness."""

    def test_nothing_goes_wrong_is_still_flagged(self):
        flat = [
            "Milo wanted a red ball.",
            "Milo looked up at the shelf.",
            "Milo reached up and held it.",
            "Milo smiled and sat down.",
            "Round and red, round and red.",
            "Round and red, round and red.",
            "Round and red, round and red.",
        ]
        assert _has_obstacle(flat) is False

    def test_a_want_and_an_easy_win_is_still_flagged(self):
        easy = [
            "Nita wanted to hear the bell.",
            "She walked to the gate and lifted the rope.",
            "The bell rang out over the yard.",
            "She rang it once more for the fun of it.",
            "Ring and run, ring and run.",
            "Ring and run, ring and run.",
            "Ring and run, ring and run.",
        ]
        assert _has_obstacle(easy) is False


def test_the_negation_vocabulary_still_works():
    """A widening, not a replacement. "The boat would not float" is a real
    obstacle and has to keep counting."""
    for line in ("The kite would not lift at all.",
                 "She could not reach the branch.",
                 "The tower kept toppling over."):
        assert _has_obstacle(_story(line)) is True, line


MAX_FLAGGED_SHARE = 0.05


def test_the_whole_bank_mostly_passes_and_that_is_the_measurement():
    """18 of 43 narratives before, 1 of 262 after - and the controls above
    prove it is not simply matching everything.

    A RATE, not zero. This asserted zero when the bank held 43 entries I
    had written myself, and it broke the build the moment an outside batch
    landed: 39 of 309 flagged, of which 21 were POEMS, which have no
    obstacle beat and never did. Zero-flagged is a property of the content,
    not of this code, so asserting it makes every future ingest a test
    failure and teaches the wrong lesson - that the fix is to loosen the
    pattern until the number comes back down.

    `evaluate` no longer asks a poem for an obstacle, so only shapes whose
    beat table contains one are counted here.
    """
    from pathlib import Path

    from engine.content.bank import load_jsonl

    flagged = []
    checked = 0
    for path in sorted(Path("banks").glob("*.jsonl")):
        entries, _ = load_jsonl(path)
        for entry in entries:
            if entry.shape != "narrative":
                continue
            checked += 1
            report = evaluate(entry.narrations(),
                              beats=[s.beat for s in entry.scenes])
            if any(f.check == "has_obstacle" and not f.passed
                   for f in report.findings):
                flagged.append(entry.entry_id)
    if checked:
        share = len(flagged) / checked
        assert share <= MAX_FLAGGED_SHARE, (
            f"{len(flagged)} of {checked} narratives ({share:.0%}) read as "
            f"having no obstacle, over the {MAX_FLAGGED_SHARE:.0%} "
            f"threshold: {flagged[:5]}")


def test_a_poem_is_never_asked_for_an_obstacle():
    """It has no obstacle beat, so the question does not apply.

    This cost 21 of 54 banked poems a finding for missing a structural
    element their own form does not contain. The beats settle it - a poem
    is open / verse_a / refrain / verse_b / refrain_2 / verse_c / close.
    """
    verses = ["Clap with Aarav: clap, clap, chime.",
              "Clap with Aarav: clap, clap, chime.",
              "Two small hands and a bright red drum.",
              "Clap with Aarav: clap, clap, chime.",
              "Three quick taps and the song is done.",
              "Clap with Aarav: clap, clap, chime."]
    poem_beats = ["open", "refrain", "verse_a", "refrain_2", "verse_b",
                  "close"]
    checks = {f.check for f in evaluate(verses, beats=poem_beats).findings}
    assert "has_obstacle" not in checks

    # A narrative with the beat still gets asked, or the fix is a mute
    # button rather than a correction.
    story_beats = ["want", "attempt", "obstacle", "turn", "resolve",
                   "refrain"]
    checks = {f.check for f in evaluate(verses, beats=story_beats).findings}
    assert "has_obstacle" in checks

    # And so does an entry whose beats the caller did not pass at all.
    checks = {f.check for f in evaluate(verses).findings}
    assert "has_obstacle" in checks


# ==========================================================================
class TestTheBlockingGateDoesNotRejectARealObstacle:
    """`obstacle_is_more_than_a_feeling` BLOCKS, so a gap in its escape
    hatch stops good writing from ever being banked.

    Reported by an author mid-run: this scene was rejected as "the obstacle
    is only a body feeling".

        "His sister took one chair for her dolls. Four corners, one roof,
         hold tight."

    The obstacle is the sister taking the chair - the first example of a
    good obstacle in the beat table. It failed because the refrain riding
    along in the same scene says "tight", and `_COMPLICATION` had no word
    for somebody taking something: it listed `another`, `broke`, `snapped`,
    `ran out`, and not `took`.

    The author's workaround was to rename the refrain. That is the gate
    editing the writing for the wrong reason, which is the same failure as
    the beat table that produced nine body-ache obstacles in a row.
    """

    BEATS = ["want", "attempt", "obstacle", "turn", "resolve", "refrain",
             "refrain", "refrain"]

    def _blocking(self, narrations):
        report = evaluate(narrations, beats=self.BEATS[:len(narrations)])
        return [f.check for f in report.findings
                if not f.passed and f.blocking]

    def _fort(self, obstacle_line):
        return [
            "Dev wanted a fort over the whole bed.",
            "Dev draped the bedsheet across two chairs.",
            obstacle_line,
            "Dev wedged the sheet under the mattress instead of the chair.",
            "Dev crawled in, and the fort stood over him.",
            "Four corners, one roof, hold tight.",
            "Four corners, one roof, hold tight.",
            "Four corners, one roof, hold tight.",
        ]

    def test_the_reported_scene_is_not_blocked(self):
        line = ("Dev saw that his sister took one chair for her dolls. "
                "Four corners, one roof, hold tight.")
        assert "obstacle_is_more_than_a_feeling" not in self._blocking(
            self._fort(line))

    @pytest.mark.parametrize("verb", ["took", "takes", "take", "taking",
                                      "claimed", "grabbed", "kept"])
    def test_every_form_of_somebody_taking_it_counts(self, verb):
        """"watched his sister take one chair" is the same obstacle as
        "his sister took one chair", and the infinitive was missed."""
        line = (f"Dev saw his sister {verb} the one good chair. "
                f"Four corners, one roof, hold tight.")
        assert "obstacle_is_more_than_a_feeling" not in self._blocking(
            self._fort(line)), verb

    @pytest.mark.parametrize("line", [
        "Dev found the sheet had torn along one edge. Hold tight, hold tight.",
        "Dev had only one chair left for four corners. Hold tight, hold tight.",
        "Dev heard the clips snap off the line. Hold tight, hold tight.",
    ])
    def test_breaking_and_limits_count_alongside_a_body_word(self, line):
        assert "obstacle_is_more_than_a_feeling" not in self._blocking(
            self._fort(line)), line

    def test_a_genuine_body_only_obstacle_is_still_blocked(self):
        """The widening must not disarm the check - this is the failure it
        was written for, and twelve of nineteen stories once shared it."""
        ache = [
            "Milo wanted the top shelf jar.",
            "Milo stretched up on his toes.",
            "Milo felt his chest go tight and his arms ached.",
            "Milo stacked two books and stood on them.",
            "Milo held the jar and grinned.",
            "One stretch, one stack, one jar.",
            "One stretch, one stack, one jar.",
            "One stretch, one stack, one jar.",
        ]
        assert "obstacle_is_more_than_a_feeling" in self._blocking(ache)

    def test_hindi_taking_counts_too(self):
        story = [
            "देव को पूरे बिस्तर पर किला चाहिए था।",
            "देव ने चादर दो कुर्सियों पर फैलाई।",
            "देव की बहन एक कुर्सी ले गई। चार कोने, एक छत, कसकर पकड़ो।",
            "देव ने चादर गद्दे के नीचे दबा दी।",
            "देव अंदर घुसा और किला टिका रहा।",
            "चार कोने, एक छत, कसकर पकड़ो।",
            "चार कोने, एक छत, कसकर पकड़ो।",
            "चार कोने, एक छत, कसकर पकड़ो।",
        ]
        assert "obstacle_is_more_than_a_feeling" not in self._blocking(story)

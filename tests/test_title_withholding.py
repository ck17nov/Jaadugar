"""A story title must not answer its own question.

The operator's complaint was "the title of the story is my main concern".
The plumbing was fine - a banked title reaches the video verbatim - so the
problem was the writing. But rewriting the titles alone would have changed
nothing published, because the SCORER preferred the old shape.

Measured on the entry that was actually rendered (kids-hi-d404cd03d4):
"आरव, गेंद और खाट का दूसरा सिरा" scored 75.8 while a title that stops at
the problem scored 50.2 - and "दूसरा सिरा" IS the turn the story pivots on.
Search relevance and specificity both reward vocabulary the script uses, so
for a story the top-scoring title was reliably the one describing the
ending. The pipeline was selecting spoilers on purpose.
"""
from __future__ import annotations

import pytest

from engine.content.metadata import MetadataGenerator, _visible_title
from engine.core.config import load_config
from engine.core.models import ContentIdea, Scene, Script


@pytest.fixture()
def gen():
    return MetadataGenerator(load_config(), None)


@pytest.fixture()
def story() -> Script:
    """Seven beats, with the turn at scene five - the real shape."""
    beats = [
        "Aarav was tossing his ball in the courtyard.",
        "The ball rolled under the heavy cot.",
        "His hand went in but his fingers fell short.",
        "He tugged the cot twice and it did not budge.",
        "Then Aarav peeked at the far end of the cot.",
        "A wide gap stood open there and he slid his hand in.",
        "The ball met his palm and he laughed.",
    ]
    scenes = [Scene(index=i, narration=t) for i, t in enumerate(beats)]
    return Script(scenes=[s.to_dict() for s in scenes],
                  script=" ".join(beats))


@pytest.fixture()
def idea():
    return ContentIdea(topic="kids bedtime stories", angle="")


# ---------------------------------------------------------------------------
# The visible title
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("decorated,plain", [
    ("🧡 Aarav Cannot Reach the Ball… What Now? #shorts #kahani",
     "Aarav Cannot Reach the Ball… What Now?"),
    ("Aarav and the Cot | Aarav and the Ball", "Aarav and the Cot"),
    ("Kiran and the Paper Boat", "Kiran and the Paper Boat"),
])
def test_the_furniture_is_not_counted_as_the_title(decorated, plain):
    """Emoji, gloss and hashtags are conventions the niche's winners use.

    Counting them as words and characters made every title that followed
    the convention lose to a bare four-word plot label - one trailing
    emoji cost an identical Hindi sentence 6.4 points.
    """
    assert _visible_title(decorated) == plain


def test_decoration_does_not_change_the_score(gen, story, idea):
    bare = "Aarav Cannot Reach His Ball Under the Heavy Cot Now"
    dressed = f"🧡 {bare} #shorts #kahani"
    assert (gen.score_title(dressed, story, idea, withhold=True)["score"]
            == gen.score_title(bare, story, idea, withhold=True)["score"])


# ---------------------------------------------------------------------------
# The inversion
# ---------------------------------------------------------------------------
def test_a_title_that_gives_away_the_turn_is_penalised(gen, story, idea):
    spoiler = gen.score_title("Aarav, the Ball and the Far End of the Cot",
                              story, idea, withhold=True)
    assert any("ending" in r for r in spoiler["risk_reasons"]), \
        spoiler["risk_reasons"]


def test_withholding_beats_spoiling_on_the_same_story(gen, story, idea):
    """The acceptance test, in English: it was 50.2 against 75.8."""
    spoiler = gen.score_title("Aarav, the Ball and the Far End of the Cot",
                              story, idea, withhold=True)["score"]
    withholding = gen.score_title(
        "Aarav's Hand Cannot Reach the Ball Under the Cot… Now What?",
        story, idea, withhold=True)["score"]
    assert withholding > spoiler, f"{withholding} !> {spoiler}"


def test_the_premise_is_not_a_spoiler(gen, story, idea):
    """The character and the object are the SETUP and stay free.

    Penalising them would make every honest title lose, since a title with
    neither the character nor the subject in it says nothing.
    """
    result = gen.score_title("Aarav Cannot Reach His Ball Anywhere at All",
                             story, idea, withhold=True)
    assert not any("ending" in r for r in result["risk_reasons"]), \
        result["risk_reasons"]


def test_an_explainer_still_wants_its_own_words(gen, story, idea):
    """Withholding is for STORIES. A how-to is found by matching its words,
    so the search weighting stays for everything else."""
    titled = "Aarav, the Ball and the Far End of the Cot"
    as_story = gen.score_title(titled, story, idea, withhold=True)
    as_explainer = gen.score_title(titled, story, idea, withhold=False)
    assert as_explainer["score"] > as_story["score"]
    assert not any("ending" in r for r in as_explainer["risk_reasons"])


def test_hindi_inflection_does_not_hide_a_spoiler(gen, idea):
    """"दूसरे सिरे" in the narration and "दूसरा सिरा" in the title are the
    same spoiler, and never match as strings."""
    beats = [
        "आरव आँगन में गेंद उछाल रहा था।",
        "गेंद लुढ़ककर भारी खाट के नीचे चली गई।",
        "उसने हाथ अंदर डाला पर उँगलियाँ नहीं पहुँचीं।",
        "आरव ने खाट दो बार खींची, वह हिली नहीं।",
        "तभी आरव ने खाट के दूसरे सिरे पर झाँका।",
        "वहाँ चौड़ी जगह खुली थी और उसने हाथ अंदर सरकाया।",
        "गेंद हाथ में आ गई और वह हँस पड़ा।",
    ]
    scenes = [Scene(index=i, narration=t) for i, t in enumerate(beats)]
    script = Script(scenes=[s.to_dict() for s in scenes],
                    script=" ".join(beats))
    result = gen.score_title("आरव, गेंद और खाट का दूसरा सिरा", script, idea,
                             withhold=True)
    assert any("ending" in r for r in result["risk_reasons"]), \
        result["risk_reasons"]


# ---------------------------------------------------------------------------
# Hindi could not earn a fifth of the marks
# ---------------------------------------------------------------------------
def test_a_hindi_question_earns_curiosity(gen, idea):
    """Curiosity is weighted 0.20 and emotional pull 0.10, and neither word
    set had a single Devanagari entry - so 22 of the 100 points were
    unreachable for a Hindi title however good it was."""
    from engine.content.metadata import CURIOSITY_WORDS

    for word in ("क्या", "क्यों", "कैसे", "कहाँ"):
        assert word in CURIOSITY_WORDS


def test_the_hindi_hook_is_actually_measured():
    """`re.findall(r"[A-Za-z']+")` returned [] for Devanagari, so the delay
    was 0.0 and every Hindi opening line scored a perfect hook."""
    from engine.content.retention import _hook_delay

    slow = "आरव आँगन में गेंद उछाल रहा था और गेंद लुढ़ककर खाट के नीचे छिप गई"
    delay, tokens = _hook_delay(slow)
    assert tokens > 0, "the Hindi opening was not tokenised at all"
    assert delay > 0.0

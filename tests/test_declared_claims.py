"""A finance explainer must be able to declare its own arithmetic.

The bank prompt tells finance authors to use "round illustrative figures and
say they are illustrative". The fact checker then flagged every one of them as
"numeric claim not declared in claims array" - measured at twelve flags on a
real 72-scene expense-ratio explainer, which made risk=medium permanent and
forced manual approval on every finance video for a reason nobody could act
on. A warning that always fires carries no information.
"""
from __future__ import annotations

import pytest

from engine.content import bank_use
from engine.content.bank import BankEntry
from engine.content.originality import FactChecker, cites_research
from engine.core.config import load_config
from engine.core.niche import build_profile


def _finance_entry(claims=None) -> BankEntry:
    return BankEntry.from_dict({
        "group": "finance", "topic": "personal finance", "shape": "explainer",
        "language": "en", "video_format": "LONGFORM",
        "title": "How an Expense Ratio Compounds",
        "claims": claims or [],
        "scenes": [
            {"beat": "hook",
             "narration": "2 percent a year sounds small.",
             "caption": "साल में 2 प्रतिशत छोटा लगता है।",
             "image_brief": "A plain notebook on a wooden desk, soft daylight"},
            {"beat": "worked_example",
             "narration": "Picture a balance of 1 lakh rupees, purely as an "
                          "illustration. A 2 percent ratio takes about 2,000 "
                          "rupees out of it that year.",
             "caption": "मान लीजिए एक लाख रुपये। दो प्रतिशत यानी दो हज़ार।",
             "image_brief": "A calculator beside a printed statement"},
            {"beat": "payoff",
             "narration": "The cost is charged on the whole balance, every "
                          "year.",
             "caption": "यह लागत हर साल पूरी रकम पर लगती है।",
             "image_brief": "A simple line chart drawn on graph paper"},
        ],
    })


@pytest.fixture()
def checker(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    return FactChecker(load_config())


@pytest.fixture()
def profile():
    return build_profile("personal finance", duration_seconds=300)


def test_undeclared_figures_are_flagged(checker, profile):
    """The check is doing its job - this half must keep working."""
    script = bank_use.to_script(_finance_entry(), language="en")
    result = checker.check(script, profile)
    assert result.flagged
    assert any("not declared" in str(f) for f in result.flagged)


def test_declaring_them_clears_the_flags(checker, profile):
    entry = _finance_entry(claims=[
        {"claim": "2 percent a year", "confidence": "medium",
         "basis": "round illustrative rate, named as an illustration"},
        {"claim": "1 lakh rupees", "confidence": "medium",
         "basis": "round illustrative balance"},
        {"claim": "2,000 rupees", "confidence": "high",
         "basis": "arithmetic: 2 percent of the stated 1 lakh"},
    ])
    result = checker.check(
        bank_use.to_script(entry, language="en"), profile)
    assert result.flagged == []
    assert result.risk == "low"
    assert result.requires_approval is False


def test_claims_survive_the_round_trip():
    """Declared in the file, carried into the Script the renderer sees."""
    entry = _finance_entry(claims=[
        {"claim": "2 percent", "confidence": "medium", "basis": "illustrative"},
    ])
    assert entry.claims[0]["claim"] == "2 percent"
    assert entry.to_dict()["claims"] == entry.claims
    assert BankEntry.from_dict(entry.to_dict()).claims == entry.claims
    assert bank_use.to_script(entry).claims == entry.claims


def test_a_claim_with_no_text_is_dropped():
    """An empty row would declare nothing while looking like a declaration."""
    entry = _finance_entry(claims=[
        {"claim": "", "confidence": "high", "basis": "nothing"},
        {"claim": "2 percent", "confidence": "medium", "basis": "illustrative"},
    ])
    assert len(entry.claims) == 1


def test_a_story_needs_no_claims_block(checker, profile):
    """Asking every bedtime story for an empty array would be noise."""
    from engine.content import bank_prompt
    text = bank_prompt.build(group_key="kids", language="en",
                             video_format="SHORT", target_seconds=50,
                             count=1, shape="narrative")
    assert "NUMBERS - declare every one" not in text

    finance = bank_prompt.build(group_key="finance", language="en",
                                video_format="LONGFORM", target_seconds=300,
                                count=1, shape="explainer")
    assert "NUMBERS - declare every one" in finance


# ---------------------------------------------------------------------------
# The research-citation check
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    # The one that actually misfired, in a finance explainer.
    "One sheet of paper is enough to run it.",
    "Write the two numbers on a piece of paper.",
    "He folded a paper boat and set it down.",
    "The report card came home in her bag.",
])
def test_ordinary_paper_is_not_a_citation(text):
    assert cites_research(text) is False, text


@pytest.mark.parametrize("text", [
    "A 2019 paper found the opposite.",
    "The paper published last year says otherwise.",
    "A recent report shows the same pattern.",
    "Studies show that people save more when it is automatic.",
    "Researchers measured it directly.",
    "A survey of two thousand households found this.",
])
def test_a_real_citation_still_fires(text):
    assert cites_research(text) is True, text


# ---------------------------------------------------------------------------
# Per-scene caption language
# ---------------------------------------------------------------------------
def _kids_entry_from_disk(path: str, index: int = 0):
    """A real banked entry, read from a FIXTURE rather than from banks/.

    These two files used to live in banks/ and were therefore shipped
    content as well as test input - so emptying or regenerating the bank
    broke three tests that have nothing to do with the catalogue. They are
    fixtures; they belong here.
    """
    import json
    from pathlib import Path
    from engine.content.bank import BankEntry
    here = Path(__file__).resolve().parent / 'fixtures' / Path(path).name
    line = here.read_text(encoding="utf-8").splitlines()[index]
    return BankEntry.from_dict(json.loads(line))


def test_one_same_language_caption_is_caught():
    """The aggregate check missed this, and it is the likeliest mistake.

    Comparing all narrations against all captions joined meant one English
    caption among five Hindi ones still left the joined text predominantly
    Devanagari. Verified on a shipped entry: not even a warning, and the
    English text was burned onto an English-narrated frame.
    """
    from engine.content.bank import validate
    entry = _kids_entry_from_disk("banks/kids-en-short-test.jsonl")
    assert validate(entry, expect_group="kids") == []

    entry.scenes[0].caption = entry.scenes[0].narration
    entry.recompute()
    problems = validate(entry, expect_group="kids")
    assert any(p.field_name == "scenes[0].caption" and p.fatal
               for p in problems), [str(p) for p in problems]


def test_a_hindi_entry_needs_english_captions():
    from engine.content.bank import validate
    entry = _kids_entry_from_disk("banks/kids-hi-short-test.jsonl")
    assert validate(entry, expect_group="kids") == []

    entry.scenes[2].caption = entry.scenes[2].narration
    entry.recompute()
    assert any(p.field_name == "scenes[2].caption" and p.fatal
               for p in validate(entry, expect_group="kids"))


def test_a_missing_caption_does_not_disable_the_language_check():
    """It used to: the comparison ran only in the all-captions-present branch."""
    from engine.content.bank import validate
    entry = _kids_entry_from_disk("banks/kids-en-short-test.jsonl")
    entry.scenes[1].caption = ""
    entry.scenes[0].caption = entry.scenes[0].narration
    entry.recompute()
    fields = [p.field_name for p in validate(entry, expect_group="kids")]
    assert "scenes[0].caption" in fields, fields

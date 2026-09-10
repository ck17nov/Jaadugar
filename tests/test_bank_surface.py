"""What the operator is told, and whether a claim agrees with it.

Every failure here was a case of the interface promising something the render
would refuse. That is worse than a plain bug: the operator acts on the
promise, and only finds out after a six-minute render.
"""
from __future__ import annotations

import json

import pytest
from rich.console import Console
from rich.markup import escape

from engine.content import bank_import
from engine.content.bank import BankEntry, load_jsonl
from engine.content.bank_use import approved, claim
from engine.core.db import Database
from engine.core.languages import claimable
from tests.test_bank import _write, kids_entry


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    yield database
    database.close()


def _rendered(text: str, tmp_path) -> str:
    """What the CLI's console actually prints for a report line."""
    target = tmp_path / "printed.txt"
    with open(target, "w", encoding="utf-8") as handle:
        Console(file=handle, width=240, markup=True).print(
            f"[red]{escape(text)}[/red]")
    return target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Language dialects
# ---------------------------------------------------------------------------
def test_indian_english_finds_english_entries(db):
    """It found none of fifteen before.

    The claim matched the language string exactly, so picking Indian English
    plus "Only my reviewed scripts" failed with a full bank - while the app
    folded dialects itself and said fifteen were ready.
    """
    db.save_bank_entry(kids_entry())
    assert claim(db, group="kids", language="en-IN",
                 video_format="SHORT", job_id="j") is not None


def test_hinglish_does_not_claim_a_devanagari_entry(db):
    """Base language alone would be the wrong rule.

    "hi-Latn" is Hindi in LATIN letters, so a Devanagari script read by a
    Hinglish voice would be wrong - and folding on base language alone pairs
    them.
    """
    entry = kids_entry()
    entry.language = "hi"
    for index, scene in enumerate(entry.scenes):
        scene.caption = f"An English caption for scene {index}."
    entry.recompute()
    db.save_bank_entry(entry)
    assert claim(db, group="kids", language="hi-Latn",
                 video_format="SHORT", job_id="j") is None


@pytest.mark.parametrize("code,expected", [
    ("en", {"en", "en-IN"}),
    ("en-IN", {"en", "en-IN"}),
    ("hi", {"hi"}),
    ("hi-Latn", {"hi-Latn"}),
])
def test_the_compatible_set(code, expected):
    assert set(claimable(code)) == expected


def test_an_unknown_language_matches_only_itself():
    assert claimable("ta") == ("ta",)
    assert claimable("") == ()


# ---------------------------------------------------------------------------
# Availability must mean claimable
# ---------------------------------------------------------------------------
def test_a_rejected_entry_is_not_available(db):
    """The status endpoint counted any entry with a reviewer NAME as ready."""
    entry = kids_entry()
    entry.human = {"reviewer": "chandan", "verdict": "reject",
                   "element": "the ending does not work"}
    db.save_bank_entry(entry)

    stored = BankEntry.from_dict(json.loads(db.bank_entries()[0]["payload"]))
    assert approved(stored) is False
    assert claim(db, group="kids", language="en",
                 video_format="SHORT", job_id="j") is None


def test_the_topic_filter_is_honoured(db):
    """A count that ignores the topic promises what a claim will not take."""
    db.save_bank_entry(kids_entry(topic="kids alphabet learning"))
    assert claim(db, group="kids", language="en", video_format="SHORT",
                 job_id="j", topics=["kids bedtime stories"]) is None
    assert claim(db, group="kids", language="en", video_format="SHORT",
                 job_id="j2", topics=["kids alphabet learning"]) is not None


# ---------------------------------------------------------------------------
# The report has to be findable
# ---------------------------------------------------------------------------
def test_a_rejection_keeps_its_line_number(tmp_path, db):
    """Rich ate the square brackets, so the line number vanished.

    "REJECT (no id) [line 7] is not valid JSON" printed as "REJECT (no id)
    is not valid JSON", and the line number is the only way to find the bad
    line in a 300-entry file. A model reply saved with its code fence still
    attached printed three identical, unlocatable rejections.
    """
    path = tmp_path / "bad.jsonl"
    path.write_text("~~~jsonl\n{}\n".replace("~~~", "```"), encoding="utf-8")
    report = bank_import.import_file(path, db, expect_group="kids")
    fence = [r for r in report.rejected if "not valid JSON" in r]
    assert fence, report.rejected
    assert "[line 1]" in fence[0]
    assert "[line 1]" in _rendered(fence[0], tmp_path)


def test_a_field_name_survives_printing(tmp_path, db):
    entry = kids_entry()
    entry.title = ""
    entry.recompute()
    report = bank_import.import_file(_write(tmp_path, entry), db,
                                     expect_group="kids")
    line = next(r for r in report.rejected if "title" in r)
    assert "[title]" in _rendered(line, tmp_path)


# ---------------------------------------------------------------------------
# Malformed shapes
# ---------------------------------------------------------------------------
def test_a_non_text_field_is_named_not_narrated(tmp_path, db):
    """It became a Python repr that edge-tts would read aloud.

    An image_brief written as {"subject": "a boy"} became the literal
    "{'subject': 'a boy'}" and went straight to the image generator, because
    the bank path deliberately has no later LLM rewrite. validate() saw a
    non-empty string and said nothing.
    """
    raw = kids_entry().to_dict()
    raw["scenes"][1]["image_brief"] = {"subject": "a boy"}
    path = tmp_path / "b.jsonl"
    path.write_text(json.dumps(raw, ensure_ascii=False) + "\n",
                    encoding="utf-8")

    report = bank_import.import_file(path, db, expect_group="kids")
    assert report.stored == 0
    assert any("image_brief" in r and "not text" in r
               for r in report.rejected), report.rejected


def test_a_list_of_strings_is_recovered(tmp_path):
    """The author meant consecutive lines, which is recoverable."""
    raw = kids_entry().to_dict()
    original = raw["scenes"][1]["narration"]
    raw["scenes"][1]["narration"] = [original, "He tried once more."]
    path = tmp_path / "b.jsonl"
    path.write_text(json.dumps(raw, ensure_ascii=False) + "\n",
                    encoding="utf-8")

    entries, problems = load_jsonl(path)
    assert entries, [str(p) for p in problems]
    assert entries[0].scenes[1].narration == f"{original} He tried once more."


def test_a_dropped_scene_is_reported(tmp_path, db):
    """It vanished silently and the entry rendered a scene short.

    from_dict filtered scenes on isinstance and recompute() then derived
    scene_count from the survivors, so a 6-scene entry imported as 5 with
    nothing reported - one beat, its authored caption and its brief simply
    gone from the video.
    """
    raw = kids_entry().to_dict()
    raw["scenes"][2] = "The branch was far above him."
    path = tmp_path / "b.jsonl"
    path.write_text(json.dumps(raw, ensure_ascii=False) + "\n",
                    encoding="utf-8")

    report = bank_import.import_file(path, db, expect_group="kids")
    assert report.stored == 0
    assert any("scenes[2]" in r and "not an object" in r
               for r in report.rejected), report.rejected


def test_a_dropped_character_is_reported(tmp_path, db):
    """The variety gate needs the cast to detect a renamed duplicate."""
    raw = kids_entry().to_dict()
    raw["characters"] = ["Milo"]
    path = tmp_path / "b.jsonl"
    path.write_text(json.dumps(raw, ensure_ascii=False) + "\n",
                    encoding="utf-8")

    report = bank_import.import_file(path, db, expect_group="kids")
    assert any("characters[0]" in r for r in report.rejected), report.rejected


def test_a_malformed_line_does_not_half_import(tmp_path, db):
    """It was reported as rejected AND stored - the worst of both."""
    good = kids_entry().to_dict()
    bad = kids_entry(name="Asha", refrain="Up and up, we go up",
                     setting="library", domain="lost_item").to_dict()
    bad["scenes"][1]["narration"] = {"line": "he jumped"}
    path = tmp_path / "b.jsonl"
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in (good, bad))
        + "\n", encoding="utf-8")

    report = bank_import.import_file(path, db, expect_group="kids")
    assert report.stored == 1
    assert len(db.bank_entries()) == 1
    assert db.bank_entries()[0]["title"] == good["title"]


def test_a_clean_file_is_untouched_by_any_of_this(tmp_path, db):
    """The shape checks must not reject anything well-formed."""
    report = bank_import.import_file(_write(tmp_path, kids_entry()), db,
                                     expect_group="kids")
    assert report.rejected == []
    assert report.stored == 1

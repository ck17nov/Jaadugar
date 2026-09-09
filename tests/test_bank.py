"""The script bank: schema, variety, import, claim, and consumption.

The variety tests are the important ones. The story gate enforces sameness of
SHAPE by design - named protagonist, failed attempts, verbatim refrain - so a
bank generated in bulk will pass it while being three hundred tellings of one
story, which is the "impression of mass production" YouTube's inauthentic
content policy prohibits. These tests pin the thing that catches that.
"""
from __future__ import annotations

import json

import pytest

from engine.content import bank, bank_import, bank_prompt, bank_use, variety
from engine.core.db import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _scene(beat: str, narration: str, caption: str, brief: str) -> dict:
    return {"beat": beat, "narration": narration, "caption": caption,
            "image_brief": brief, "on_screen_text": ""}


def kids_entry(*, name: str = "Milo", refrain: str = "Slow and slow, up we go",
               setting: str = "rooftop", domain: str = "reach_retrieve",
               topic: str = "kids bedtime stories") -> bank.BankEntry:
    """A well-formed kids entry that passes every gate."""
    return bank.BankEntry.from_dict({
        "group": "kids", "topic": topic, "shape": "narrative",
        "language": "en", "video_format": "SHORT", "made_for_kids": True,
        "title": f"{name} and the Kite on the Roof",
        "title_alts": [f"{name} Cannot Reach the Kite", "The Kite Up There"],
        "refrain": refrain,
        "description_hook": "A small boy, a stuck kite, and one good idea.",
        "arc_variant": "alone", "outcome_class": "got_it",
        "problem_domain": domain, "setting": setting,
        "protagonist_type": "boy_6", "emotional_register": "determined",
        "characters": [{"name": name,
                        "description": "six years old, curly black hair, "
                                       "yellow shirt, one scraped knee"}],
        "scenes": [
            _scene("want", f"{name} stood under the mango tree and looked up "
                           f"at his red kite, caught high in the branches. "
                           f"{refrain}.",
                   f"{name} आम के पेड़ के नीचे खड़ा अपनी पतंग देख रहा था।",
                   f"{name}, a six-year-old boy in a yellow shirt, standing "
                   f"under a mango tree looking up at a red kite in the "
                   f"branches, warm afternoon light"),
            _scene("attempt", f"{name} jumped as high as he could. His "
                              f"fingers brushed nothing but leaves and warm "
                              f"air.",
                   f"{name} ने ऊँची छलांग लगाई पर पत्ते ही हाथ आए।",
                   f"{name} jumping with one arm stretched up, leaves "
                   f"falling around him, garden in afternoon light"),
            _scene("obstacle", "The branch was far above him. His chest felt "
                               "tight and his eyes stung a little.",
                   "टहनी बहुत ऊँची थी और उसका मन भर आया।",
                   f"{name} standing still with his shoulders down, looking "
                   f"up at a high branch, long shadows"),
            _scene("turn", f"Then {name} saw the low wall beside the tree. "
                           f"{refrain}. He climbed it, one careful foot at a "
                           f"time.",
                   f"तभी {name} ने पेड़ के पास की दीवार देखी।",
                   f"{name} placing one foot on a low garden wall beside the "
                   f"mango tree, careful expression, golden light"),
            _scene("resolve", f"From the wall, {name} reached out and the "
                              f"kite came free. He held it against his chest "
                              f"and laughed.",
                   f"दीवार से {name} ने पतंग छुड़ा ली और हँस पड़ा।",
                   f"{name} standing on the wall holding a red kite against "
                   f"his chest, laughing, warm light behind him"),
            _scene("refrain", f"{refrain}.",
                   "धीरे धीरे, हम ऊपर जाते हैं।",
                   f"{name} sitting on the low wall with the red kite in his "
                   f"lap, evening light, calm"),
        ],
        "provenance": {"tool": "claude", "batch": "kids-en-short-001"},
        "human": {"reviewer": "chandan", "verdict": "approve",
                  "element": "the low wall was my idea"},
    })


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    yield database
    database.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def test_a_good_entry_validates_and_derives_its_own_length():
    entry = kids_entry()
    assert [str(p) for p in bank.validate(entry, expect_group="kids")] == []
    # Duration is DERIVED from the word count, never authored: kids narrate at
    # 2.0 words per second, so the words are the runtime.
    assert entry.estimated_seconds == pytest.approx(
        entry.word_count / 2.0, rel=0.01)
    assert entry.scene_count == 6
    assert entry.entry_id


@pytest.mark.parametrize("mutate,field_hit", [
    # The whole reason captions are authored: they must be the OTHER language.
    (lambda e: [setattr(s, "caption", s.narration) for s in e.scenes],
     "caption"),
    # An image brief in Devanagari goes to a generator that barely reads it.
    (lambda e: setattr(e.scenes[0], "image_brief", "एक लड़का पतंग देख रहा है"),
     "image_brief"),
    (lambda e: setattr(e, "arc_variant", "invented_arc"), "arc_variant"),
    (lambda e: setattr(e, "refrain", ""), "refrain"),
    (lambda e: setattr(e, "scenes", e.scenes[:2]), "scenes"),
    # Banking time-sensitive material means publishing it stale.
    (lambda e: setattr(e, "volatility", "live_only"), "volatility"),
    (lambda e: setattr(e, "group", "finance"), "group"),
    (lambda e: setattr(e, "title", "x" * 140), "title"),
    (lambda e: setattr(e, "characters", []), "characters"),
])
def test_validate_catches(mutate, field_hit):
    entry = kids_entry()
    mutate(entry)
    entry.recompute()
    problems = bank.validate(entry, expect_group="kids")
    assert any(field_hit in p.field_name for p in problems), \
        f"expected a {field_hit} problem, got {[str(p) for p in problems]}"


def test_a_topic_outside_the_group_warns_but_does_not_reject():
    """It still renders and still goes to the right channel.

    It just cannot be reached by a topic-filtered automation, which is worth
    saying and not worth rejecting over.
    """
    entry = kids_entry(topic="kids dinosaur facts")
    problems = bank.validate(entry, expect_group="kids")
    topic_problems = [p for p in problems if p.field_name == "topic"]
    assert topic_problems and not topic_problems[0].fatal


# ---------------------------------------------------------------------------
# Variety - the monetisation gate
# ---------------------------------------------------------------------------
def test_renaming_the_protagonist_does_not_make_a_new_story():
    """The finding this module exists for.

    `originality.py` compares 4-grams at a 0.80 threshold and a rename-only
    duplicate scores well under it, so it passes. Masking the declared names
    takes the same pair to ~1.0.
    """
    original = kids_entry(name="Milo")
    # Identical in every respect except the protagonist's name. This is the
    # shape a bulk-generated bank actually produces.
    renamed = kids_entry(name="Ravi")

    raw = variety.exact_similarity(
        " ".join(original.narrations()), " ".join(renamed.narrations()))
    masked = variety.exact_similarity(
        variety.mask_names(" ".join(original.narrations()), ["Milo"]),
        variety.mask_names(" ".join(renamed.narrations()), ["Ravi"]))

    assert raw < 0.80, "originality.py would let this through"
    assert masked > 0.95, "with names masked it is the same story"

    issues = variety.check_new(renamed, [original])
    assert any(i.kind == "wording" and i.fatal for i in issues)
    assert any(i.kind == "axes" and i.fatal for i in issues)


def test_a_reused_refrain_is_rejected():
    original = kids_entry(name="Milo")
    other = kids_entry(name="Asha", setting="library", domain="lost_item")
    other.scenes = [bank.BankScene.from_dict(
        _scene("want", "Asha searched the library shelves for her lost "
                       "notebook with the blue cover on it.",
               "आशा अपनी नीली कॉपी ढूँढ़ रही थी।",
               "Asha searching tall library shelves, dust in the light"))]
    other.recompute()
    issues = variety.check_new(other, [original])
    assert any(i.kind == "refrain" and i.fatal for i in issues)


def test_a_genuinely_different_story_passes():
    original = kids_entry(name="Milo")
    other = bank.BankEntry.from_dict({
        "group": "kids", "shape": "narrative", "language": "en",
        "video_format": "SHORT", "made_for_kids": True,
        "title": "Asha Counts the Rain",
        "refrain": "One drop, two drops, count with me",
        "arc_variant": "by_helping", "outcome_class": "helped_another",
        "problem_domain": "loud_noise", "setting": "veranda",
        "protagonist_type": "girl_5", "emotional_register": "curious",
        "characters": [{"name": "Asha", "description": "five, two plaits, "
                                                       "green raincoat"}],
        "scenes": [
            _scene("want", "Asha sat on the veranda while thunder shook the "
                           "windows and her little brother hid behind a "
                           "cushion. One drop, two drops, count with me.",
                   "आशा बरामदे में बैठी थी और उसका भाई डर गया था।",
                   "Asha, five, in a green raincoat on a veranda during "
                   "heavy rain, her small brother hiding behind a cushion"),
            _scene("attempt", "She told him thunder was only a noise. He "
                              "shook his head and stayed behind the cushion.",
                   "उसने कहा गरज सिर्फ़ आवाज़ है, पर वह नहीं माना।",
                   "Asha leaning towards a cushion with a small foot "
                   "sticking out, rain on the windows behind"),
            _scene("turn", "So Asha began counting the raindrops on the step "
                           "out loud. One drop, two drops, count with me.",
                   "फिर आशा सीढ़ी पर बूँदें गिनने लगी।",
                   "Asha pointing at raindrops landing on a stone step, "
                   "counting, grey daylight"),
            _scene("resolve", "Her brother's head came out from the cushion "
                              "and he counted with her, all the way to ten.",
                   "भाई ने कुशन से सिर निकाला और साथ गिनने लगा।",
                   "Asha and her small brother side by side on the veranda "
                   "step, both pointing at the rain"),
            _scene("refrain", "One drop, two drops, count with me.",
                   "एक बूँद, दो बूँद, मेरे साथ गिनो।",
                   "Asha and her brother sitting together watching rain, "
                   "soft grey light"),
        ],
        "human": {"reviewer": "chandan"},
    })
    assert [str(i) for i in variety.check_new(other, [original])] == []


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def _write(tmp_path, *entries):
    path = tmp_path / "batch.jsonl"
    path.write_text("\n".join(json.dumps(e.to_dict(), ensure_ascii=False)
                              for e in entries), encoding="utf-8")
    return path


def test_import_stores_and_reports(tmp_path, db):
    path = _write(tmp_path, kids_entry(name="Milo"))
    report = bank_import.import_file(path, db, expect_group="kids")
    assert report.stored == 1 and report.rejected == []
    assert len(db.bank_entries()) == 1


def test_import_compares_two_entries_inside_one_file(tmp_path, db):
    """Not just against what is already stored.

    Otherwise the first import of a 300-line file has nothing to compare
    against and every duplicate inside it lands.
    """
    path = _write(tmp_path, kids_entry(name="Milo"),
                  kids_entry(name="Ravi", refrain="Bit by bit, we go a bit"))
    report = bank_import.import_file(path, db, expect_group="kids")
    assert report.stored == 1
    assert any("wording" in r or "axes" in r for r in report.rejected)


def test_reimport_does_not_unuse_a_published_script(tmp_path, db):
    """The correction must not republish a story that already went out."""
    path = _write(tmp_path, kids_entry(name="Milo"))
    bank_import.import_file(path, db, expect_group="kids")
    claimed = db.claim_bank_entry(group="kids", language="en",
                                 video_format="SHORT", job_id="job1")
    assert claimed is not None

    bank_import.import_file(path, db, expect_group="kids")
    row = db.bank_entries()[0]
    assert row["used_at"] > 0 and row["used_job_id"] == "job1"


def test_require_review_refuses_an_unread_entry(tmp_path, db):
    entry = kids_entry()
    entry.human = {}
    path = _write(tmp_path, entry)
    report = bank_import.import_file(path, db, expect_group="kids",
                                     require_review=True)
    assert report.stored == 0
    assert any("[review]" in r for r in report.rejected)


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------
def test_a_claim_is_atomic(db):
    """Two automations firing in the same minute must not take one entry."""
    db.save_bank_entry(kids_entry(name="Milo"))
    first = db.claim_bank_entry(group="kids", language="en",
                               video_format="SHORT", job_id="a")
    second = db.claim_bank_entry(group="kids", language="en",
                                video_format="SHORT", job_id="b")
    assert first is not None and second is None


def test_topic_filter_matches_group_wide_entries(db):
    """An entry with no topic must stay reachable.

    A bank imported before the topic field existed would otherwise become
    invisible the moment someone selected a topic.
    """
    entry = kids_entry(topic="")
    db.save_bank_entry(entry)
    got = db.claim_bank_entry(group="kids", language="en",
                              video_format="SHORT", job_id="j",
                              topics=["kids moral stories"])
    assert got is not None


def test_topic_filter_excludes_another_topic(db):
    db.save_bank_entry(kids_entry(topic="kids alphabet learning"))
    got = db.claim_bank_entry(group="kids", language="en",
                              video_format="SHORT", job_id="j",
                              topics=["kids bedtime stories"])
    assert got is None


def test_release_returns_an_entry_after_a_failed_render(db):
    db.save_bank_entry(kids_entry())
    claim = bank_use.claim(db, group="kids", language="en",
                           video_format="SHORT", job_id="j")
    assert claim is not None
    bank_use.release(db, claim, reason="ffmpeg died")
    again = bank_use.claim(db, group="kids", language="en",
                           video_format="SHORT", job_id="j2")
    assert again is not None and again.entry_id == claim.entry_id


def test_release_is_idempotent(db):
    db.save_bank_entry(kids_entry())
    claim = bank_use.claim(db, group="kids", language="en",
                           video_format="SHORT", job_id="j")
    bank_use.release(db, claim)
    bank_use.release(db, claim)
    assert len(db.bank_entries(unused_only=True)) == 1


def test_an_unreviewed_entry_is_not_claimed_by_default(db):
    entry = kids_entry()
    entry.human = {}
    db.save_bank_entry(entry)
    assert bank_use.claim(db, group="kids", language="en",
                          video_format="SHORT", job_id="j") is None


# ---------------------------------------------------------------------------
# Consumption - the fix for "image prompt doesn't match, captions aren't right"
# ---------------------------------------------------------------------------
def test_the_script_carries_the_authored_captions_and_briefs():
    entry = kids_entry()
    script = bank_use.to_script(entry, language="en", caption_language="hi")
    scenes = script.scene_objects()
    assert len(scenes) == len(entry.scenes)
    for scene, banked in zip(scenes, entry.scenes):
        # The caption is the AUTHORED one, not a translation made later.
        assert scene.caption_text == banked.caption
        # The image brief IS the visual prompt. Nothing rewrites it.
        assert scene.visual_prompt == banked.image_brief
        assert scene.narration == banked.narration
    assert script.provider.startswith("bank:")
    assert bank_use.has_authored_captions(entry)


def test_beats_become_scene_roles():
    """Retention analysis, motion and caption style all read `role`.

    Without the mapping every banked scene arrives as "value" and the hook
    gets no hook treatment.
    """
    script = bank_use.to_script(kids_entry())
    roles = [s.role for s in script.scene_objects()]
    assert roles[0] == "hook"
    assert roles[-1] == "cta"


def test_captions_in_the_wrong_language_are_dropped_not_used():
    """Authored captions in the wrong language are worse than none.

    A Hindi caption on a request that asked for Tamil is not a caption, and
    leaving the field empty lets the normal translation path fill it.
    """
    entry = kids_entry()                 # English narration, Hindi captions
    script = bank_use.to_script(entry, language="en", caption_language="ta")
    assert all(not s.caption_text for s in script.scene_objects())


def test_translation_leaves_authored_captions_alone():
    """The direct fix for "captions aren't correct".

    translate_scenes used to fill every scene unconditionally, which would
    have machine-translated straight over the hand-authored ones.
    """
    from engine.content.translate import translate_scenes

    class Boom:
        def complete_json(self, *a, **k):      # pragma: no cover - must not run
            raise AssertionError("translation was called for authored captions")

    scenes = bank_use.to_script(kids_entry()).scene_objects()
    assert translate_scenes(scenes, target="hi", router=Boom()) == 0
    assert all(s.caption_text for s in scenes)


def test_the_cast_comes_from_the_entry_not_an_llm():
    bible = bank_use.bible_for(kids_entry(name="Milo"))
    assert bible is not None
    assert [c.name for c in bible.characters] == ["Milo"]
    assert "curly black hair" in bible.clause_for("Milo reached up")


def test_an_idea_from_a_bank_entry_claims_no_opportunity_score():
    """It has not been scored against live research.

    Inventing a number here would put a fabricated score in front of the
    user and into the strategy learner.
    """
    idea = bank_use.to_idea(kids_entry())
    assert idea.opportunity_score == 0.0
    assert idea.working_title
    assert "chandan" in idea.originality_note


# ---------------------------------------------------------------------------
# The generation prompt
# ---------------------------------------------------------------------------
def test_scene_count_never_leaves_a_still_on_screen_too_long():
    """A 7-minute story cannot be its six beats: that is 70s per picture."""
    for seconds in (30, 50, 120, 300, 420, 600, 900):
        for group, shape, kids in [("kids", "narrative", True),
                                   ("finance", "explainer", False),
                                   ("tech", "procedure", False)]:
            plan = bank_prompt.scene_plan(
                group_key=group, target_seconds=seconds, shape=shape,
                made_for_kids=kids)
            hold = plan["seconds_per_scene"]
            assert hold <= bank_prompt.MAX_SCENE_SECONDS + 0.01, \
                f"{group}/{shape}/{seconds}s holds a still for {hold}s"
            assert plan["scenes_low"] >= 3


def test_the_prompt_states_a_single_consistent_scene_count():
    """It used to say "exactly 6 scenes" and then "vary the scene count"."""
    text = bank_prompt.build(group_key="finance", language="en",
                             video_format="LONG", target_seconds=600, count=1)
    assert "exactly 90 scenes" in text
    assert "Do NOT make every script the same size" not in text


def test_long_form_sections_are_weighted_not_even():
    """An 11-scene hook is not a hook."""
    names = [n for n, _ in bank_prompt.BEATS["explainer"]]
    spread = dict(zip(names, bank_prompt._spread(90, names)))
    assert sum(spread.values()) == 90
    assert spread["mechanism"] > 3 * spread["cta"]
    assert spread["worked_example"] > spread["hook"]
    assert min(spread.values()) >= 1


def test_finance_prompts_carry_the_disclaimer_and_forbid_a_persona():
    text = bank_prompt.build(group_key="finance", language="en",
                             video_format="LONG", target_seconds=300, count=1)
    assert bank_prompt.FINANCE_DISCLAIMER_EN in text
    assert "NO HOST PERSONA" in text
    # An explainer has no cast, and asking for one is how a finance video
    # ends up illustrated with a presenter at a desk.
    assert "no presenter in this channel" in text


def test_hindi_finance_prompts_use_the_hindi_disclaimer():
    text = bank_prompt.build(group_key="finance", language="hi",
                             video_format="LONG", target_seconds=300, count=1)
    assert bank_prompt.FINANCE_DISCLAIMER_HI in text
    assert bank_prompt.FINANCE_DISCLAIMER_EN not in text


def test_the_prompt_asks_for_captions_in_the_other_language():
    english = bank_prompt.build(group_key="kids", language="en",
                                video_format="SHORT", target_seconds=50,
                                count=10)
    hindi = bank_prompt.build(group_key="kids", language="hi",
                              video_format="SHORT", target_seconds=50,
                              count=10)
    assert "Hindi (Devanagari)" in english and '"narration" is in English' in english
    assert '"narration" is in Hindi' in hindi and "in English" in hindi


def test_the_prompt_feeds_back_what_is_already_banked(db):
    """Batch N+1 must not retell batch N."""
    db.save_bank_entry(kids_entry(name="Milo",
                                  refrain="Slow and slow, up we go"))
    context = bank_prompt.context_from_bank(db, group_key="kids",
                                            language="en")
    text = bank_prompt.build(group_key="kids", language="en",
                             video_format="SHORT", target_seconds=50,
                             count=10, **context)
    assert "Milo" in text
    assert "Slow and slow, up we go" in text


def test_viral_titles_are_pattern_input_and_say_not_to_copy():
    text = bank_prompt.build(
        group_key="kids", language="en", video_format="SHORT",
        target_seconds=50, count=10,
        viral_titles=["The Lion Who Could Not Roar | Bedtime Story"])
    assert "Do NOT copy them" in text
    assert "SHAPE ONLY" in text


def test_batch_size_shrinks_for_long_form():
    """A twenty-entry ask for ten-minute scripts guarantees truncation.

    The last entries lose their captions silently, which is the worst
    possible failure for a batch job.
    """
    short = bank_prompt.recommended_count(group_key="kids", target_seconds=50,
                                          made_for_kids=True)
    long = bank_prompt.recommended_count(group_key="finance",
                                         target_seconds=600)
    assert short >= 10
    assert long <= 2


def test_every_beat_in_every_table_has_a_role_and_a_weight():
    """A new beat with no mapping silently becomes a "value" scene."""
    for shape, beats in bank_prompt.BEATS.items():
        for name, _purpose in beats:
            assert name in bank_use._ROLE_FOR_BEAT, \
                f"{shape}:{name} has no scene role"
            assert name in bank_prompt.BEAT_WEIGHTS, \
                f"{shape}:{name} has no section weight"

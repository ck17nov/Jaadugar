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
from engine.core.config import load_config
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


def test_finance_prompts_forbid_a_persona_and_a_written_disclaimer():
    """The RENDERER owns the disclaimer, so the prompt must not ask for one.

    It used to instruct the author to open scene 1 with the disclaimer
    verbatim, while `disclaimer.apply()` also prepends its own scene - so a
    banked finance entry opened with two different disclaimers in a row.
    Measured on a real entry.
    """
    text = bank_prompt.build(group_key="finance", language="en",
                             video_format="LONG", target_seconds=300, count=1)
    assert "DO NOT WRITE A DISCLAIMER" in text
    assert bank_prompt.FINANCE_DISCLAIMER_EN not in text
    assert "NO HOST PERSONA" in text
    # An explainer has no cast, and asking for one is how a finance video
    # ends up illustrated with a presenter at a desk.
    assert "no presenter in this channel" in text


def test_no_finance_prompt_contains_a_disclaimer_in_any_language():
    for language in ("en", "hi"):
        text = bank_prompt.build(group_key="finance", language=language,
                                 video_format="LONG", target_seconds=300,
                                 count=1)
        assert bank_prompt.FINANCE_DISCLAIMER_HI not in text
        assert bank_prompt.FINANCE_DISCLAIMER_EN not in text
        assert "DO NOT WRITE A DISCLAIMER" in text


def test_a_banked_disclaimer_is_not_doubled():
    """Belt to the prompt's braces, for entries banked before it changed."""
    from engine.content import bank_use, disclaimer
    from engine.core.niche import build_profile

    entry = kids_entry()
    entry.group = "finance"
    entry.made_for_kids = False
    entry.topic = "personal finance"
    entry.scenes[0].narration = (
        bank_prompt.FINANCE_DISCLAIMER_EN
        + " A one percent fee sounds small. Over twenty years it is not.")
    entry.recompute()

    script = bank_use.to_script(entry, language="en", caption_language="hi")
    profile = build_profile("personal finance", duration_seconds=60)
    assert disclaimer.has_disclaimer(script) is True
    assert disclaimer.apply(script, profile, language="en") is False


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


# ---------------------------------------------------------------------------
# Approval: reviewed-and-rejected is not the same as reviewed
# ---------------------------------------------------------------------------
def test_a_rejected_entry_is_not_claimable(db):
    """The hole this closes.

    The gate tested `human.reviewer` for non-emptiness, so recording
    "reviewer: chandan, verdict: reject" made an entry MORE claimable than
    leaving it alone. A rejection is the one verdict that has to be
    load-bearing.
    """
    entry = kids_entry()
    entry.human = {"reviewer": "chandan", "verdict": "reject",
                   "element": "the ending does not work"}
    db.save_bank_entry(entry)
    assert bank_use.approved(entry) is False
    assert bank_use.claim(db, group="kids", language="en",
                          video_format="SHORT", job_id="j") is None


def test_an_approved_entry_is_claimable(db):
    db.save_bank_entry(kids_entry())
    assert bank_use.claim(db, group="kids", language="en",
                          video_format="SHORT", job_id="j") is not None


@pytest.mark.parametrize("verdict", ["approve", "approved", "OK", "yes", ""])
def test_approval_synonyms(verdict):
    """An empty verdict means approve - that was the old default."""
    entry = kids_entry()
    entry.human = {"reviewer": "someone", "verdict": verdict}
    assert bank_use.approved(entry) is True


@pytest.mark.parametrize("verdict", ["reject", "rejected", "no", "hold",
                                     "needs work"])
def test_anything_that_is_not_approval_blocks(verdict):
    entry = kids_entry()
    entry.human = {"reviewer": "someone", "verdict": verdict}
    assert bank_use.approved(entry) is False


def test_a_machine_review_is_recorded_as_a_machine_review(db):
    """A model may author and approve a batch, but the record must say so.

    Writing a model's name into a field called `human` would make the data
    claim a review that never happened - and the reason the field exists is
    YouTube's rule about AI content published "without adding the creator's
    original, authentic insights".
    """
    entry = kids_entry()
    entry.human = {}
    db.save_bank_entry(entry)
    assert bank_import.review(db, entry.entry_id, reviewer="claude-opus-5",
                              kind="machine") is True

    stored = bank.BankEntry.from_dict(
        json.loads(db.bank_entries()[0]["payload"]))
    assert stored.human["kind"] == "machine"
    assert bank_use.approved(stored) is True
    assert bank_use.reviewed_by_human(stored) is False


def test_require_human_skips_a_machine_approved_entry(db):
    """So a group where a person signing off matters can insist on it."""
    entry = kids_entry()
    entry.human = {"reviewer": "claude-opus-5", "verdict": "approve",
                   "kind": "machine"}
    db.save_bank_entry(entry)
    assert bank_use.claim(db, group="kids", language="en",
                          video_format="SHORT", job_id="j",
                          require_human=True) is None


def test_require_human_accepts_a_human_approved_entry(db):
    db.save_bank_entry(kids_entry())          # the fixture records a human
    assert bank_use.claim(db, group="kids", language="en",
                          video_format="SHORT", job_id="j",
                          require_human=True) is not None


def test_an_unrecorded_kind_counts_as_human(db):
    """Entries reviewed before `kind` existed were all reviewed by a person."""
    entry = kids_entry()
    entry.human = {"reviewer": "chandan", "verdict": "approve"}
    assert bank_use.reviewed_by_human(entry) is True


def test_import_and_claim_agree_on_what_approved_means(tmp_path, db):
    """"Importable with --require-review" and "claimable" must not drift."""
    entry = kids_entry()
    entry.human = {"reviewer": "chandan", "verdict": "reject"}
    path = _write(tmp_path, entry)
    report = bank_import.import_file(path, db, expect_group="kids",
                                     require_review=True)
    assert report.stored == 0
    assert any("[review]" in r and "reject" in r for r in report.rejected)


# ---------------------------------------------------------------------------
# Content safety at import, and correcting an entry
# ---------------------------------------------------------------------------
def test_a_knife_in_a_kids_story_is_caught_at_import(tmp_path, db):
    """Found the hard way: it was caught, but after a six-minute render.

    A hand-written kids story said "there was a knife in the kitchen". The
    quality gate blocked it correctly - and by then the entry had been
    consumed from the pool and the video rendered. Text checks belong where a
    rejection costs nothing.
    """
    entry = kids_entry()
    entry.scenes[3].narration = (
        "Then Milo saw the low wall, and there was a knife in the kitchen. "
        "Slow and slow, up we go.")
    entry.recompute()
    hits = bank_import.unsafe(entry)
    assert any(label == "violence" for label, _ in hits), hits

    report = bank_import.import_file(_write(tmp_path, entry), db,
                                     expect_group="kids")
    assert report.stored == 0
    assert any("[safety:violence]" in r for r in report.rejected)


def test_the_kids_list_only_applies_to_kids(tmp_path, db):
    """"knife" in a cooking explainer is a knife.

    The child-directed list is stricter than the universal one on purpose, so
    applying it to every group would reject legitimate adult content.
    """
    entry = kids_entry()
    entry.group = "tech"
    entry.made_for_kids = False
    entry.topic = "pc and laptop tech"
    entry.scenes[3].narration = ("Then Milo used a knife to strip the cable. "
                                 "Slow and slow, up we go.")
    entry.recompute()
    labels = [label for label, _ in bank_import.unsafe(entry)]
    assert "violence" not in labels, labels


def test_captions_are_checked_too(tmp_path):
    """A caption is burned into the picture, so it is on-screen text."""
    entry = kids_entry()
    entry.scenes[2].caption = "यहाँ एक gun रखी थी।"
    entry.recompute()
    assert any(label == "violence"
               for label, _ in bank_import.unsafe(entry))


def test_a_clean_entry_trips_nothing():
    assert bank_import.unsafe(kids_entry()) == []


def test_an_entry_that_keeps_its_id_is_corrected_in_place(tmp_path, db):
    """This is why `stories export` writes the ids out.

    The variety gate skips an entry's own id, and save_bank_entry preserves
    used state - so an edited entry that KEEPS its id updates the same
    record. Correcting a story that has already published therefore cannot
    republish it.
    """
    original = kids_entry()
    bank_import.import_file(_write(tmp_path, original), db,
                            expect_group="kids")
    assert db.claim_bank_entry(group="kids", language="en",
                               video_format="SHORT", job_id="job1") is not None

    fixed = kids_entry()
    fixed.entry_id = original.entry_id          # what export gives you
    fixed.scenes[3].narration = ("Then Milo spotted the low garden wall and "
                                 "climbed it. Slow and slow, up we go.")
    fixed.recompute()
    assert fixed.entry_id == original.entry_id
    assert fixed.content_hash != original.content_hash

    report = bank_import.import_file(_write(tmp_path, fixed), db,
                                     expect_group="kids")
    assert report.stored == 1
    assert len(db.bank_entries()) == 1, "it forked instead of updating"
    assert db.bank_entries()[0]["used_at"] > 0, "lost the used state"


def test_an_authored_file_forks_and_remove_is_the_way_back(tmp_path, db):
    """An authored file carries NO id, so every import derives a fresh one.

    Editing such a file therefore produces a SECOND entry, which the variety
    gate rejects as a near-duplicate of the first - measured at 62% overlap
    while taking a policy violation out of a story. Either export first, or
    retire the original.
    """
    original = kids_entry()
    original.entry_id = ""
    original.recompute()
    bank_import.import_file(_write(tmp_path, original), db,
                            expect_group="kids")

    fixed = kids_entry()
    fixed.entry_id = ""
    fixed.scenes[3].narration = ("Then Milo spotted the low garden wall and "
                                 "climbed it. Slow and slow, up we go.")
    fixed.recompute()
    assert fixed.entry_id != original.entry_id

    blocked = bank_import.import_file(_write(tmp_path, fixed), db,
                                      expect_group="kids")
    assert blocked.stored == 0
    assert any("wording" in r for r in blocked.rejected)

    assert db.delete_bank_entry(original.entry_id) is True
    accepted = bank_import.import_file(_write(tmp_path, fixed), db,
                                       expect_group="kids")
    assert accepted.stored == 1


def test_removing_something_that_is_not_there_says_so(db):
    assert db.delete_bank_entry("no-such-entry") is False


def test_an_entry_id_with_stray_whitespace_still_resolves(db):
    """Ids get pasted from a table and piped between commands.

    On Windows a piped id carries a trailing carriage return, which produced
    a bare "no entry" for an id that plainly existed.
    """
    entry = kids_entry()
    entry.human = {}
    db.save_bank_entry(entry)
    assert bank_import.review(db, f"  {entry.entry_id}\r\n",
                              reviewer="chandan") is True


# ---------------------------------------------------------------------------
# The prompt must ask for something achievable
# ---------------------------------------------------------------------------
def test_every_recommended_batch_size_can_satisfy_its_own_variety_caps():
    """The caps were arithmetically impossible at two reachable lengths.

    The prompt derived them from the share constants alone, so at count=9 it
    said "no arc_variant more than 1 time" with eight arcs available, and at
    count=6 "no outcome_class more than 1 time" with five outcomes. Nine
    scripts cannot be spread one-per-arc across eight arcs. An instruction
    that cannot be obeyed gets ignored, and which of the two conflicting
    instructions is ignored is a guess.

    The floor is now the pigeonhole count, ceil(N/K).
    """
    import re

    arcs, outcomes = len(bank.ARC_VARIANTS), len(bank.OUTCOME_CLASSES)
    for seconds in (45, 50, 60, 90, 120, 150, 200, 300):
        count = bank_prompt.recommended_count(
            group_key="kids", target_seconds=seconds, shape="narrative",
            made_for_kids=True)
        text = bank_prompt.build(
            group_key="kids", language="en", video_format="SHORT",
            target_seconds=seconds, count=count, shape="narrative")
        stated = re.search(r"no arc_variant may appear more than\s+(\d+) "
                           r"times and no outcome_class more than (\d+)",
                           text)
        assert stated, f"{seconds}s: the caps are not in the prompt"
        arc_cap, outcome_cap = int(stated.group(1)), int(stated.group(2))
        assert arc_cap * arcs >= count, \
            f"{seconds}s: {count} scripts cannot fit {arc_cap} per arc"
        assert outcome_cap * outcomes >= count, \
            f"{seconds}s: {count} scripts cannot fit {outcome_cap} per outcome"


def test_the_caps_still_bind_when_the_batch_is_large():
    """The floor must not become a licence to repeat one arc."""
    text = bank_prompt.build(group_key="kids", language="en",
                             video_format="SHORT", target_seconds=45,
                             count=20, shape="narrative")
    assert "more than\n  4 times" in text or "more than 4 times" in text


def test_a_batch_is_only_offered_the_topics_of_its_own_shape():
    """Two instructions that cannot both be satisfied.

    The batch is locked to one shape while the TOPICS block listed the whole
    group and asked for a spread across it. Obeying that writes six alphabet
    drills and labels two of them "kids bedtime stories" - the topic check
    passes, and a bedtime-stories automation later claims a letter-B drill.
    """
    drill = bank_prompt.build(group_key="kids", language="en",
                              video_format="SHORT", target_seconds=45,
                              count=6, shape="drill")
    block = drill.split("TOPICS")[1].split("\n\n")[0]
    assert "alphabet" in block
    assert "bedtime stories" not in block.split("DIFFERENT shape")[0]
    # And the excluded ones are named rather than silently missing, so the
    # operator knows to ask for them with their own --shape.
    assert "DIFFERENT shape" in block
    assert "bedtime stories" in block


def test_every_offered_topic_maps_back_to_the_batch_shape():
    """The real invariant, across every group and shape."""
    for group in ("kids", "finance", "tech"):
        for shape in ("narrative", "poem", "drill", "explainer", "procedure"):
            text = bank_prompt.build(group_key=group, language="en",
                                     video_format="SHORT",
                                     target_seconds=60, count=4,
                                     shape=shape)
            if "Allowed values for" not in text:
                continue
            listed = text.split("Allowed values for \"topic\": ")[1]
            listed = listed.split("\n")[0]
            for topic in [t.strip().strip('"') for t in listed.split('", "')]:
                topic = topic.strip('"')
                assert bank_prompt.shape_for(group, topic) == shape, \
                    f"{group}/{shape} offered {topic!r}"


# ---------------------------------------------------------------------------
# Correcting an entry must not cost its review
# ---------------------------------------------------------------------------
def test_a_correction_keeps_the_review_it_already_had(db):
    """Re-importing to fix anything else wiped every verdict.

    A bank FILE carries no `human` block - the review lives in the payload,
    written by `stories review` - so replacing the payload dropped it, and
    "ready" went from five to nought while the import reported success.
    Measured on the live bank while correcting three character descriptions.
    """
    entry = kids_entry()
    db.save_bank_entry(entry)
    assert bank_import.review(db, entry.entry_id, reviewer="claude-opus-5",
                              kind="machine") is True

    # The same entry as it comes back off disk: no review in the file.
    fresh = bank.BankEntry.from_dict(entry.to_dict())
    fresh.human = {}
    fresh.characters = [{"name": "Milo", "description": "a six-year-old boy, "
                                                       "red shirt, barefoot"}]
    db.save_bank_entry(fresh)

    stored = bank.BankEntry.from_dict(
        json.loads(db.bank_entries()[0]["payload"]))
    assert stored.human.get("reviewer") == "claude-opus-5"
    assert stored.human.get("kind") == "machine"
    assert bank_use.approved(stored) is True


def test_a_rewritten_narration_loses_its_review(db):
    """A verdict on different words is not a verdict on these."""
    entry = kids_entry()
    db.save_bank_entry(entry)
    bank_import.review(db, entry.entry_id, reviewer="chandan")

    rewritten = bank.BankEntry.from_dict(entry.to_dict())
    rewritten.human = {}
    rewritten.scenes[2].narration = ("He climbed onto the crate and the crate "
                                     "tipped over sideways.")
    rewritten.recompute()
    rewritten.entry_id = entry.entry_id          # as `stories export` writes it
    db.save_bank_entry(rewritten)

    stored = bank.BankEntry.from_dict(
        json.loads(db.bank_entries()[0]["payload"]))
    assert stored.human.get("reviewer", "") == ""


def test_a_replacement_is_reported_as_one(tmp_path, db):
    """"1 of 1 stored" over a bank that stayed the same size.

    Storing an entry whose id is already banked is an UPDATE. That is the
    correction workflow working, but it has to be legible: an id that turned
    up twice by accident looked exactly like a successful import.
    """
    entry = kids_entry()
    db.save_bank_entry(entry)

    report = bank_import.import_file(_write(tmp_path, entry), db,
                                     expect_group="kids")
    assert report.stored == 1
    assert report.replaced == [entry.entry_id]
    assert "0 new" in report.summary()
    assert "updates to entries already banked" in report.summary()


def test_the_same_id_twice_in_one_file_warns(tmp_path, db):
    """Across imports it is a correction. Inside one file it is a mistake."""
    first = kids_entry()
    second = kids_entry(name="Asha", refrain="Up and up, we go up",
                        setting="library", domain="lost_item")
    second.entry_id = first.entry_id
    path = tmp_path / "twice.jsonl"
    path.write_text("\n".join(json.dumps(e.to_dict(), ensure_ascii=False)
                              for e in (first, second)) + "\n",
                    encoding="utf-8")

    report = bank_import.import_file(path, db, expect_group="kids")
    assert any("appears twice in this file" in w
               for w in report.warnings), report.warnings


def test_the_reported_bank_size_matches_the_table(tmp_path, db):
    """It said "bank now: 31 entries" over a 26-row table.

    A replaced entry was in the in-memory catalogue twice - once as loaded,
    once as imported - and the stats described that list.
    """
    entry = kids_entry()
    db.save_bank_entry(entry)
    report = bank_import.import_file(_write(tmp_path, entry), db,
                                     expect_group="kids")
    assert report.stats["total"] == len(db.bank_entries()) == 1


# ---------------------------------------------------------------------------
# What reaches the image generator has to be readable by it
# ---------------------------------------------------------------------------
def test_a_devanagari_character_description_is_rejected(tmp_path, db):
    """It became "<name> is <Devanagari>" in every image prompt.

    SDXL's text encoder cannot read Devanagari, so the clause was noise -
    and the cast the bible exists to keep consistent was drawn differently in
    every frame. Found in three banked Hindi entries.
    """
    entry = kids_entry()
    entry.language = "hi"
    for index, scene in enumerate(entry.scenes):
        scene.caption = f"An English caption for scene {index}."
        scene.narration = "मीरा ने छत की ओर देखा और रुक गई।"
    entry.characters = [{"name": "मीरा",
                         "description": "छह साल की लड़की, दो चोटियाँ"}]
    entry.recompute()

    report = bank_import.import_file(_write(tmp_path, entry), db,
                                     expect_group="kids")
    assert report.stored == 0
    assert any("characters[0].description" in r and "ENGLISH" in r
               for r in report.rejected), report.rejected


def test_a_devanagari_name_with_an_english_description_is_fine():
    """The NAME follows the narration - that is what makes it mask."""
    entry = kids_entry()
    entry.characters = [{"name": "मीरा",
                         "description": "six-year-old girl, two braids, "
                                        "green frock"}]
    problems = [str(p) for p in bank.validate(entry, expect_group="kids")
                if "characters" in str(p)]
    assert problems == []


def test_the_bible_leaves_out_a_description_it_cannot_use():
    """For the entries banked before the import check existed."""
    entry = kids_entry()
    entry.characters = [
        {"name": "मीरा", "description": "छह साल की लड़की, दो चोटियाँ"},
        {"name": "Dadi", "description": "elderly woman, white saree"}]
    bible = bank_use.bible_for(entry)
    assert bible is not None
    assert [c.name for c in bible.characters] == ["Dadi"]


def test_a_cast_with_nothing_usable_gives_no_bible():
    entry = kids_entry()
    entry.characters = [{"name": "मीरा",
                         "description": "छह साल की लड़की, दो चोटियाँ"}]
    assert bank_use.bible_for(entry) is None


# ---------------------------------------------------------------------------
# The share caps have to be able to stop something
# ---------------------------------------------------------------------------
_OBJECTS = ["lantern", "whistle", "marble", "ribbon", "pebble", "spoon",
            "feather", "button", "bottle", "ladder", "basket", "candle"]
_PLACES = ["kitchen", "terrace", "balcony", "courtyard", "verandah",
           "doorway", "stairwell", "workshop", "garden", "attic", "shed",
           "porch"]


def _varied(index: int, *, arc: str = "alone",
            outcome: str = "got_it") -> bank.BankEntry:
    """An entry that differs from its siblings on everything but the arc.

    Written so the earlier gates - refrain, axes, wording - cannot be what
    rejects it. Only the share cap can.
    """
    thing, place = _OBJECTS[index], _PLACES[index]
    entry = kids_entry(name=f"Child{index}",
                       refrain=f"The {thing} waits, the {thing} waits",
                       setting=place, domain=f"domain_{index}")
    entry.arc_variant = arc
    entry.outcome_class = outcome
    entry.protagonist_type = f"child_{index}"
    entry.emotional_register = f"register_{index}"
    for number, scene in enumerate(entry.scenes):
        scene.narration = (
            f"Child{index} left the {thing} on the {place} and it rolled "
            f"under the {_OBJECTS[(index + number) % len(_OBJECTS)]}. "
            f"Nobody in the {place} noticed it there for {number + 2} whole "
            f"days, which is how the {thing} came to matter at all.")
        scene.caption = f"बच्चा {index} और {thing} की कहानी, दृश्य {number}।"
    entry.characters = [{"name": f"Child{index}",
                         "description": f"child of {index + 5} years, "
                                        f"short hair, plain shirt"}]
    entry.recompute()
    entry.entry_id = ""
    entry.recompute()
    return entry


def test_one_arc_cannot_own_a_group(db):
    """The cap was a WARNING, so the check that looks at the shape of the
    whole channel - the thing a reviewer would read as mass production -
    could not stop anything, while the batch prompt said it was enforced.
    """
    peers = [_varied(i) for i in range(11)]
    issues = variety.check_new(_varied(11), peers)
    fatal = [i for i in issues if i.fatal and i.kind == "arc_share"]
    assert fatal, [str(i) for i in issues]


def test_below_the_floor_a_share_cap_measures_nothing(db):
    """One script out of five is 20% whatever it says."""
    peers = [_varied(i) for i in range(4)]
    issues = variety.check_new(_varied(4), peers)
    assert not [i for i in issues if i.kind in ("arc_share", "outcome_share")]


def test_a_recurring_name_is_a_series_not_mass_production(db):
    """Still a warning: at the floor of ten, a second appearance is 20%."""
    peers = [_varied(i, arc=("alone" if i % 2 else "noticed")) for i in
             range(11)]
    for peer in peers[:2]:
        peer.characters = [{"name": "Milo", "description": "a small boy"}]
    candidate = _varied(11, arc="noticed")
    candidate.characters = [{"name": "Milo", "description": "a small boy"}]
    issues = variety.check_new(candidate, peers)
    shares = [i for i in issues if i.kind == "name_share"]
    assert shares and not any(i.fatal for i in shares)


def test_the_signature_cache_does_not_change_a_verdict(db):
    """Peer signatures are now computed once per process, not per pair."""
    peers = [_varied(i) for i in range(6)]
    candidate = _varied(7)
    first = [str(i) for i in variety.check_new(candidate, peers)]
    again = [str(i) for i in variety.check_new(candidate, peers)]
    assert first == again

    # A corrected peer must re-sign rather than reuse the signature of the
    # text it replaced - the cache is keyed on the content hash for this.
    peers[0].scenes[1].narration = candidate.scenes[1].narration
    peers[0].recompute()
    third = [str(i) for i in variety.check_new(candidate, peers)]
    assert third != first


# ---------------------------------------------------------------------------
# The declared language has to be true of the narration
# ---------------------------------------------------------------------------
def _relabelled(language: str) -> bank.BankEntry:
    """The same narration, declared as another language.

    The id is cleared first because that is how an AUTHORED file arrives -
    it states no entry_id, so one is derived from group, language and the
    narration hash, and the relabelled copy therefore gets a different id
    from the original. Which is what lets it be stored beside it.
    """
    entry = kids_entry()
    entry.language = language
    entry.entry_id = ""
    entry.recompute()
    return entry


def test_an_english_narration_cannot_be_labelled_hindi(db):
    """It was never checked, and two things came through the gap.

    An honest mislabel sends the script to the wrong voice. A deliberate one
    is a way past the variety gate: peers are matched on group AND language,
    so relabelling this field leaves an otherwise identical entry with no
    peers to be compared against, and the byte-identical check never runs.
    """
    original = kids_entry()
    db.save_bank_entry(original)

    relabelled = _relabelled("hi")
    assert relabelled.entry_id != original.entry_id      # the id carries it
    assert variety.check_new(relabelled, [original]) == []   # no peers

    problems = [str(p) for p in bank.validate(relabelled, expect_group="kids")]
    assert any("language" in p and "not in Devanagari" in p
               for p in problems), problems


def test_a_devanagari_narration_cannot_be_labelled_english():
    entry = kids_entry()
    entry.language = "en"
    for scene in entry.scenes:
        scene.narration = "मीरा ने छत की ओर देखा और वहीं रुक गई।"
        scene.caption = "Meera looked towards the roof and stopped there."
    entry.recompute()
    problems = [str(p) for p in bank.validate(entry, expect_group="kids")]
    assert any("language" in p and "is in Devanagari" in p
               for p in problems), problems


def test_hinglish_must_be_written_in_latin_letters():
    """"hi-Latn" is Hindi in LATIN letters - that is the whole distinction."""
    entry = kids_entry()
    entry.language = "hi-latn"
    for scene in entry.scenes:
        scene.narration = "मीरा ने छत की ओर देखा और वहीं रुक गई।"
        scene.caption = "Meera looked towards the roof."
    entry.recompute()
    problems = [str(p) for p in bank.validate(entry, expect_group="kids")]
    assert any("language" in p and "hi-Latn" in p for p in problems), problems


def test_a_loanword_does_not_make_a_hindi_entry_english():
    """A majority test, so the odd English word is still allowed."""
    entry = kids_entry()
    entry.language = "hi"
    for scene in entry.scenes:
        scene.narration = ("मीरा की school bag छत पर रह गई और वह वापस "
                           "सीढ़ियाँ चढ़ने लगी।")
        scene.caption = "Meera left her school bag on the roof."
    entry.recompute()
    problems = [str(p) for p in bank.validate(entry, expect_group="kids")
                if "language" in str(p)]
    assert problems == []


def test_every_banked_file_declares_its_language_truthfully():
    """The rule has to hold for the real catalogue, not just a fixture."""
    from pathlib import Path

    for path in sorted(Path("banks").glob("*.jsonl")):
        entries, problems = bank.load_jsonl(path)
        assert not [p for p in problems if p.fatal], \
            f"{path.name}: {[str(p) for p in problems]}"
        for entry in entries:
            wrong = [str(p) for p in bank.validate(entry)
                     if "language" in str(p)]
            assert wrong == [], f"{path.name} / {entry.entry_id}: {wrong}"


# ---------------------------------------------------------------------------
# Title patterns from the niche, for the batch prompt
# ---------------------------------------------------------------------------
class _FakeVideo:
    def __init__(self, title, velocity, views):
        self.title, self.view_velocity, self.views = title, velocity, views


class _FakeResearch:
    """Stands in for YouTubeResearch. `configured` is a PROPERTY there."""
    corpus: list = []
    ready = True

    def __init__(self, cfg, db=None):
        pass

    @property
    def configured(self):
        return type(self).ready

    def research_channels(self, niche, profile, **kwargs):
        return list(type(self).corpus)

    def research(self, niche, profile, **kwargs):   # pragma: no cover
        return []


@pytest.fixture()
def fake_research(monkeypatch):
    from engine.research import youtube

    monkeypatch.setattr(youtube, "YouTubeResearch", _FakeResearch)
    _FakeResearch.ready = True
    _FakeResearch.corpus = []
    return _FakeResearch


def test_title_patterns_are_ranked_by_views_per_day(db, fake_research):
    """`build` has taken `viral_titles` since the bank existed and nothing
    ever supplied one, so the batch prompt never saw what performs.

    Ranked on velocity, not lifetime views: a five-year-old video with a big
    total should not crowd out what is working now.
    """
    fake_research.corpus = [
        _FakeVideo("Old But Huge Bedtime Story", 10, 9_000_000),
        _FakeVideo("Working Right Now", 5_000, 100_000),
        _FakeVideo("Second Best", 900, 50_000)]
    titles = bank_prompt.viral_titles_for(load_config(), db, group_key="kids")
    assert titles == ["Working Right Now", "Second Best",
                      "Old But Huge Bedtime Story"]


def test_a_repeated_title_is_listed_once(db, fake_research):
    fake_research.corpus = [_FakeVideo("The Same Title", 10, 10),
                            _FakeVideo("The Same Title", 9, 9),
                            _FakeVideo("", 8, 8)]
    assert bank_prompt.viral_titles_for(load_config(), db,
                                        group_key="kids") == ["The Same Title"]


def test_no_key_means_no_patterns_and_no_error(db, fake_research):
    fake_research.ready = False
    fake_research.corpus = [_FakeVideo("Never Reached", 10, 10)]
    assert bank_prompt.viral_titles_for(load_config(), db,
                                        group_key="kids") == []


def test_research_failing_does_not_cost_the_prompt(db, monkeypatch):
    """A prompt without the shape hints is still a usable prompt."""
    from engine.research import youtube

    class _Broken(_FakeResearch):
        def research_channels(self, niche, profile, **kwargs):
            raise RuntimeError("quota exceeded")

    monkeypatch.setattr(youtube, "YouTubeResearch", _Broken)
    assert bank_prompt.viral_titles_for(load_config(), db,
                                        group_key="kids") == []


def test_an_unknown_group_asks_for_nothing(db, fake_research):
    fake_research.corpus = [_FakeVideo("Never Reached", 10, 10)]
    assert bank_prompt.viral_titles_for(load_config(), db,
                                        group_key="dog grooming") == []

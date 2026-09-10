"""Turn a banked entry into the objects the pipeline already renders.

WHAT THIS FIXES. Three complaints, all with the same root cause - the script,
the captions and the image prompts were each produced by a separate LLM call
that could not see the others:

  "image prompt doesn't match video script"   the visual prompt was written
      from the narration by a later call, so it drifted.
  "captions aren't correct"                   Hindi captions were machine
      translations made at render time from English narration.
  "scripts are lacking engagement"            a 4k-token single shot written
      in seconds, gated but never curated.

A banked entry carries narration, its own caption in the other language, and
its own image brief PER SCENE, all authored together and all read by a human
before import. So consuming one does not mean "use a cached script" - it means
skip the three calls that were introducing the drift.

WHAT THIS DELIBERATELY DOES NOT DO. It does not run `auto_improve`. That pass
rewrites narration to hit a retention target, and rewriting narration silently
invalidates the authored caption and the authored image brief for that scene -
which would reintroduce the exact mismatch this module exists to remove. The
retention score is still measured, because a low score on a banked entry is
worth knowing; it just no longer edits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..core.logging import log_event
from ..core.models import ContentIdea, Scene, Script
from .bank import BankEntry

# Beat name -> the pipeline's five scene roles. Retention analysis, motion
# assignment and the caption style all read `role`, so a banked scene has to
# declare one; without this every scene arrives as "value" and the hook gets
# no hook treatment.
_ROLE_FOR_BEAT: dict[str, str] = {
    # narrative
    "want": "hook", "attempt": "value", "obstacle": "value",
    "turn": "value", "resolve": "payoff", "refrain": "cta",
    # poem
    "open": "hook", "verse_a": "value", "verse_b": "value",
    "verse_c": "value", "refrain_2": "value", "close": "cta",
    # drill
    "item_intro": "context", "model": "value", "call": "value",
    "response": "value", "vary": "value", "check": "value",
    "recap": "payoff",
    # explainer / procedure
    "hook": "hook", "context": "context", "promise": "context",
    "mechanism": "value", "worked_example": "value", "boundary": "value",
    "payoff": "payoff", "cta": "cta", "why": "context",
    "prepare": "context", "steps": "value", "verify": "payoff",
}


@dataclass
class BankClaim:
    """An entry taken out of the pool for one job."""
    entry: BankEntry
    entry_id: str
    released: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"entry_id": self.entry_id, "title": self.entry.title,
                "shape": self.entry.shape, "group": self.entry.group,
                "estimated_seconds": round(self.entry.estimated_seconds, 1),
                "scene_count": self.entry.scene_count,
                "reviewer": (self.entry.human or {}).get("reviewer", "")}


def approved(entry: BankEntry) -> bool:
    """Has this entry been reviewed AND approved?

    Reviewed and REJECTED is not the same as unreviewed, and the difference
    was being lost: the old test was `human.reviewer` being non-empty, so
    recording "reviewer: chandan, verdict: reject" made an entry MORE
    claimable than leaving it alone. A rejection is the one verdict that has
    to be load-bearing.
    """
    review = entry.human or {}
    if not str(review.get("reviewer", "")).strip():
        return False
    # A blank or absent verdict is an approval, not a rejection: it means
    # somebody was recorded as having read it and did not object. Only an
    # explicit rejection blocks. Failing closed on blank would look safer and
    # would in fact just make hand-edited records mysteriously unclaimable,
    # since `review()` always writes a verdict.
    verdict = str(review.get("verdict", "") or "approve").strip().lower()
    return verdict in ("approve", "approved", "ok", "yes")


def reviewed_by_human(entry: BankEntry) -> bool:
    """True only when a PERSON approved it, not a model.

    Scripts can be authored and approved by a model - which is a legitimate
    way to fill the bank, and what the automated batch does - but the record
    has to say so. Writing a model's name into a field called `human` would
    make the data claim a review that never happened, and the whole reason
    the field exists is YouTube's rule about AI content published "without
    adding the creator's original, authentic insights".
    """
    return (approved(entry)
            and str((entry.human or {}).get("kind", "human")).lower() == "human")


def claim(db, *, group: str, language: str, video_format: str, job_id: str,
          topics: Sequence[str] = (), near_seconds: float = 0.0,
          require_review: bool = True,
          require_human: bool = False) -> BankClaim | None:
    """Take the next unused entry for this group, or None.

    `require_review` skips entries nobody has approved. On by default: the
    whole argument for a bank over live generation is that somebody read it,
    and an unapproved entry has none of that benefit while having all of the
    permanence.

    `require_human` narrows that to entries a PERSON approved. Off by default
    because a model-authored, model-approved batch is a legitimate way to
    fill the bank; turn it on (config `bank.require_human_review`) for
    anything where a person signing off actually matters.
    """
    import json

    # DECIDE FIRST, CLAIM SECOND.
    #
    # This used to claim a row and THEN test it, moving on when the test
    # failed - which left every rejected entry marked used. Measured: three
    # unapproved entries in the bank, one claim() call, all three consumed
    # and nothing rendered. And `save_bank_entry` preserves used state, so
    # reviewing them afterwards could not bring them back. A single render
    # destroyed the pool, while the CLI cheerfully said they "will not be
    # claimed for rendering until reviewed".
    candidates = db.bank_candidates(
        group=group, language=language, video_format=video_format,
        topics=topics, near_seconds=near_seconds)
    if not candidates and near_seconds > 0:
        # The duration filter is a preference, not a requirement: a bank of
        # 30-second stories should still serve a 45-second request, since the
        # entry's own length is what gets used anyway.
        candidates = db.bank_candidates(
            group=group, language=language, video_format=video_format,
            topics=topics, near_seconds=0.0)

    skipped: dict[str, int] = {}

    def note(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for row in candidates:
        try:
            entry = BankEntry.from_dict(json.loads(row["payload"]))
        except Exception as exc:                    # noqa: BLE001
            log_event("BANK", "stored entry will not parse; skipping it",
                      entry=row["entry_id"], error=str(exc)[:120])
            note("unparseable")
            continue
        if require_review and not approved(entry):
            note("not_approved")
            continue
        if require_human and not reviewed_by_human(entry):
            note("model_approved_only")
            continue

        # Only now is anything marked used, and only this one entry.
        if db.claim_specific_bank_entry(entry.entry_id, job_id) is None:
            # Another job took it between the read and here. Not an error and
            # not a reason to stop - try the next candidate.
            note("taken_by_another_job")
            continue
        log_event("BANK", "entry claimed", entry=entry.entry_id,
                  title=entry.title[:60],
                  seconds=f"{entry.estimated_seconds:.0f}",
                  scenes=entry.scene_count)
        return BankClaim(entry=entry, entry_id=entry.entry_id)

    if candidates:
        log_event("BANK", "no claimable entry; nothing was consumed",
                  candidates=len(candidates), **skipped)
    return None


def release(db, claim_: BankClaim | None, *, reason: str = "") -> None:
    """Return an entry to the pool after a failed render.

    The script was fine; ffmpeg, the TTS provider or the network was not.
    Burning a curated entry on a transient failure is pure loss, and unlike a
    live script it cannot simply be regenerated.
    """
    if claim_ is None or claim_.released:
        return
    db.release_bank_entry(claim_.entry_id)
    claim_.released = True
    log_event("BANK", "entry released back to the pool",
              entry=claim_.entry_id, reason=reason[:120])


def to_idea(entry: BankEntry) -> ContentIdea:
    """The ContentIdea a banked entry stands in for.

    The pipeline threads an idea through to metadata, thumbnails and the
    learner, so an entry has to present as one rather than being special-cased
    at every downstream stop.
    """
    return ContentIdea(
        topic=entry.topic or entry.title,
        angle=entry.arc_variant or entry.shape,
        working_title=entry.title,
        hook_concept=(entry.scenes[0].narration if entry.scenes else ""),
        hook_type="story" if entry.shape in ("narrative", "poem") else "myth",
        why_now="evergreen" if entry.volatility == "evergreen" else entry.volatility,
        # 0 rather than a flattering number. The score is an estimate of an
        # unknown opportunity, and a banked entry has not been scored against
        # live research, so claiming a value here would be inventing one.
        opportunity_score=0.0,
        source_topic_cluster=f"bank:{entry.group}",
        originality_note=(f"curated bank entry {entry.entry_id}, "
                          f"reviewed by "
                          f"{(entry.human or {}).get('reviewer') or 'nobody'}"),
    )


def to_script(entry: BankEntry, *, language: str = "",
              caption_language: str = "") -> Script:
    """Build the Script, carrying the authored captions and image briefs.

    `caption_language` is what the request asked for. When it does not match
    the language the entry's captions were written in, the captions are left
    empty so the normal translation path fills them - authored captions in the
    wrong language are worse than none.
    """
    entry_caption_language = "en" if entry.language.startswith("hi") else "hi"
    wanted = (caption_language or "").strip().lower()[:2]
    use_authored = (not wanted) or wanted == entry_caption_language
    if wanted and not use_authored:
        log_event("BANK", "authored captions are in the wrong language for "
                          "this request; falling back to translation",
                  entry=entry.entry_id, authored=entry_caption_language,
                  requested=wanted)

    scenes: list[Scene] = []
    for i, banked in enumerate(entry.scenes):
        scenes.append(Scene(
            index=i,
            narration=banked.narration,
            # The image brief IS the visual prompt. No later LLM call rewrites
            # it, which is the whole point.
            visual_prompt=banked.image_brief,
            on_screen_text=banked.on_screen_text,
            role=_ROLE_FOR_BEAT.get(banked.beat, "value"),
            caption_text=banked.caption if use_authored else "",
        ))

    return Script(
        idea_id="",
        title_ideas=[entry.title, *entry.title_alts],
        hook=(entry.scenes[0].narration if entry.scenes else ""),
        script="\n".join(s.narration for s in scenes),
        scenes=[s.to_dict() for s in scenes],
        visual_plan=[s.visual_prompt for s in scenes],
        voice_style="calm" if entry.made_for_kids else "energetic",
        cta=(entry.scenes[-1].narration if entry.scenes else ""),
        estimated_duration=round(entry.estimated_seconds, 2),
        language=language or entry.language,
        # Declared numbers, so the fact checker can tell an illustrative
        # figure from an unverified assertion. Without them a finance entry
        # is flagged medium-risk on every one of its own figures.
        claims=[dict(c) for c in (entry.claims or [])],
        provider=f"bank:{entry.entry_id}",
        chapters=_chapters(entry),
    )


def _chapters(entry: BankEntry) -> list[dict[str, Any]]:
    """Section boundaries, for real YouTube chapters.

    Only where the beats are sections - several consecutive scenes sharing a
    beat name. One scene per beat is not a chapter list, it is a spoiler.
    """
    if entry.scene_count <= len(set(s.beat for s in entry.scenes)) + 2:
        return []
    out: list[dict[str, Any]] = []
    seen = ""
    for i, scene in enumerate(entry.scenes):
        if scene.beat and scene.beat != seen:
            out.append({"heading": scene.beat.replace("_", " ").title(),
                        "scene_index": i})
            seen = scene.beat
    return out if len(out) >= 2 else []


def bible_for(entry: BankEntry):
    """The cast as a CharacterBible, without an LLM call.

    The live path asks a model to read the narration and infer who is in it.
    A banked entry declares its cast, so that call is both unnecessary and
    strictly worse - inference can miss a character the author named.
    """
    from .characters import Character, CharacterBible

    people = [Character(name=str(c.get("name", "")).strip(),
                        description=str(c.get("description", "")).strip())
              for c in (entry.characters or [])
              if str(c.get("name", "")).strip()]
    return CharacterBible(characters=people) if people else None


def has_authored_captions(entry: BankEntry) -> bool:
    """True when every scene carries a caption.

    All-or-nothing on purpose: a video where some captions are authored and
    the rest are machine-translated reads as inconsistent, which is worse
    than either alone.
    """
    return bool(entry.scenes) and all(
        (s.caption or "").strip() for s in entry.scenes)

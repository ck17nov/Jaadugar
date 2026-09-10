"""Title, description, tags and chapters (spec sections 17 & 18).

Titles are generated then SCORED, and the misleading-risk term is subtractive:
a title that promises something the script does not contain loses points no
matter how clickable it is.  `TitleScore` is reported out of 100.
"""
from __future__ import annotations

import re
from typing import Any

from ..core.config import Config
from ..core.logging import log_event
from ..core.models import ContentIdea, Script, VideoMetadata
from ..core.niche import NicheProfile
from ..core.util import (STOPWORDS, clamp, keywords, sentences,
                         token_overlap, truncate, words)

YOUTUBE_TITLE_LIMIT = 100
YOUTUBE_DESC_LIMIT = 5000
YOUTUBE_TAGS_TOTAL_CHARS = 460          # 500 with separators; stay safely under
# A 40-minute video legitimately has more than a dozen sections. YouTube has
# no documented chapter ceiling; 30 keeps the description readable.
MAX_CHAPTERS = 30

def _visible_title(text: str) -> str:
    """The title minus its furniture: emoji, gloss and hashtag tail.

    Length and word count are measures of what a viewer READS. A leading
    emoji, a trailing " | English gloss" on a Devanagari title and a
    "#shorts #kahani" tail are all conventions the best performers in this
    niche use - and counting them as words made every title that followed
    the convention score worse than a bare four-word plot label.
    """
    import re as _re

    out = (text or "").strip()
    out = _re.sub(r"(?:\s*#[^\s#]+)+\s*$", "", out)      # hashtag tail
    out = _re.split(r"\s+\|\s+", out)[0]                  # " | gloss"
    # One leading emoji or symbol, plus the space after it.
    out = _re.sub(r"^[^\wऀ-ॿ(\[\"']+\s*", "", out)
    return out.strip()


CURIOSITY_WORDS = {
    "why", "how", "what", "who", "actually", "really", "hidden", "secret",
    "nobody", "strange", "stranger", "unexpected", "wrong", "mistake", "myth",
    "found", "discovered", "revealed", "until", "before", "almost", "never",
    # Devanagari. Without these, 22 of the 100 points were UNREACHABLE for a
    # Hindi title however good it was - curiosity is weighted 0.20 and
    # emotional pull 0.10, and neither set had a single Devanagari word in
    # it. A Hindi title asking "अब वो क्या करेगा?" scored zero curiosity.
    "क्या", "क्यों",
    "कैसे", "कहाँ",
    "कौन", "कब",
    "आख़िर", "असल",
    "छिपा", "छिपी",
    "राज़", "रहस्य",
    "ग़लती", "कभी",
    "फिर",
}
# What a human-reviewed authored title is worth, on the 0-1 scale before the
# x100. Enough to lift a specific, concrete title over a formulaic one of
# similar mechanical quality, not enough to save a bad one.
AUTHORED_BONUS = 0.12

EMOTION_WORDS = {
    "shocking", "incredible", "terrifying", "beautiful", "brutal", "insane",
    "amazing", "unbelievable", "wild", "crazy", "stunning", "haunting",
    "dangerous", "impossible", "extraordinary",
    # Devanagari, for the same reason.
    "डर", "डरा", "ख़ुश",
    "रो", "रोया", "हँस",
    "प्यार", "अकेला",
    "चुप", "हिम्मत",
    "मज़ा", "अजीब",
}
# Overpromises we refuse to ship (spec section 17).
MISLEADING_PATTERNS = [
    (r"\b(aliens?|ufo)\b", 0.35, "unverifiable claim"),
    (r"\b(proof|proves|confirmed)\b", 0.18, "overstated certainty"),
    (r"\b(cure|cures|miracle)\b", 0.35, "health overpromise"),
    (r"\b(guaranteed|100%|risk[- ]free)\b", 0.30, "guarantee language"),
    (r"\b(you won'?t believe|will shock you|gone wrong)\b", 0.28, "empty clickbait"),
    (r"[!]{2,}", 0.20, "punctuation shouting"),
    (r"\b[A-Z]{6,}\b", 0.14, "all-caps shouting"),
    (r"\b(get rich|make \$?\d+|double your money)\b", 0.35, "financial overpromise"),
]

SYSTEM_PROMPT = """You write YouTube titles that are clickable AND accurate.

Rules:
- The title must be delivered by the script. No promise the video cannot keep.
- No ALL CAPS words, no multiple exclamation marks, no "you won't believe".
- 40-70 characters is the sweet spot; hard limit 100.
- Specific beats vague: a number, a name or a concrete noun.
- Output valid JSON only."""


# Hook-type / analysis prefixes the idea engine attaches internally. They are
# useful for scoring and must never reach a viewer.
_ANALYSIS_PREFIX = re.compile(
    r"^\s*(consequence|correction|hidden|reveal|contradiction|mechanism|"
    r"question|story|number|comparison|myth|gap|angle)\s*[:\-]\s*", re.I)

_NOT_PUBLISHABLE = re.compile(
    r"(gap score|momentum|cluster|saturated|\bn=|derived from|"
    r"without an LLM|structural|\b\d+\s+(angles?|videos?|samples?)\b)", re.I)


def _publishable_angle(text: str) -> str:
    """Return `text` fit for a public description, or "".

    Strips the internal hook-type prefix and rejects anything that reads like a
    metric or an internal note rather than a sentence.
    """
    cleaned = _ANALYSIS_PREFIX.sub("", (text or "").strip()).strip()
    if not cleaned or len(cleaned.split()) < 4:
        return ""
    if _NOT_PUBLISHABLE.search(cleaned):
        return ""
    return cleaned[0].upper() + cleaned[1:]



def title_patterns(research: list | None, *, limit: int = 12) -> str:
    """A prompt block of real, high-performing titles from this niche.

    Sorted by views and capped, because the point is the SHAPE of what works -
    how long, whether it asks a question, whether it names a character, where
    the hook sits - and twelve examples show that while fifty become noise the
    model paraphrases.

    The originality boundary is stated in the prompt rather than assumed. This
    project has a hard rule against reproducing anyone's content, and "here
    are competitor titles" is exactly the input that invites it, so the
    instruction says in words that the structure may be learned and the
    subject may not.
    """
    if not research:
        return ""
    rows = []
    for video in research:
        title = (getattr(video, "title", "") or "").strip()
        views = int(getattr(video, "views", 0) or 0)
        if title:
            rows.append((views, title))
    if not rows:
        return ""
    rows.sort(reverse=True)
    seen: set[str] = set()
    picked: list[tuple[int, str]] = []
    for views, title in rows:
        key = title.lower()[:40]
        if key in seen:
            continue
        seen.add(key)
        picked.append((views, title))
        if len(picked) >= limit:
            break
    listing = "\n".join(f"  - {t}  ({v:,} views)" for v, t in picked)
    return f"""
WHAT ALREADY WORKS IN THIS NICHE - these are real titles from other
channels, with their view counts, sorted by performance:
{listing}

Learn the SHAPE from them: typical length, whether they ask a question or
make a promise, whether they name a character, where the strongest word
sits, what punctuation and emoji they use, and what they leave out.

Do NOT copy any of them, do not rewrite one with a word swapped, and do not
write a title for THEIR video. The title you write is for OUR video, about
OUR topic, and it must be true of it. Borrow the pattern, never the content.
"""


def _lead_narration(script: Script) -> str:
    """The narration with a disclaimer opener dropped.

    `has_disclaimer` answers precisely "does scene 1 open with one", which is
    what makes this safe: a video that merely MENTIONS "not financial advice"
    in its closing line keeps every scene.
    """
    from .disclaimer import has_disclaimer

    scenes = script.scene_objects()
    if len(scenes) > 1 and has_disclaimer(script):
        return " ".join(s.narration for s in scenes[1:])
    return script.script


class MetadataGenerator:
    def __init__(self, cfg: Config, router=None):
        self.cfg = cfg
        self.router = router

    # ------------------------------------------------------------------
    def build(self, script: Script, idea: ContentIdea, profile: NicheProfile,
              *, video_format: str = "SHORT", language: str = "en",
              made_for_kids: bool = False,
              synthetic_disclosure: bool = True,
              hashtags: bool = True,
              research: list | None = None) -> VideoMetadata:
        candidates = self._title_candidates(script, idea, profile,
                                            research=research)
        # Titles that arrived WITH the script, in the order the author put
        # them - the first is their preferred one. Only for a banked script:
        # a live-generated script's title_ideas came from the same model that
        # is about to generate more, so there is nothing human about them.
        authored = set()
        if (script.provider or "").startswith("bank:"):
            authored = {t.strip().lower()
                        for t in (script.title_ideas or []) if t.strip()}
        # Child-directed STORIES are judged on withholding: a bedtime story
        # is chosen from a promise, not found by matching its own words.
        # Explainers are the opposite and keep the search weighting.
        # A story has beats and a payoff to protect; a drill or a list does
        # not. `story_report` is only filled for narrative shapes, which is
        # exactly the distinction, and a kids profile without one (an
        # alphabet drill) keeps the ordinary search weighting.
        withhold = bool(getattr(profile, "made_for_kids", False)
                        and len(script.scene_objects()) >= 3)
        scored = [self.score_title(t, script, idea,
                                   authored=t.strip().lower() in authored,
                                   withhold=withhold)
                  for t in candidates]
        scored.sort(key=lambda c: c["score"], reverse=True)
        best = scored[0] if scored else {"title": idea.working_title, "score": 50.0}

        meta = VideoMetadata(
            title=truncate(best["title"], YOUTUBE_TITLE_LIMIT),
            title_score=round(best["score"], 1),
            title_candidates=scored[:10],
            category_id=str(self.cfg.get("youtube.default_category_id", "27")),
            privacy=str(self.cfg.get("youtube.default_privacy", "private")),
            made_for_kids=made_for_kids,
            synthetic_disclosure=synthetic_disclosure,
            language=language,
        )
        meta.tags = self.build_tags(script, idea, profile)
        if video_format == "LONGFORM":
            meta.chapters = self.build_chapters(script)
        meta.description = self.build_description(
            script, idea, profile, meta, video_format=video_format,
            hashtags=hashtags)
        log_event("METADATA", "metadata built",
                  title_score=f"{meta.title_score:.0f}/100",
                  tags=len(meta.tags), desc_chars=len(meta.description))
        return meta

    # ------------------------------------------------------------------
    def _title_candidates(self, script: Script, idea: ContentIdea,
                          profile: NicheProfile,
                          research: list | None = None) -> list[str]:
        """10 candidates: LLM-generated where available, plus structural ones."""
        out: list[str] = []
        out += [t for t in (script.title_ideas or []) if t.strip()]
        if idea.working_title:
            out.append(idea.working_title)

        if self.router is not None and len(out) < 10:
            try:
                data, _ = self.router.complete_json(
                    self._title_prompt(script, idea, profile, research),
                    system=SYSTEM_PROMPT, temperature=0.9, max_tokens=1024)
                out += [str(t).strip() for t in (data.get("titles") or [])
                        if str(t).strip()]
            except Exception as exc:
                log_event("METADATA", "LLM titles unavailable", 
                          error=str(exc)[:140])

        # Structural titles are ENGLISH-ONLY, so they must never be attached to
        # a video in another language. A Hindi Short titled "How Account
        # Changes Kids" is worse than a plain one: it is wrong AND in the wrong
        # language, and YouTube shows the title before anything else.
        language = (getattr(script, "language", "") or "en").lower()
        if out or language.startswith("en"):
            out += self._structural_titles(script, idea)
        else:
            log_event("METADATA", "no model title for a non-English video",
                      language=language,
                      note="structural titles are English-only; using the "
                           "script's own hook instead")
            hook = (getattr(script, "hook", "") or "").strip()
            if hook:
                out.append(hook[:100])

        # De-duplicate case-insensitively, keep order, cap at 10.
        seen: set[str] = set()
        unique: list[str] = []
        for t in out:
            t = re.sub(r"\s+", " ", t).strip(" .")
            key = t.lower()
            if t and key not in seen and len(t) <= YOUTUBE_TITLE_LIMIT:
                seen.add(key)
                unique.append(t)
        return unique[:10]

    def _title_prompt(self, script: Script, idea: ContentIdea,
                      profile: NicheProfile,
                      research: list | None = None) -> str:
        language = (getattr(script, "language", "") or "en")
        from .translate import language_name
        patterns = title_patterns(research)
        return f"""Write 10 title options for this video.
{patterns}

LANGUAGE: write every title in {language_name(language)} ({language}), in that
language's own script. The title is the first thing a viewer sees, so it must
be in the language they are about to hear.

NICHE: {profile.name}   AUDIENCE: {profile.audience}
TOPIC: {idea.topic}
ANGLE: {idea.angle}
HOOK (first spoken line): {script.hook}

FULL NARRATION:
{truncate(script.script, 1400)}

RULES, and the first one matters most:
- BE SPECIFIC. Name the actual thing the video is about - the object, the
  number, the place, the person. A title made of abstractions could belong to
  any video and tells a viewer nothing. "How Account Changes Kids" is the
  failure mode: three vague words and no subject.
- Every title must be supported by the narration above. Do not promise
  anything the video does not deliver.
- Under 70 characters, so it is not cut off on a phone.
- No clickbait punctuation pile-ups, no ALL CAPS words, no emoji.
- Vary the structure across the ten: question, reveal, mechanism, number,
  consequence, contrast.

Return JSON: {{"titles": ["...", "..."]}}"""

    def _structural_titles(self, script: Script, idea: ContentIdea) -> list[str]:
        """Deterministic fallbacks derived from the actual script content."""
        topic = (idea.topic or "").strip().rstrip(".")
        if not topic:
            return []
        # Title-case each word ("black holes" -> "Black Holes") but leave short
        # acronyms alone ("AI" must not become "Ai").
        subject = " ".join(
            w if w.isupper() and len(w) <= 4 else w.capitalize()
            for w in topic.split())
        angle_words = keywords(idea.angle or script.script, limit=3)
        detail = angle_words[0].title() if angle_words else subject
        # Every pattern here is deliberately NUMBER-AGNOSTIC. The topic label
        # can be singular ("neutron star") or plural ("animals"), so nothing may
        # depend on subject-verb agreement - "What Animals Actually Does" was a
        # real output before this was fixed.
        return [
            f"The Part Of {subject} Nobody Explains",
            f"Inside {subject}: What Most People Miss",
            f"The Truth About {subject}",
            f"{subject}: The Detail Everyone Skips",
            f"How {detail} Changes {subject}",
            f"{subject}, Explained Properly",
        ]

    # ------------------------------------------------------------------
    def score_title(self, title: str, script: Script,
                    idea: ContentIdea, *, authored: bool = False,
                    withhold: bool = False) -> dict[str, Any]:
        """Score 0-100 across the spec's eight dimensions.

        `authored` marks a title that came with a human-reviewed banked
        script rather than being generated during this render. It earns a
        bonus rather than an automatic win: a person chose it AND a person
        read it, which is evidence the rubric cannot measure, but a genuinely
        bad authored title should still be able to lose.
        """
        text = title.strip()
        # THE VISIBLE TITLE - what a viewer reads - separated from the
        # furniture. A leading emoji, a " | English gloss" for a Devanagari
        # title, and a hashtag tail are all things the niche's own top
        # performers carry (50% emoji, 83% hashtags in our cached research),
        # and every one of them was being counted as length and as words. One
        # trailing emoji cost an identical Hindi sentence 6.4 points.
        visible = _visible_title(text)
        # Everything downstream reads the VISIBLE text. Scoring the hashtag
        # tail as vocabulary made "#shorts #kahani" count towards search
        # relevance and specificity, so decoration nudged the score on its
        # own - 57.8 against 57.2 for the same sentence.
        lowered = visible.lower()
        toks = set(words(lowered))
        length = len(visible)

        # Words the video is ACTUALLY about.
        #
        # Capitalisation cannot identify a proper noun in a Title Case string:
        # "What Happens When Kiran Sets a Paper Boat" has eight capitals and
        # one name. That is why an earlier attempt at this still ranked the
        # formulaic title first - every candidate scored as maximally
        # specific. The reliable signal is the SCRIPT: a word that appears in
        # the narration is a word the video delivers, and "happens" does not
        # appear in a story about a paper boat while "Kiran" and "boat" do.
        subject_words = {w for w in words(script.script.lower())
                         if len(w) > 3 and w not in STOPWORDS}

        # Specificity gates the question-mark bonus.
        #
        # A flat +0.25 for "?" paid a vague question the same as a pointed
        # one, and "What Happens When ...?" is the vaguest possible use of a
        # question mark - it commits to nothing. A question only earns the
        # bonus when the title also names something concrete, which is the
        # difference between "Will Kiran's Boat Reach the Gate?" and "What
        # Happens When ...?".
        names_something = (any(c.isdigit() for c in text)
                           or len(toks & subject_words) >= 2)
        curiosity = clamp(len(toks & CURIOSITY_WORDS) / 2.0)
        if visible.endswith(("?", "…")) and names_something:
            curiosity = clamp(curiosity + 0.25)

        # Clarity: readable length, not too many words, no jargon pileup.
        # A no-penalty BAND rather than a single ideal length. The researched
        # top thirty in this niche run 8-14 words at a 66.5-character median;
        # scoring a peak at 9 words made every one of them lose to a
        # four-word plot label.
        word_count = len(visible.split())
        if 8 <= word_count <= 14:
            clarity = 1.0
        else:
            miss = (8 - word_count) if word_count < 8 else (word_count - 14)
            clarity = clamp(1.0 - miss / 8.0)
        if length > 95:
            clarity *= 0.75

        emotional = clamp(len(toks & EMOTION_WORDS) / 2.0 + 0.25)

        # Specificity: numbers and words the script actually delivers.
        has_number = any(c.isdigit() for c in text)
        concrete = len(toks & subject_words)
        specificity = clamp((0.4 if has_number else 0.0)
                            + min(concrete, 4) * 0.15 + 0.2)

        # Novelty: does it avoid the tired stock phrasings?
        #
        # The FORMULAIC OPENERS were the ones actually being produced, and the
        # rubric rewarded them twice over - once through `curiosity`, which
        # counts "what"/"how"/"will" plus a question mark, and again through
        # `click`, which pays a bonus for a curiosity word in the first three
        # words. A measured run of a hand-written bedtime story scored "What
        # Happens When Kiran Sets a Paper Boat on a Monsoon Path?" at 75 and
        # the authored "Kiran and the Paper Boat" at 51, ranking it ninth of
        # ten. That is the wrong way round: the first is sixty characters of
        # throat-clearing that promises nothing, and titles that work in this
        # niche name the character and the trouble - "The Lion Who Couldn't
        # Roar". Reported as "title is not eye catching".
        tired = ("top 10", "you need to know", "in 60 seconds", "explained simply",
                 "mind blowing", "must know")
        formulaic = ("what happens when", "you won't believe", "this is why",
                     "here's why", "here is why", "the truth about",
                     "what nobody tells you", "everything you need",
                     "the secret to", "watch what happens")
        novelty = clamp(1.0
                        - sum(1 for t in tired if t in lowered) * 0.4
                        - sum(1 for t in formulaic if lowered.startswith(t)) * 0.5)

        # Search relevance: shares vocabulary with the actual content.
        search = clamp(token_overlap(visible,
                                     f"{idea.topic} {script.script[:600]}") * 1.5)

        # Click potential: front-loaded interest.
        #
        # This used to pay 0.3 for a CURIOSITY WORD in the first three words,
        # which - together with the curiosity term above at weight 0.20 -
        # meant 36% of the score rewarded the same opening word twice. The
        # result was measurable: "What Happens When Kiran Sets a Paper Boat
        # on a Monsoon Path?" beat the authored "Kiran's Boat Will Not Sail".
        #
        # What actually front-loads interest is a SPECIFIC thing in the
        # opening words - a name, a number, the subject itself. "Kiran's Boat
        # Will Not Sail" opens on a character and a problem; "What happens
        # when" opens on nothing. The curiosity credit stays but is now
        # smaller than the specific one.
        opening = visible.split()[:3]
        opening_toks = set(words(" ".join(opening).lower()))
        opens_specific = (bool(opening_toks & subject_words)
                          or any(c.isdigit() for w in opening for c in w))
        click = clamp(0.35
                      + (0.28 if opens_specific else 0.0)
                      + (0.12 if opening_toks & CURIOSITY_WORDS else 0.0)
                      + (0.2 if has_number else 0.0)
                      + (0.15 if 40 <= length <= 70 else 0.0))

        # Misleading risk: subtractive, and also checks the script backs it up.
        risk = 0.0
        reasons: list[str] = []
        for pattern, weight, why in MISLEADING_PATTERNS:
            if re.search(pattern, text, 0 if pattern.startswith(r"\b[A-Z]") else re.I):
                risk += weight
                reasons.append(why)
        # Claim support: every content word should appear in, or relate to, the script.
        support = token_overlap(text, script.script)
        if support < 0.20:
            risk += 0.22
            reasons.append("title vocabulary barely appears in the script")

        # A STORY TITLE MUST NOT ANSWER ITS OWN QUESTION.
        #
        # This is the inversion that mattered. Search relevance and
        # specificity both reward vocabulary the script uses - so for a
        # story, the highest-scoring title was the one that described the
        # ENDING. Measured: "आरव, गेंद और खाट का दूसरा सिरा" scored 75.8
        # while a version that stops at the problem scored 50.2, and
        # "दूसरा सिरा" IS the turn. The pipeline was picking spoilers on
        # purpose, so writing better titles would have changed nothing.
        #
        # Words introduced in the last two scenes are the payoff. Words from
        # the opening are the premise and stay fair game, which is what
        # keeps the character's name and the object out of the penalty.
        spoilers: set[str] = set()
        if withhold:
            scenes = script.scene_objects()
            if len(scenes) >= 3:
                # THE PAYOFF IS THE LAST ~40%, which is where the turn sits.
                # The last two scenes alone were too narrow: in a 7-scene
                # story the turn is scene 5, so "दूसरा सिरा" - literally the
                # idea the story turns on - fell outside the window and the
                # spoiler went unpunished.
                tail = max(2, round(len(scenes) * 0.4))
                opening = " ".join(s.narration for s in scenes[:-tail]).lower()
                ending = " ".join(s.narration for s in scenes[-tail:]).lower()
                # STEMS, not exact tokens. Hindi inflects: the narration says
                # "दूसरे सिरे" and the title says "दूसरा सिरा", which never
                # match as strings even though they are the same spoiler.
                # A four-character prefix catches the inflection without
                # colliding across unrelated words.
                def stems(text: str) -> set[str]:
                    # Three letters, because "far end" is the whole giveaway
                    # in "the far end of the cot" and a four-letter floor
                    # drops both words. STOPWORDS keeps the noise out.
                    return {w[:4] for w in words(text)
                            if len(w) >= 3 and w not in STOPWORDS}

                spoiled = stems(ending) - stems(opening)
                spoilers = {w for w in toks
                            if len(w) >= 3 and w not in STOPWORDS
                            and w[:4] in spoiled}

        parts = {
            "curiosity": curiosity, "clarity": clarity, "emotional_pull": emotional,
            "specificity": specificity, "novelty": novelty,
            "search_relevance": search, "click_potential": click,
        }
        weights = {"curiosity": 0.20, "clarity": 0.16, "emotional_pull": 0.10,
                   "specificity": 0.14, "novelty": 0.10,
                   "search_relevance": 0.14, "click_potential": 0.16}
        if withhold:
            # A story is not found by matching its own words - it is chosen
            # from a thumbnail and a promise. The weight moves to curiosity
            # rather than being dropped, so the scale still tops out at 100.
            weights = {**weights, "search_relevance": 0.06, "curiosity": 0.28}
        base = sum(parts[k] * weights[k] for k in parts)
        if spoilers:
            risk += min(0.25, 0.09 * len(spoilers))
            reasons.append(
                "gives away the ending: "
                + ", ".join(sorted(spoilers)[:4]))
        score = clamp(base - risk + (AUTHORED_BONUS if authored else 0.0)) * 100
        return {"title": text, "score": round(score, 1), "authored": authored,
                "breakdown": {k: round(v, 3) for k, v in parts.items()},
                "misleading_risk": round(risk, 3), "risk_reasons": reasons}

    # ------------------------------------------------------------------
    def build_tags(self, script: Script, idea: ContentIdea,
                   profile: NicheProfile) -> list[str]:
        """Relevant tags, no stuffing: derived from real script vocabulary."""
        pool: list[str] = []
        for source in (idea.topic, profile.name, idea.angle):
            pool += [k for k in keywords(source or "", limit=4)]
        pool += keywords(script.script, limit=10)
        # Two-word phrases read as real search terms.
        topic_kws = keywords(f"{idea.topic} {profile.name}", limit=3)
        if len(topic_kws) >= 2:
            pool.append(f"{topic_kws[0]} {topic_kws[1]}")
        if profile.name:
            pool.append(profile.name.lower())

        tags: list[str] = []
        used: set[str] = set()
        total = 0
        for tag in pool:
            t = re.sub(r"\s+", " ", tag).strip().lower()
            if not t or t in used or len(t) < 3:
                continue
            if total + len(t) + 1 > YOUTUBE_TAGS_TOTAL_CHARS or len(tags) >= 15:
                break
            used.add(t)
            tags.append(t)
            total += len(t) + 1
        return tags

    def build_chapters(self, script: Script) -> list[dict[str, Any]]:
        """Chapters for long-form. YouTube needs the first one at 00:00."""
        scenes = script.scene_objects()

        # A sectioned long-form script already carries real section headings
        # from its outline. Those are far better chapter labels than the first
        # sentence of some scene's narration, so prefer them when present.
        if script.chapters:
            starts: list[float] = []
            cursor = 0.0
            for scene in scenes:
                starts.append(cursor)
                cursor += scene.duration or 0.0
            out: list[dict[str, Any]] = []
            for chapter in script.chapters:
                idx = int(chapter.get("scene_index", 0))
                if not 0 <= idx < len(starts):
                    continue
                seconds = round(starts[idx], 1)
                # YouTube requires chapters to be at least 10s apart and the
                # first at 00:00, or it silently shows none at all.
                if out and seconds - out[-1]["seconds"] < 10.0:
                    continue
                out.append({"seconds": seconds,
                            "label": str(chapter.get("heading") or "").strip()
                                     or f"Part {len(out) + 1}"})
            if out:
                out[0]["seconds"] = 0.0
                return out[:MAX_CHAPTERS]

        chapters: list[dict[str, Any]] = []
        cursor = 0.0
        for scene in scenes:
            span = scene.duration or 0.0
            label_source = scene.on_screen_text or scene.narration
            if scene.role in {"hook", "context"} and cursor == 0.0:
                label = "Intro"
            else:
                label = truncate(sentences(label_source)[0]
                                 if sentences(label_source) else label_source, 42)
            if not chapters or (cursor - chapters[-1]["seconds"]) >= 10.0:
                chapters.append({"seconds": round(cursor, 1), "label": label})
            cursor += span
        if chapters:
            chapters[0]["seconds"] = 0.0
        return chapters[:MAX_CHAPTERS]

    # ------------------------------------------------------------------
    def build_description(self, script: Script, idea: ContentIdea,
                          profile: NicheProfile, meta: VideoMetadata, *,
                          video_format: str = "SHORT",
                          hashtags: bool = True) -> str:
        parts: list[str] = []

        # Lead: what the viewer gets. An AUTHORED opener wins - a banked
        # entry carries one written for exactly this slot.
        summary = (script.description_hook or "").strip()
        if not summary:
            # Otherwise the video's own words, MINUS a mandatory disclaimer
            # opener. `disclaimer.apply` prepends a scene and rebuilds
            # `script.script` from the scenes, so on every finance and health
            # video the first two sentences of the narration ARE the
            # disclaimer - and it then appeared twice in one description,
            # once as the lead and again under the disclosures.
            lead = sentences(_lead_narration(script))
            summary = " ".join(lead[:2]) if lead else idea.angle
        parts.append(truncate(summary, 260))
        # `idea.angle` is an ANALYSIS field, not copy. A real description shipped
        # with the line "consequence: the future tidal silence as the Moon
        # drifts away" - the hook-type label and all. Same class of leak as the
        # one that reached the narration, so it gets the same treatment.
        angle = _publishable_angle(idea.angle)
        if angle:
            parts.append(truncate(angle, 200))

        if meta.chapters and video_format == "LONGFORM":
            parts.append("Chapters:\n" + "\n".join(
                f"{int(c['seconds']) // 60:02d}:{int(c['seconds']) % 60:02d} {c['label']}"
                for c in meta.chapters))

        # Model-generated citations are NOT published.
        #
        # A real run produced these under "Sources and further reading":
        #   - NASA JPL Lunar Recession Update, 2024
        #   - Nature Astronomy, "Moon-Earth Distance Evolution" 2024
        # Plausible-looking, correctly formatted, and unverifiable - the system
        # prompt forbids inventing studies and the model did it anyway. Printing
        # them in a public description presents fabrications as evidence, which
        # is worse than citing nothing. They stay in script.json for review, and
        # the fact-check report already grades the claims that rest on them.
        if script.sources:
            log_event("METADATA", "model-supplied sources withheld from the "
                                  "description (unverifiable)",
                      count=len(script.sources))

        # Required / recommended disclosures (spec sections 11 & 48).
        if meta.synthetic_disclosure:
            parts.append("This video was produced with AI assistance "
                         "(script, synthetic narration and generated visuals).")
        for disclaimer in profile.disclaimers:
            parts.append(disclaimer)
        if profile.made_for_kids:
            parts.append("Made for children. No external links or purchase prompts.")

        if hashtags:
            tag_pool = [t for t in meta.tags if " " not in t][:3]
            if video_format != "LONGFORM":
                tag_pool = (tag_pool + ["shorts"])[:3]
            if tag_pool:
                parts.append(" ".join(f"#{t.replace('-', '')}" for t in tag_pool))

        text = "\n\n".join(p for p in parts if p and p.strip())
        return truncate(text, YOUTUBE_DESC_LIMIT)

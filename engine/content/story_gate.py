"""Does this child-directed script actually contain a story?

Every check here failed on at least one of the three kids scripts that were
sitting on disk, and between them they describe the exact shape of the
complaint: "kids stories are boring and not interesting".

Why a separate gate rather than more prompt text. The prompt already asks for
all of this - a named character, three tries, a verbatim refrain, no opening
question - and the model complies with most of it most of the time. Asking
harder does not fix the tail. A gate does, because it runs before
`stage_render` and a script that fails costs one more LLM call instead of a
whole image budget and a render.

Regex and Counter only. No API call, no new dependency, and it must stay that
way: a gate that costs a round trip is a gate somebody will disable.

The checks are deliberately split. Seven are BLOCKING because a script
failing them is not a story at all - no protagonist, no repetition, an
opening rhetorical question, an adult solving it for the child, a turn that
is only a glance, an obstacle that is only an ache, or a refrain made of
abstractions. Four are ADVISORY because they are matters of degree and a
false positive should not throw away a usable script.

The last three of those were advisory when written, because 12 of the 19
banked entries failed them and blocking would have made the bank
un-importable. All twelve were rewritten; the bank passes; they block now.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# Tokens that appear in scene 1 and recur, but are not the character's name.
# Deliberately short: the name detector wants the most-recurring token that is
# not obviously furniture, and over-filtering hides a real failure.
_NOT_NAMES = {
    # English
    "the", "a", "an", "and", "but", "or", "so", "then", "she", "he", "they",
    "her", "his", "them", "it", "its", "was", "is", "are", "were", "had",
    "has", "have", "did", "does", "do", "not", "no", "yes", "on", "in", "at",
    "to", "of", "for", "with", "from", "up", "down", "out", "one", "two",
    "very", "too", "all", "this", "that", "there", "here", "when", "what",
    "little", "small", "big", "day", "night", "time", "again", "now",
    # Hindi function words, in Devanagari
    "और", "एक", "यह", "वह", "है", "था", "थी", "थे", "में", "पर", "से", "को",
    "का", "की", "के", "ने", "नहीं", "फिर", "तो", "भी", "बहुत", "अब", "जब",
    "उसने", "उसकी", "उसका", "वो", "ये", "हैं", "गया", "गई", "कर", "लिया",
}

# An adult solving the child's problem. The measured failure: the mother
# fixed it, which is the agency inversion the craft literature names.
_RESCUERS = re.compile(
    r"\b(mother|mummy|mum|mom|mama|papa|dad|daddy|father|teacher|"
    r"grandma|grandpa|granny|nani|dadi|nana|dada|aunt|uncle)\b"
    r"|माँ|मां|मम्मी|पापा|पिता|शिक्षक|टीचर|दादी|नानी|दादा|नाना|चाचा|मौसी",
    re.I)

_WANT = re.compile(
    r"\b(wants?|wanted|wishes|wished|needs?|needed|looking for|hoping|"
    r"tries to reach|dreams? of|"
    # A want shown rather than named. "She had been waiting all week for that
    # one" and "her mouth watered" are stronger writing than "she wanted it",
    # and the keyword-only pattern scored them as no want at all.
    r"waiting for|been waiting|mouth watered|longed?|ached to|"
    r"had to have|could ?n[o']t wait)\b"
    r"|चाहत[ीाे]|चाहिए|ढूंढ|तलाश|पाना चाहत|सपना|इंतज़ार|मुँह में पानी",
    re.I)

_OBSTACLE = re.compile(
    # WHAT AN OBSTACLE IS, not the words a writer uses to apologise for one.
    #
    # This was a contrast-and-negation matcher - but, however, still,
    # couldn't, didn't - and concrete writing does not need any of them.
    # Measured on 43 narrative entries, it flagged 18 as "nothing goes wrong
    # anywhere", among them "The crack split wide open. The can emptied onto
    # the path. The tap was locked." and "Two more hands land on Priya's
    # drum. Three want one. The beat breaks."
    #
    # It also told two authors that the fix was to add "but" to scene 2. A
    # check satisfiable by inserting a filler conjunction teaches the
    # opposite of what it exists for, which is the same mistake as the beat
    # table that produced nine body-ache obstacles in a row.
    #
    # Three families, plus the original negation vocabulary - "the boat
    # would not float" is still a real obstacle.
    r"\b(but|however|still|instead|too (high|tall|far|heavy|small|narrow)|"
    r"could ?n[o']t|did ?n[o']t|was ?n[o']t|would ?n[o']t|misses|missed|"
    r"fails?|failed|stuck|wobbl\w*|slipp\w*|"
    # 1. SOMEBODY ELSE TAKES IT. The commonest real obstacle in this bank,
    # and the beat table asks for it by name: "a second person who wants it
    # too".
    r"claim\w*|took|takes|taken|grabb\w*|snatch\w*|kept|keeps|"
    r"someone else|somebody else|another\w*|other \w+ wants|wants it too|"
    # 2. THE THING BREAKS, EMPTIES OR ENDS.
    r"tipp\w*|topple\w*|fell|spill\w*|tangl\w*|jamm\w*|refuse\w*|"
    r"snap\w*|broke\w*|breaks?|crack\w*|split|tore|torn|rips?|"
    r"empt\w*|drain\w*|ran out|runs out|used up|gone|lost|locked|shut|"
    r"dries?|dried|melt\w*|shrank|shrink\w*|smaller|"
    # 3. A LIMIT APPEARS, or a count runs down.
    # "only <n> left" was too rigid to match "only one mango was
    # left" - three words between, not one. Caught by its own test.
    r"only (one|two|three|a few|\w+)( \w+){0,2} (was |were )?left|"
    r"(was|were) left\b|last one|no more|nothing left|not enough|"
    r"drops to|down to|stopped|stops|halted|vanish\w*|disappear\w*|"
    # 4. WORSE BY DEGREE, not only by event. "The sharp tapping echoed even
    # louder" and "the low vibration travelled straight through the
    # mattress" are real complications - something intensifying - and
    # nothing above recognised a situation escalating rather than breaking.
    #
    # Deliberately not `grew`, `closer` or `tried harder`: a seed growing
    # and a child trying harder are not complications, and an advisory
    # check that fires on them stops meaning anything.
    r"louder|worse|stronger|even more|more and more|kept \w+ing|"
    r"straight through|right through|"
    # "once more" dropped: "she rang it once more for the fun of it" is
    # a success repeated, not an obstacle, and the original list scored
    # it as one. "tried again" stays - it implies a prior failure.
    r"tried again|nothing but)\b"
    # Hindi. The original list was five contrast words and three failures,
    # which is why a Devanagari story describing a real mishap scored zero.
    r"|लेकिन|मगर|फिर भी|नहीं|बहुत ऊँच|बहुत दूर|टिक नहीं|गिर|पलट"
    # NUKTA AND INFLECTION. खाली was listed and ख़ाली was not, so a poem
    # whose obstacle is "दादी की बारी ख़ाली रही" scored as having none - the
    # same class of Devanagari trap as the perception-turn regex that never
    # fired. Stems, not whole words, because Hindi inflects: रुक covers
    # रुकी, रुका and रुक गया.
    r"|टूट|छूट|फिसल|फँस|फंस|लुढ़क|भीग|बिखर|खाली|ख़ाली|खत्म|ख़त्म|"
    r"रुक|थम|छीन|ले लिया|ले ली|"
    r"बंद|बच गय|बाकी|बची|कम पड़|सिर्फ़ एक|आखिरी|आख़िरी|रुक गय|थम गय|"
    # MORE HINDI EVENT VERBS. After the poem fix, every entry still flagged
    # "nothing goes wrong anywhere" was Hindi - 16 of them - and every one
    # described a real event: पन्ना फट गया, बुर्ज ढह गया, रस्सी अटक गई,
    # डोरी उलझ गई, लट्टू टकराया, कील खिसक गई. The English side had words
    # for tearing, collapsing, sticking and tangling; the Hindi side did
    # not, so the same obstacle passed in one language and failed in the
    # other.
    r"फट|ढह|अटक|उलझ|टकरा|मुड़|खिसक|ढील|ढीली|"
    # Escalation, matching the English `louder|worse` family.
    r"बढ़ ग|बढ़ने|बढ़कर|तेज़ चल|तेज़ हो|शोर से भर|"
    # A limit of heat or cold, and a thing carried off or pounced on.
    # `उड़` is NOT here on its own - a kite flying is the success, not the
    # obstacle - only the completive "blew away".
    r"तपकर|बहुत गर्म|उड़ ग|कूद पड़",
    re.I)

_PARTICIPATION = re.compile(
    # "<verb> with me" generally, rather than a list of three verbs.
    #
    # The old pattern recognised only "say it with me", "try with me" and
    # "count with me", and the effect was visible in the output: the model
    # reproduced the example phrasings verbatim, so a story about a kite
    # asked the child to knock three times. A story-appropriate invitation -
    # "blow with me, one big puff" - scored as no invitation at all.
    r"\b(can you|could you|will you|\w+ (it )?with me|clap|knock)\b"
    r"|क्या तुम|मेरे साथ|साथ बोलो|साथ गिन",
    re.I)

# A scene that OPENS with a rhetorical question. The shipped story began
# "Can a hug turn a dark room into a starry sky?" - a question about a
# feeling, with no character in it.
_OPENING_QUESTION = re.compile(
    r"^\s*(have you|has anyone|what if|what happens|can a|can you imagine|"
    r"do you ever|did you ever|why do|why does|ever wondered|imagine)"
    r"|^\s*क्या ",
    re.I)

# A TURN THAT IS ONLY A PERCEPTION.
#
# 14 of our 17 kids narratives resolve with the child LOOKING somewhere
# else: "Then Aarav peeked at the far end of the cot", "तभी विवान ने ...
# देखा". Nothing is invented, combined, traded or reframed - the camera
# simply pans. The test is: if the turn can be restated as "they looked
# somewhere else", it is not a turn, and a five-year-old cannot copy it
# tomorrow.
_PERCEPTION_TURN = re.compile(
    r"^\s*(then\s+|so\s+|at last\s+|suddenly\s+)?"
    r"[\w']+\s+(just\s+|then\s+|finally\s+)?"
    r"(noticed|saw|spotted|looked|peeked|peered|glanced|remembered|"
    r"realised|realized|heard|listened|watched|found)\b"
    # `\s`, NOT `\b`, after the Devanagari word. A vowel sign such as the
    # final "ी" of "तभी" is a combining mark, and Python does not count
    # combining marks as word characters - so there is no word boundary
    # between "तभी" and the space after it, and `\b` silently killed the
    # whole Hindi branch. Same family of bug as the Latin-only tokeniser
    # that gave every Hindi script a perfect hook score.
    r"|^\s*तभी\s.*?(देखा|देखी|सुना|झाँका|झांका|दिखा|दिखी|सूझा|याद आया)",
    re.I)

# AN OBSTACLE THAT IS ONLY A FEELING IN THE BODY.
#
# Every one of the 17 puts an ache in the obstacle beat, and "throat went
# tight" appears verbatim in three different stories. A body sensation is
# a fine SECOND sentence; on its own it is not a complication, because
# nothing about the situation has changed.
_BODY_ONLY = re.compile(
    r"\b(ache[ds]?|aching|sore|tight|tired|heavy|throbb\w*|sting\w*|"
    r"burn\w*|trembl\w*|shiver\w*|wobbl\w* knees|out of breath)\b"
    r"|दुखने|दुख रह|थक|भारी लग|साँस फूल|काँप",
    re.I)

# Something that CHANGES THE SITUATION: another person who wants the same
# thing, a limit appearing, or the attempt breaking something.
_COMPLICATION = re.compile(
    # THE ESCAPE HATCH FOR A BLOCKING CHECK, so a gap here rejects good
    # writing. It had no word for somebody TAKING something - the first
    # example of a good obstacle in the beat table - so this scene was
    # blocked:
    #
    #   "His sister took one chair for her dolls. Four corners, one
    #    roof, hold tight."
    #
    # The obstacle is the sister taking the chair. It failed because the
    # refrain riding along in the same scene says "tight", and nothing
    # here matched "took". The author renamed the refrain to get past
    # it, which is the gate editing the writing for the wrong reason.
    r"\b(another|someone else|somebody else|"
    r"too\b.*\b(also|as well)|now (also|both)|"
    # 1. Somebody else takes or keeps it.
    r"took|take|takes|taking|taken|claim\w*|grabb\w*|snatch\w*|kept|keeps|"
    r"wants it too|wants the same|"
    # 2. The thing breaks, empties, ends - or simply comes down.
    #
    # "shifted and dropped", "collapsed straight downward", "rolled
    # sideways, knocking her drink" are all real obstacles that were
    # refused, because the scene also said "heavy" or "tight" and
    # nothing here recognised the event.
    r"broke|broken|snap\w*|crack\w*|spilled|spilt|tore|torn|rips?|"
    r"dropp\w*|drops|collaps\w*|roll\w*|knock\w*|slid|slipp\w*|"
    r"gave way|came down|toppl\w*|tumbl\w*|tipp\w*|"
    # A sensation belonging to a THIRD PARTY or an animal is not the
    # protagonist feeling something, and the scene around it is a real
    # event: "cold wind seeped through the loose frame", "the
    # shivering puppy scrambled out onto the floor".
    r"seep\w*|crept|creep\w*|scrambl\w*|wriggl\w*|"
    r"escap\w*|got out|climbed out|pull\w*|push\w*|"
    r"ran out|runs out|used up|empt\w*|locked|shut|gone|lost|"
    # 3. A limit appears.
    r"last one|only one|not enough|no more|nothing left|drops to|"
    r"before (the|it)|had to choose|started to cry|"
    r"began to cry|crying|shouted|called out)\b"
    r"|और भी|दूसरा भी|टूट|फट|गिर पड़|रोने लग|चिल्ला|आख़िरी|बस एक ही"
    r"|छीन|ले लिया|ले ली|माँग|मांग|खींचातानी|फँस|लुढ़क|ख़ाली|खाली"
    r"|कम पड़|बस एक|सिर्फ़ एक",
    re.I)

# Refrains made of ideas rather than things. A child cannot point at
# "kindness" or chant "patience"; they can point at a ball and chant a
# count.
_ABSTRACT_REFRAIN = re.compile(
    r"\b(sharing|share|kindness|kind|quiet|calm|enough|brave|bravery|"
    r"patience|patient|happy|happiness|love|friendship|sorry|proud)\b"
    r"|हिम्मत|सब्र|दया|प्यार|खुशी|शांति|अच्छा बनो",
    re.I)

# Half the scenes, not 60%.
#
# A six-beat story that names the character in three scenes and uses a
# pronoun in the others is normal, well-written prose - the earlier 0.6
# failed exactly that. The check is "is there a protagonist at all", and 0.5
# still rejects a script with no recurring character.
MIN_NAME_COVERAGE = 0.5
REFRAIN_MIN_WORDS = 4
REFRAIN_MIN_REPEATS = 3
PARTICIPATION_TARGET = 2


@dataclass
class Finding:
    check: str
    passed: bool
    blocking: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check, "passed": self.passed,
                "blocking": self.blocking, "detail": self.detail}


@dataclass
class StoryReport:
    findings: list[Finding] = field(default_factory=list)
    character: str = ""
    refrain: str = ""

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking and not f.passed]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if not f.blocking and not f.passed]

    @property
    def passed(self) -> bool:
        return not self.blockers

    def summary(self) -> str:
        """One line naming what to fix, for a corrective re-ask."""
        return "; ".join(f.detail for f in self.blockers + self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "character": self.character,
                "refrain": self.refrain,
                "findings": [f.to_dict() for f in self.findings]}


def _tokens(text: str) -> list[str]:
    """Words, keeping combining marks so Indic text survives.

    The same trap as engine/core/util.py words() and the thumbnail headline:
    `\\w` matches Devanagari base letters but not the matras, so an
    ASCII-minded pattern shreds Hindi into single consonants.
    """
    return [t for t in re.split(r"[^\wऀ-ॿ']+", (text or "").lower())
            if t]


def _mid_sentence_capitals(text: str) -> set[str]:
    """Capitalised words that are NOT merely sentence-initial.

    The strongest available name signal in a Latin script, and it does not
    exist in Devanagari - so it is used when present and frequency is the
    fallback when it is not. Frequency alone picked "yet" as the protagonist
    of a story about Mia, because the refrain "Not yet, Mia. Not yet." made
    both words equally common and the tie broke arbitrarily.
    """
    out: set[str] = set()
    for sentence in re.split(r"(?<=[.!?।])\s+", text or ""):
        words = sentence.split()
        for i, raw in enumerate(words):
            word = raw.strip("\"'.,!?;:()")
            if i == 0 or not word or not word[:1].isupper():
                continue
            if word.isupper() and len(word) > 1:
                continue                      # shouted word, not a name
            token = word.lower()
            if token not in _NOT_NAMES:
                out.add(token)
    return out


def _shouted(text: str) -> set[str]:
    """Words written in ALL CAPS anywhere, lowercased.

    Excluded from BOTH name paths. The capitals path already skipped them,
    but the frequency fallback then picked them up: a story whose tin was
    "HIGH on the shelf" got "high" as its protagonist and passed the
    named-character check, which makes the gate wrongly lenient rather than
    wrongly strict.
    """
    return {w.strip("\"'.,!?;:()").lower()
            for w in (text or "").split()
            if len(w.strip("\"'.,!?;:()")) > 1
            and w.strip("\"'.,!?;:()").isupper()}


def _depossess(token: str) -> str:
    """Strip an English possessive so "kiran's" counts as "kiran".

    `_tokens` keeps the apostrophe inside a word, which is right for "don't"
    and wrong here: a story refers to its protagonist possessively constantly
    - "Kiran's knees ached", "Devi's stomach twisted" - and counting those as
    a different word entirely dropped a well-written story from 50% name
    coverage to 33% and rejected it.

    Only a TRAILING apostrophe-s is removed, so "don't" and "it's" are
    untouched.
    """
    if token.endswith("'s") and len(token) > 3:
        return token[:-2]
    if token.endswith("'") and len(token) > 2:
        return token[:-1]
    return token


def _coverage(candidates: set[str], narrations: list[str]) -> tuple[str, float]:
    if not candidates or not narrations:
        return "", 0.0
    hits: Counter = Counter()
    for narration in narrations:
        present = {_depossess(t) for t in _tokens(narration)}
        for candidate in candidates:
            if _depossess(candidate) in present:
                hits[candidate] += 1
    if not hits:
        return "", 0.0
    # Most scenes first, then the longer token - a tie between "yet" and
    # "mia" should not be settled by dictionary order.
    name = max(hits.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
    return name, hits[name] / len(narrations)


def _find_character(narrations: list[str]) -> tuple[str, float]:
    """The most plausible protagonist name, and how many scenes it appears in.

    Capitalisation first where the script has it, frequency where it does not
    (Devanagari has no capitals, so a capital-only heuristic would silently
    pass every Hindi script). Either way the name must appear in scene 1: a
    character introduced halfway through is not the protagonist.
    """
    if not narrations:
        return "", 0.0
    first_tokens = set(_tokens(narrations[0]))
    if not first_tokens:
        return "", 0.0

    joined = " ".join(narrations)
    shouted = _shouted(joined)

    capitals = (_mid_sentence_capitals(joined) & first_tokens) - shouted
    if capitals:
        return _coverage(capitals, narrations)

    frequency = {t for t in first_tokens
                 if len(t) > 1 and t not in _NOT_NAMES} - shouted
    return _coverage(frequency, narrations)


def _find_refrain(narrations: list[str]) -> tuple[str, int]:
    """The longest phrase repeated verbatim, and how many times.

    Longest first, so "step stretch and grab it" is reported rather than the
    four-word window inside it.
    """
    counts: Counter = Counter()
    for narration in narrations:
        words = _tokens(narration)
        for size in range(REFRAIN_MIN_WORDS, min(len(words), 12) + 1):
            for i in range(len(words) - size + 1):
                counts[" ".join(words[i:i + size])] += 1
    repeated = [(phrase, n) for phrase, n in counts.items() if n >= 2]
    if not repeated:
        return "", 0
    # Most repeats first, then longest - a phrase said three times beats a
    # longer one said twice.
    repeated.sort(key=lambda pair: (pair[1], len(pair[0].split())), reverse=True)
    return repeated[0]


def evaluate(narrations: list[str], *, words_per_scene_floor: int = 12,
             total_floor: int = 0,
             beats: list[str] | None = None) -> StoryReport:
    """Check a child-directed narrative for the shape of a story.

    `beats` are the entry's own beat names, when the caller has them. They
    let the craft checks look at the RIGHT scene - the turn, the obstacle -
    instead of guessing by position.
    """
    report = StoryReport()
    narrations = [n for n in (narrations or []) if (n or "").strip()]
    if not narrations:
        report.findings.append(Finding("has_scenes", False, True,
                                       "the script has no narration at all"))
        return report

    joined = " ".join(narrations)
    total_words = len(_tokens(joined))

    # ---- 1. a protagonist, named and recurring ------------------- BLOCKING
    name, coverage = _find_character(narrations)
    report.character = name
    report.findings.append(Finding(
        "named_character", bool(name) and coverage >= MIN_NAME_COVERAGE, True,
        (f"no recurring character: the most frequent word from scene 1 is "
         f"{name!r} and it appears in only {coverage:.0%} of scenes - name "
         f"one character and use the name in most scenes")
        if not (name and coverage >= MIN_NAME_COVERAGE)
        else f"character {name!r} in {coverage:.0%} of scenes"))

    # ---- 2. a want, stated early --------------------------------- advisory
    early = " ".join(narrations[:2])
    report.findings.append(Finding(
        "want_stated_early", bool(_WANT.search(early)), False,
        "no want in the first two scenes - say what the character wants"
        if not _WANT.search(early) else "want stated early"))

    # ---- 3. something in the way --------------------------------- advisory
    #
    # ONLY WHERE THE FORM HAS ONE. A poem is open / verse_a / refrain /
    # verse_b / refrain_2 / verse_c / close - there is no obstacle beat in
    # it, and asking anyway flagged 21 of 54 banked poems for missing a
    # structural element they are not supposed to contain. The beats are
    # the evidence, so read them rather than taking the shape on trust;
    # with no beats given, the question still applies.
    beat_names = {str(b).lower() for b in (beats or [])}
    if (not beat_names) or any("obstacle" in b for b in beat_names):
        report.findings.append(Finding(
            "has_obstacle", bool(_OBSTACLE.search(joined)), False,
            "nothing goes wrong anywhere - a story needs a failed attempt"
            if not _OBSTACLE.search(joined) else "obstacle present"))

    # ---- 4. a verbatim refrain ----------------------------------- BLOCKING
    refrain, repeats = _find_refrain(narrations)
    report.refrain = refrain
    report.findings.append(Finding(
        "verbatim_refrain", repeats >= REFRAIN_MIN_REPEATS, True,
        (f"no refrain: the most repeated phrase is {refrain!r} said "
         f"{repeats} time(s) - repeat one short line WORD FOR WORD at least "
         f"{REFRAIN_MIN_REPEATS} times")
        if repeats < REFRAIN_MIN_REPEATS
        else f"refrain {refrain!r} x{repeats}"))

    # ---- 5. join-in lines, about two ----------------------------- advisory
    invites = len(_PARTICIPATION.findall(joined))
    report.findings.append(Finding(
        "participation", 1 <= invites <= 3, False,
        f"{invites} join-in lines; aim for {PARTICIPATION_TARGET}"
        if not 1 <= invites <= 3 else f"{invites} join-in lines"))

    # ---- 6. no scene opens on a rhetorical question -------------- BLOCKING
    offenders = [i for i, n in enumerate(narrations)
                 if _OPENING_QUESTION.match(n.strip())]
    report.findings.append(Finding(
        "no_opening_question", not offenders, True,
        (f"scene(s) {offenders} open with a rhetorical question - open on the "
         f"character doing something")
        if offenders else "no rhetorical openings"))

    # ---- 7. scenes long enough to be sentences ------------------- advisory
    per_scene = total_words / len(narrations)
    floor_ok = per_scene >= words_per_scene_floor and (
        not total_floor or total_words >= total_floor)
    report.findings.append(Finding(
        "enough_words", floor_ok, False,
        (f"{per_scene:.1f} words per scene and {total_words} total, against "
         f"{words_per_scene_floor}/scene - finish the sentences")
        if not floor_ok else f"{per_scene:.1f} words per scene"))

    # ---- 8. the child resolves it, not an adult ------------------ BLOCKING
    #
    # Only the LAST TWO scenes are examined. A kind grown-up earlier in the
    # story is fine and normal; one who appears at the resolution is the
    # agency inversion - and the measured failure was exactly that.
    ending = " ".join(narrations[-2:])
    rescuer = _RESCUERS.search(ending)
    child_present = bool(name) and name in set(_tokens(ending))
    ok = not rescuer or child_present
    report.findings.append(Finding(
        "child_resolves_it", ok, True,
        (f"an adult ({rescuer.group(0)!r}) appears at the resolution without "
         f"the child - the winning idea must be the child's own")
        if not ok else "the child resolves it"))

    # ---- 9. the turn is an IDEA, not a glance -------------------- BLOCKING
    #
    # Advisory when it was written, because 12 of the 19 banked entries
    # failed it and blocking would have made the bank un-importable. All
    # twelve have since been rewritten and the whole bank passes, so it
    # blocks now - which is the point: the next weak batch is rejected
    # before anyone renders it.
    turn = _turn_scene(narrations, beats)
    perception = bool(turn and _PERCEPTION_TURN.match(turn.strip()))
    report.findings.append(Finding(
        "turn_is_an_idea", not perception, True,
        (f"the turn is a perception, not an idea: {turn.strip()[:60]!r} - "
         f"the child should invent, combine, trade or reframe something a "
         f"five-year-old could copy tomorrow, not just look elsewhere")
        if perception else "the turn is an idea"))

    # ---- 10. the obstacle is more than an ache ------------------- advisory
    cost = _obstacle_scene(narrations, beats)
    body_only = bool(cost and _BODY_ONLY.search(cost)
                     and not _COMPLICATION.search(cost))
    report.findings.append(Finding(
        "obstacle_is_more_than_a_feeling", not body_only, True,
        (f"the obstacle is only a body feeling: {cost.strip()[:60]!r} - "
         f"something must get measurably WORSE: a second person who wants "
         f"the same thing, a limit appearing, or the attempt breaking "
         f"something")
        if body_only else "the obstacle complicates something"))

    # ---- 11. a refrain a child can point at ---------------------- advisory
    abstract = bool(refrain and _ABSTRACT_REFRAIN.search(refrain))
    report.findings.append(Finding(
        "refrain_is_concrete", not abstract, True,
        (f"the refrain {refrain!r} is an idea rather than a thing - every "
         f"content word should be something a child can point at, do or "
         f"count")
        if abstract else "the refrain is concrete"))

    return report


def _beat_scene(narrations: list[str], beats: list[str] | None,
                wanted: tuple[str, ...], fallback: float) -> str:
    """The narration of a named beat, or the scene at `fallback` through.

    `evaluate` is given narrations alone by some callers, so the beat names
    are a hint rather than a requirement - positional inference is close
    enough for a six-to-eight beat story and wrong for nothing that matters.
    """
    if beats and len(beats) == len(narrations):
        for index, beat in enumerate(beats):
            if (beat or "").strip().lower() in wanted:
                return narrations[index]
    if not narrations:
        return ""
    return narrations[min(len(narrations) - 1,
                          max(0, int(len(narrations) * fallback)))]


def _turn_scene(narrations: list[str], beats: list[str] | None = None) -> str:
    return _beat_scene(narrations, beats, ("turn", "idea", "twist"), 0.62)


def _obstacle_scene(narrations: list[str],
                    beats: list[str] | None = None) -> str:
    return _beat_scene(narrations, beats,
                       ("obstacle", "cost", "setback"), 0.45)

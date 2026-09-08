"""Caption generation: animated ASS for burn-in + clean SRT for YouTube.

Why per-word Dialogue events instead of ASS \\k karaoke:
  ASS \\k only sweeps SecondaryColour -> PrimaryColour, so it can express
  "words already spoken change colour" but NOT "only the word being spoken is
  highlighted and scaled".  Emitting one event per word-state gives exact
  control over colour, scale and outline of the active word, which is the look
  every high-retention Short uses.  libass handles thousands of events fine.

Safe areas (spec section 44): captions are clamped inside a configurable band
so they never sit under the YouTube Shorts UI (bottom action bar / right rail)
or the top overlay.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.config import Config
from ..core.logging import log_event
from ..tts.base import SceneAudio
from .fonts import display_font


@dataclass
class CaptionWord:
    start: float
    end: float
    text: str
    # Which scene this word was spoken in, so a caption can never straddle
    # two of them. -1 means unknown, which keeps older callers working.
    scene: int = -1


@dataclass
class CaptionGroup:
    """A phrase that appears on screen as a unit."""
    words: list[CaptionWord]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end


def _ts(seconds: float) -> str:
    """ASS timestamp: h:mm:ss.cc"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _escape(text: str) -> str:
    """Escape the characters libass treats as markup."""
    return (text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            .replace("\n", " ").strip())


def absolute_words(clips: list[tuple[float, SceneAudio]]) -> list[CaptionWord]:
    """Convert per-clip relative word marks into one absolute timeline."""
    out: list[CaptionWord] = []
    for index, (offset, clip) in enumerate(clips):
        for w in clip.words:
            text = (w.text or "").strip()
            if not text:
                continue
            start = offset + max(w.start, 0.0)
            end = start + max(w.duration, 0.08)
            out.append(CaptionWord(start=start, end=end, text=text,
                                   scene=index))
    out.sort(key=lambda w: (w.start, w.scene))
    # Remove overlaps so a word never starts before the previous one ends.
    for i in range(1, len(out)):
        if out[i].start < out[i - 1].end:
            out[i - 1].end = max(out[i - 1].start + 0.06, out[i].start)
    return out


# Sentence terminators, and NOT just the English ones.
#
# This rule used to be `endswith((".", "!", "?", ":", ";"))`, which meant a
# Hindi sentence never ended: Devanagari finishes a sentence with the danda
# "।", not a full stop. So the sentence-boundary break never fired on any
# Indic-script video and grouping fell through to the word- and
# character-count limits, producing captions spliced out of two different
# sentences - one read "niyam hai Arav", the last two words of one sentence
# followed by the first word of the next.
SENTENCE_END = (
    ".", "!", "?", ":", ";",
    "।", "॥",          # danda, double danda - Devanagari and kin
    "۔", "؟",          # Urdu full stop, Arabic question mark
    "。", "！", "？",   # CJK full stop, bang, question mark
)

# A comma is a WEAKER break: worth honouring only when the caption is already
# long enough to be worth ending, otherwise every clause becomes its own
# two-word flash.
CLAUSE_END = (",", "،", "、", "，")


def group_words(words: list[CaptionWord], max_words: int = 4,
                max_chars: int = 26, max_gap: float = 0.60,
                max_span: float = 2.6) -> list[CaptionGroup]:
    """Chunk words into readable phrases.

    Breaks on: sentence punctuation, word count, character count, a long pause
    between words, or a long elapsed span.  Those five rules together keep the
    on-screen text short enough to read at Shorts pace.
    """
    groups: list[CaptionGroup] = []
    current: list[CaptionWord] = []

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(CaptionGroup(words=current))
            current = []

    for w in words:
        prospective_chars = sum(len(x.text) + 1 for x in current) + len(w.text)
        gap = (w.start - current[-1].end) if current else 0.0
        span = (w.end - current[0].start) if current else 0.0
        # A caption may never contain words from two scenes. The picture
        # changes at that boundary, so text carried across it describes an
        # image that is no longer on screen.
        crossed_scene = bool(current) and w.scene != current[-1].scene             and w.scene >= 0 and current[-1].scene >= 0
        if current and (crossed_scene
                        or len(current) >= max_words
                        or prospective_chars > max_chars
                        or gap > max_gap
                        or span > max_span):
            flush()
        current.append(w)
        stripped = w.text.rstrip()
        if stripped.endswith(SENTENCE_END):
            flush()
        elif stripped.endswith(CLAUSE_END) and len(current) >= max(2, max_words - 1):
            flush()
    flush()
    return _merge_orphans(groups, max_words=max_words, max_chars=max_chars,
                          max_gap=max_gap, max_span=max_span)


def _merge_orphans(groups: list[CaptionGroup], *, max_words: int,
                   max_chars: int, max_gap: float,
                   max_span: float) -> list[CaptionGroup]:
    """Fold a one-word caption back into the phrase it belongs to.

    Adding the sentence-terminator break created these: the word limit ends a
    caption, then the very next word carries the full stop and flushes
    immediately, leaving the closing word of a sentence alone on screen for a
    third of a second. A Hindi story showed "kya yah niyam sach mein niyam"
    and then just "hai." by itself.

    Every condition here is a guard against undoing a break that was made for
    a REASON. In particular the gap and span checks: a first version of this
    re-joined two words either side of a 2.3-second pause, silently reversing
    the long-pause rule above. Cosmetic tidying must not override timing.
    """
    out: list[CaptionGroup] = []
    for group in groups:
        prev = out[-1] if out else None
        if (prev is not None
                and len(group.words) == 1
                and len(prev.words) < max_words + 1
                # same scene: the picture must not have changed
                and group.words[0].scene == prev.words[-1].scene
                # still fits on one line
                and sum(len(w.text) + 1 for w in prev.words)
                + len(group.words[0].text) <= max_chars
                # and the break was NOT a deliberate timing break
                and (group.words[0].start - prev.words[-1].end) <= max_gap
                and (group.words[0].end - prev.words[0].start) <= max_span):
            prev.words.append(group.words[0])
            continue
        out.append(group)
    return out


class CaptionEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.enabled = bool(cfg.get("captions.enabled", True))
        self.style = str(cfg.get("captions.style", "karaoke"))
        self.font_size = int(cfg.get("captions.font_size", 92))
        self.primary = str(cfg.get("captions.primary_color", "&H00FFFFFF"))
        self.highlight = str(cfg.get("captions.highlight_color", "&H0000E5FF"))
        self.outline = int(cfg.get("captions.outline", 6))
        self.shadow = int(cfg.get("captions.shadow", 3))
        self.safe_bottom = float(cfg.get("captions.safe_bottom", 0.22))
        self.safe_top = float(cfg.get("captions.safe_top", 0.12))
        self.max_words = int(cfg.get("captions.max_words_on_screen", 3))
        self.max_chars = int(cfg.get("captions.max_chars_on_screen", 20))
        self.uppercase = bool(cfg.get("captions.uppercase", True))

    # ------------------------------------------------------------------
    def srt_only(self, clips: list[tuple[float, SceneAudio]]) -> str:
        """The SRT track without burning anything into the picture.

        Used when captions are switched off for a video. YouTube still gets a
        real subtitle file - so viewers who want subtitles have them and the
        video is still indexed on its words - while the frame stays clean,
        which is what the narration-only reference videos do.
        """
        words = absolute_words(clips)
        if not words:
            return ""
        groups = group_words(words, max_words=self.max_words + 2,
                             max_chars=int(self.max_chars * 1.6))
        return self._render_srt(groups)

    def build_translated(self, spans: list[tuple[float, float, str]],
                         out_ass: Path, out_srt: Path,
                         width: int, height: int, *,
                         language: str = "") -> tuple[Path, Path, int]:
        """Captions in a DIFFERENT language from the narration.

        One block per scene, timed to the span the scene occupies. Not
        karaoke, and there is deliberately no option to make it karaoke:
        word-level timing comes from the synthesiser, so it describes the
        words the VOICE says. Highlighting translated words in an order the
        voice is not saying them looks broken.

        `spans` is (start, end, text) per scene, already in absolute timeline
        seconds - the same offsets the audio was assembled from, so this is
        exact rather than estimated.
        """
        groups: list[CaptionGroup] = []
        floor = float(self.cfg.get("captions.min_block_seconds", 0.85))
        for start, end, text in spans:
            clean = (text or "").strip()
            if not clean:
                continue
            # A very short scene would flash text nobody can read. Hold it for
            # the floor instead, overlapping into the next scene's span - which
            # is what a human subtitler does.
            stop = max(end, start + floor)
            groups.append(CaptionGroup(
                words=[CaptionWord(start=start, end=stop, text=clean)]))
        if not groups:
            raise RuntimeError("no translated caption text available")

        ass_text = self._render_ass(groups, width, height, "block", language)
        out_ass.parent.mkdir(parents=True, exist_ok=True)
        out_ass.write_text(ass_text, encoding="utf-8")
        out_srt.write_text(self._render_srt(groups), encoding="utf-8")
        log_event("CAPTION", "translated captions built", blocks=len(groups),
                  language=language)
        return out_ass, out_srt, len(groups)

    def build(self, clips: list[tuple[float, SceneAudio]], out_ass: Path,
              out_srt: Path, width: int, height: int, *,
              style_override: str | None = None,
              language: str = "") -> tuple[Path, Path, int]:
        words = absolute_words(clips)
        if not words:
            raise RuntimeError("no word timings available for captions")

        # Long-form frames are wider, so more words fit comfortably.
        landscape = width > height
        max_words = self.max_words + (2 if landscape else 0)
        max_chars = int(self.max_chars * 1.6) if landscape else self.max_chars
        # Cap by what actually FITS on one line at this font size and frame
        # width. A configured character count knows nothing about either, so a
        # caption that was legal by the config wrapped onto a second line on
        # the phone - which doubled the area the text covered and pushed it up
        # into the picture. One line is a layout guarantee, not a preference.
        max_chars = min(max_chars, self._chars_per_line(width, height, language))
        groups = group_words(words, max_words=max_words, max_chars=max_chars)

        style = style_override or self.style
        ass_text = self._render_ass(groups, width, height, style, language)
        out_ass.parent.mkdir(parents=True, exist_ok=True)
        out_ass.write_text(ass_text, encoding="utf-8")
        out_srt.write_text(self._render_srt(groups), encoding="utf-8")

        log_event("CAPTION", "captions built", groups=len(groups),
                  words=len(words), style=style)
        return out_ass, out_srt, len(groups)

    # ------------------------------------------------------------------
    def _render_ass(self, groups: list[CaptionGroup], width: int, height: int,
                    style: str, language: str = "") -> str:
        # The language decides the FONT, not just the text. Anton and Arial
        # Black have no Devanagari glyphs, so a Hindi caption rendered as a row
        # of tofu boxes once Hindi scripts started generating correctly.
        font_file, family = display_font(
            str(self.cfg.get("captions.font_file", "Anton")), language=language)
        size = self._scaled_font_size(width, height)
        # MarginV is measured from the bottom for bottom-aligned text.
        margin_v = int(height * self.safe_bottom)
        margin_h = int(width * self.margin_fraction)

        header = [
            "[Script Info]",
            "; Jaadugar generated captions",
            "ScriptType: v4.00+",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "",
            "[V4+ Styles]",
            ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
             "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
             "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
             "Alignment, MarginL, MarginR, MarginV, Encoding"),
            # Bold is OFF, and that is two fixes in one field.
            #
            # It was -1, which makes libass SYNTHESISE bold by smearing each
            # glyph wider - while the character budget is measured from the
            # font file at its real weight. So every line was wider than
            # calculated and ran off the edge of the frame, which no amount of
            # adjusting the budget would have fixed. Turning it off makes the
            # measurement true, and normal weight is also what was asked for:
            # a heavy display face at 112px was shouting.
            (f"Style: Main,{family},{size},{self.primary},{self.highlight},"
             f"&H00101010,&H80000000,{self._bold_flag()},0,0,0,100,100,"
             f"{self._letter_spacing(language)},0,1,"
             f"{self.outline},{self.shadow},2,{margin_h},{margin_h},{margin_v},1"),
            "",
            "[Events]",
            ("Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
             "MarginV, Effect, Text"),
        ]

        events: list[str] = []
        if style == "block":
            events = self._block_events(groups)
        elif style == "none":
            events = []
        else:
            events = self._karaoke_events(groups)

        return "\n".join(header + events) + "\n"

    # Fallback only, used if the font cannot be measured. Deliberately on the
    # wide side so the estimate errs towards a shorter caption than necessary
    # rather than one that wraps.
    GLYPH_RATIO = 0.46

    # Representative text per script, for measuring the real average advance.
    _RULERS = {
        "deva": "क्या जादू दिखता है जब माँ कहानी पढ़ती है",
        "taml": "ஒரு அணைப்பு இருண்ட அறையை நட்சத்திர வானமாக",
        "telu": "ఒక కౌగిలి చీకటి గదిని నక్షత్రాల ఆకాశంగా",
        "beng": "একটি আলিঙ্গন অন্ধকার ঘরকে তারার আকাশে",
        "gujr": "એક ભેટ અંધારા રૂમને તારાઓના આકાશમાં",
        "": "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG",
    }

    def _bold_flag(self) -> int:
        """ASS Bold: -1 for on, 0 for off.

        Off by default. Synthetic bold widens glyphs past what the character
        budget was measured against, and the bundled faces are already heavy.
        """
        return -1 if bool(self.cfg.get("captions.bold", False)) else 0

    def _letter_spacing(self, language: str) -> float:
        """ASS Spacing (letter tracking). ZERO for complex scripts.

        This is what made Hindi captions unreadable even after the right font
        was loaded. libass applies tracking between every GLYPH, and in
        Devanagari a syllable is a base plus combining marks - so tracking
        pushes each matra off its consonant and the result is a row of
        detached fragments. Isolated by rendering the same line with tracking
        on and off: identical font, identical everything else.

        Tracking is a Latin display-typography flourish. For Indic scripts it
        is not a weaker effect, it is wrong.
        """
        from .fonts import script_for_language
        if script_for_language(language):
            return 0.0
        # Was hard-coded at 1.2 here, ignoring the template that already had a
        # letter_spacing field.
        return float(self.cfg.get("captions.letter_spacing", 1.2))

    def _glyph_ratio(self, language: str, size: int) -> float:
        """Average advance per character, as a fraction of font size.

        MEASURED from the actual font file rather than assumed. Two different
        constants were wrong in opposite directions: Anton is condensed
        (~0.436) while Devanagari is narrower still (~0.380), because its
        vowel signs stack above and below rather than advancing. Guessing one
        number for both either wraps the caption or wastes half the width.
        """
        try:
            from PIL import ImageFont
            from .fonts import script_for_language
            font_file, _ = display_font(
                str(self.cfg.get("captions.font_file", "Anton")),
                language=language)
            ruler = self._RULERS.get(script_for_language(language),
                                     self._RULERS[""])
            face = ImageFont.truetype(str(font_file), size)
            return max(0.20, face.getlength(ruler) / (len(ruler) * size))
        except Exception:
            return self.GLYPH_RATIO

    def _chars_per_line(self, width: int, height: int,
                        language: str = "") -> int:
        """How many characters fit on ONE line inside the side margins."""
        size = self._scaled_font_size(width, height)
        usable = width * (1.0 - 2 * self.margin_fraction)
        spacing = self._letter_spacing(language)   # matches the ASS Spacing
        # 6% headroom: the ruler is an average, and one caption of unusually
        # wide characters should still not wrap.
        per_char = size * self._glyph_ratio(language, size) * 1.06 + spacing
        return max(8, int(usable / per_char))

    @property
    def margin_fraction(self) -> float:
        """Side margin as a fraction of width.

        Was 0.075 a side, which threw away 15% of the frame and forced an
        early wrap. Captions now use nearly the full width, which is what
        keeps them on one line.
        """
        return float(self.cfg.get("captions.margin_fraction", 0.045))

    def _scaled_font_size(self, width: int, height: int) -> int:
        """Font size configured for 1080x1920; scale to the actual frame."""
        base = self.font_size
        if width > height:                      # long-form: relatively smaller
            return max(28, int(base * (height / 1920) * 1.35))
        return max(28, int(base * (width / 1080)))

    def _karaoke_events(self, groups: list[CaptionGroup]) -> list[str]:
        """One event per word-state: the active word is coloured and scaled up."""
        events: list[str] = []
        for group in groups:
            words = group.words
            for i, active in enumerate(words):
                start = active.start if i > 0 else group.start
                # Hold the last word until the group ends to avoid a flicker gap.
                end = words[i + 1].start if i + 1 < len(words) else group.end + 0.12
                if end <= start:
                    end = start + 0.10

                pieces: list[str] = []
                for j, w in enumerate(words):
                    token = _escape(w.text.upper() if self.uppercase else w.text)
                    if j == i:
                        # Active: highlight colour, slight scale bump, thicker edge.
                        pieces.append(
                            f"{{\\c{self.highlight}\\3c&H00201000&"
                            f"\\fscx108\\fscy108\\bord{self.outline + 1}}}{token}"
                            f"{{\\r}}")
                    else:
                        pieces.append(f"{{\\c{self.primary}}}{token}{{\\r}}")
                text = " ".join(pieces)
                # A short pop on the first word of the group reads as an entrance.
                if i == 0:
                    text = "{\\fad(70,0)}" + text
                events.append(
                    f"Dialogue: 0,{_ts(start)},{_ts(end)},Main,,0,0,0,,{text}")
        return events

    def _block_events(self, groups: list[CaptionGroup]) -> list[str]:
        """Whole phrase, no per-word highlight (calmer; used for kids content)."""
        events: list[str] = []
        for group in groups:
            # A translated block arrives as ONE "word" holding a whole
            # sentence. Uppercasing that is shouting rather than emphasis, and
            # several scripts have no case at all, so it is left alone.
            translated = len(group.words) == 1 and " " in group.words[0].text
            upper = self.uppercase and not translated
            tokens = [_escape(w.text.upper() if upper else w.text)
                      for w in group.words]
            text = "{\\fad(90,70)}" + " ".join(tokens)
            events.append(
                f"Dialogue: 0,{_ts(group.start)},{_ts(group.end + 0.14)},"
                f"Main,,0,0,0,,{text}")
        return events

    @staticmethod
    def _render_srt(groups: list[CaptionGroup]) -> str:
        """Plain SRT for the YouTube captions track (no styling, real casing)."""
        lines: list[str] = []
        for i, group in enumerate(groups, start=1):
            text = " ".join(w.text for w in group.words).strip()
            lines += [str(i),
                      f"{_srt_ts(group.start)} --> {_srt_ts(group.end + 0.1)}",
                      text, ""]
        return "\n".join(lines)

"""Thumbnail generation (spec section 16).

Generates 3 variants, scores each, picks the best. Principles enforced in code:
one clear subject, high contrast, very little text, no fake claims.

For Shorts a thumbnail matters far less than the first frame, so for SHORT the
generator uses the video's own opening frame as the base (which is what viewers
actually see in feeds and on the channel grid) and keeps text minimal.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from contextlib import suppress
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from ..core.config import Config
from ..core.logging import log_event
from ..core.util import (clamp, ffmpeg_bin, ffmpeg_filter_path, run,
                        words)
from ..video.fonts import display_font

# YouTube thumbnail spec: 1280x720 minimum, under 2 MB, JPG/PNG.
#
# Rendered at 1920x1080 rather than the 1280x720 minimum. The text is
# rasterised natively at this size instead of being upscaled with the image,
# and it measured 356 KB - comfortably inside the 2 MB cap, which was the only
# reason to stay small.
THUMB_W, THUMB_H = 1920, 1080
MAX_BYTES = 2 * 1024 * 1024

# Filler that never earns thumbnail space. Deliberately KEEPS the curiosity
# words (why / what / how / who) - those carry the hook.
SKIP_WORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "with", "and", "or",
    "is", "are", "was", "were", "that", "this", "it", "its", "at", "by",
    "from", "you", "your", "we", "our", "they", "their", "as", "but",
    "do", "does", "did", "so", "be", "been", "being", "will", "would",
    "can", "could", "should", "just", "very", "really", "get", "got",
    "than", "then", "when", "while", "into", "about", "over", "after",
    "before", "if", "no", "not", "all", "more", "most", "some", "any",
    "there", "here", "up", "out", "down", "off", "has", "have", "had",
    # HINDI FUNCTION WORDS. This set was English-only, so a Hindi title never
    # split into runs at all - the whole title was one run and the four-word
    # cap became a blind truncation. Hindi puts its postpositions AFTER the
    # noun, so cutting at four words reliably ended ON one: "मीरा और छत पर
    # रखी दादी की चप्पल" became "मीरा और छत पर" - "Meera and roof on",
    # which drops the entire subject of the title.
    "का", "के", "की", "को", "में", "पर", "से", "ने", "है", "हैं", "था",
    "थी", "थे", "और", "या", "भी", "ही", "तो", "कि", "यह", "वह", "ये",
    "वे", "एक", "लिए", "साथ", "बाद", "पहले", "जब", "तब", "अगर", "नहीं",
    "हुआ", "हुई", "हुए", "करना", "करने", "किया",
}


@dataclass
class ThumbnailVariant:
    path: Path
    style: str
    score: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    text: str = ""


def _is_devanagari(text: str) -> bool:
    """True when most letters are Devanagari.

    "Most" rather than "any", so an English headline containing one Hindi
    word does not switch strategies.
    """
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if not letters:
        return False
    hits = sum(1 for ch in letters if "ऀ" <= ch <= "ॿ")
    return hits > len(letters) / 2


def _headline(title: str, max_words: int = 4) -> str:
    """Reduce a title to a few words that still read as a phrase.

    Two strategies, because English and Hindi put their function words in
    different places and one rule cannot serve both.

    ENGLISH: the longest CONTIGUOUS run of content words. Picking the "most
    specific" words independently produces word salad ("The First Light Of A
    Dying Star Was Finally Caught" -> "FIRST FINALLY CAUGHT"); the run rule
    yields "FIRST LIGHT".

    HINDI: the TAIL, trimmed of function words. See the comment at the branch
    - Hindi is head-final and its postpositions sit inside the noun phrase,
    so the run rule takes it apart rather than preserving it.
    """
    # `\w` DROPS COMBINING MARKS, and that silently destroyed every Hindi
    # headline. Python's \w matches Devanagari base letters (category Lo) but
    # not the matras and viramas (Mn/Mc) that turn them into words - so
    # "गाँव की स्कूल यादें" lost ा ँ ी ् ू े
    # ं and became eight bare consonants, from which the four-word cap
    # produced the headline "ग व क स". Both Hindi jobs on disk shipped that.
    #
    # Same bug class as engine/core/util.py words(), where an ASCII-only
    # pattern returned zero words for Indic text. Category "M" is the whole
    # fix and it is script-general.
    raw = "".join(
        ch if (ch.isalnum() or unicodedata.category(ch).startswith("M")
               or ch in " '-")
        else " "
        for ch in (title or "")
    ).split()
    if not raw:
        return ""

    # HINDI IS HEAD-FINAL, so it needs the opposite strategy.
    #
    # The contiguous-run rule below assumes function words sit BETWEEN
    # phrases, which is true of English ("The Truth About Animals" splits at
    # "the" and "about"). Hindi puts its postposition INSIDE the noun phrase:
    # "कागज़ की नाव" is "paper boat" as one unit, and splitting on की shreds
    # it into two one-word runs, from which the longest-run rule returned just
    # "कागज़" - "paper".
    #
    # Hindi also puts the head noun LAST, so the informative words are at the
    # end rather than the front. Taking the tail and trimming function words
    # off both edges gets the actual subject: "मीरा और छत पर रखी दादी की
    # चप्पल" -> "रखी दादी की चप्पल", and "किरन और कागज़ की नाव" -> "कागज़ की
    # नाव".
    if _is_devanagari(" ".join(raw)):
        tail = raw[-max_words:]
        while tail and tail[0].lower() in SKIP_WORDS:
            tail = tail[1:]
        while len(tail) > 1 and tail[-1].lower() in SKIP_WORDS:
            tail = tail[:-1]
        # Everything was a function word: fall back to the longest word, which
        # in a head-final language is usually the head noun.
        if not tail:
            tail = [max(raw, key=len)]
        return " ".join(tail)

    # Split into runs of consecutive content words.
    runs: list[list[str]] = []
    current: list[str] = []
    for word in raw:
        if word.lower() in SKIP_WORDS:
            if current:
                runs.append(current)
                current = []
        else:
            current.append(word)
    if current:
        runs.append(current)
    if not runs:
        return " ".join(raw[:max_words]).upper()

    # Longest run wins; earliest run breaks ties (front-loaded titles read best).
    best_index = max(range(len(runs)), key=lambda i: (len(runs[i]), -i))
    best = runs[best_index]

    # A single-word run cannot be extended with the NEXT run: those words are
    # not adjacent in the title, so joining them invents a phrase that was never
    # written ("The Truth About Animals" -> "TRUTH ANIMALS"). Use the single
    # most specific word instead - a one-word thumbnail is normal.
    if len(best) < 2:
        best = [max((w for run in runs for w in run), key=len)]

    best = best[:max_words]
    # Never end on a dangling qualifier. "What Space Actually Does" truncated to
    # three words gave "WHAT SPACE ACTUALLY", which reads as a cut-off sentence;
    # dropping the trailing adverb gives the cleaner "WHAT SPACE".
    while len(best) > 1 and (_is_qualifier(best[-1]) or _is_weak_ending(best[-1])):
        best = best[:-1]

    return " ".join(best).upper()


# Adverbs and intensifiers that must not be the last word of a headline.
_QUALIFIERS = {
    "actually", "really", "truly", "very", "quite", "rather", "almost",
    "nearly", "simply", "merely", "hardly", "barely", "totally", "utterly",
    "completely", "absolutely", "definitely", "probably", "possibly",
    "apparently", "basically", "essentially", "literally", "seriously",
    "finally", "eventually", "suddenly", "recently", "currently",
}


# Determiners and pronouns that leave a headline hanging mid-thought.
_WEAK_ENDINGS = {
    "nobody", "everyone", "someone", "anyone", "everybody", "anybody",
    "most", "every", "each", "both", "either", "neither", "another",
    "such", "same", "own", "other", "others",
    # Interrogatives and determiners that promise a noun and then do not
    # deliver one. "PPF vs NPS: Which One Actually Locks Your Money Longer?"
    # truncated to "PPF VS NPS WHICH", which reads as a sentence cut off
    # mid-word.
    "which", "what", "who", "whose", "whom", "how", "why", "where",
    "one", "ones", "vs", "versus",
    # Hindi postpositions and conjunctions, for the same reason - and these
    # matter more, because Hindi word order puts them at exactly the position
    # a four-word cap lands on.
    "और", "या", "पर", "की", "का", "के", "को", "में", "से", "ने", "तो",
    "भी", "ही", "कि", "लिए", "साथ",
}


def _is_weak_ending(word: str) -> bool:
    return word.lower().strip(".,!?'\"") in _WEAK_ENDINGS


def _is_qualifier(word: str) -> bool:
    lowered = word.lower().strip(".,!?'\"")
    if lowered in _QUALIFIERS:
        return True
    # Most -ly words are adverbs; keep short exceptions like "only"/"early".
    return lowered.endswith("ly") and len(lowered) > 6


def _fit_font(font_path: Path, text: str, max_w: int, max_h: int,
              start: int = 260) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Largest font size that fits `text` in at most 2 lines.

    `start` is a CEILING, not a size - the sweep below walks down from it
    until the text fits. It was 150, which measured as the binding constraint
    rather than the frame: "SCHOOL DAYS" fits at 222, and the difference at
    the ~120px width most impressions are actually served at is a cap height
    of 12.1px against 17.9px. Small text on a thumbnail is invisible text.
    """
    wordlist = text.split()
    for size in range(start, 34, -4):
        font = ImageFont.truetype(str(font_path), size)
        for split in range(len(wordlist), 0, -1):
            lines = [" ".join(wordlist[:split]), " ".join(wordlist[split:])]
            lines = [ln for ln in lines if ln]
            widest = max(font.getbbox(ln)[2] - font.getbbox(ln)[0] for ln in lines)
            height = sum(font.getbbox(ln)[3] - font.getbbox(ln)[1] + size * 0.22
                         for ln in lines)
            if widest <= max_w and height <= max_h:
                return font, lines
    font = ImageFont.truetype(str(font_path), 38)
    return font, [text]


def _draw_text_block(img: Image.Image, lines: list[str],
                     font: ImageFont.FreeTypeFont, *, anchor: str = "bottom",
                     accent: tuple[int, int, int] = (255, 210, 40),
                     accent_word: int = -1
                     ) -> tuple[int, int, int, int] | None:
    draw = ImageDraw.Draw(img)
    w, h = img.size
    line_heights = [font.getbbox(ln)[3] - font.getbbox(ln)[1] for ln in lines]
    gap = int(font.size * 0.20)
    block_h = sum(line_heights) + gap * (len(lines) - 1)

    if anchor == "bottom":
        y = h - int(h * 0.09) - block_h
    elif anchor == "center":
        y = (h - block_h) // 2
    else:
        y = int(h * 0.09)

    word_index = 0
    # The union of everything drawn, so the scorer can ask where the text is
    # instead of assuming. Without it, "does the text cover the subject" and
    # "is the duration badge clear" are unanswerable.
    box = [w, h, 0, 0]
    for line, lh in zip(lines, line_heights):
        tokens = line.split()
        widths = [font.getbbox(t + " ")[2] - font.getbbox(t + " ")[0] for t in tokens]
        total_w = sum(widths)
        x = (w - total_w) // 2
        box[0] = min(box[0], x)
        box[2] = max(box[2], x + total_w)
        box[1] = min(box[1], y)
        box[3] = max(box[3], y + lh)
        for token, tw in zip(tokens, widths):
            color = accent if word_index == accent_word else (255, 255, 255)
            # Heavy outline keeps text readable over any image.
            stroke = max(4, int(font.size * 0.075))
            draw.text((x, y), token, font=font, fill=color,
                      stroke_width=stroke, stroke_fill=(8, 8, 10))
            x += tw
            word_index += 1
        y += lh + gap
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return (max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3]))


def _keyframes(video: Path, out_dir: Path, limit: int = 24) -> list[Path]:
    """Extract keyframes only, which is the cheap way to get candidates.

    Measured: 60 keyframes out in 5.56s and scored in 2.03s, against 38.4s for
    any route that decodes every frame. Against a render measured in minutes,
    7.6s to choose the thumbnail well is free.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("kf_*.jpg"):
        stale.unlink(missing_ok=True)
    pattern = out_dir / "kf_%03d.jpg"
    try:
        # `-fps_mode passthrough`, not `-vsync 0`: ffmpeg 9 REMOVED -vsync
        # and answers "Unrecognized option 'vsync'", which failed the whole
        # extraction silently into the fixed-frame fallback.
        run([ffmpeg_bin(), "-y", "-loglevel", "error", "-skip_frame", "nokey",
             "-i", str(video), "-fps_mode", "passthrough",
             "-frames:v", str(limit), "-q:v", "3", str(pattern)], timeout=300)
    except Exception as exc:                    # noqa: BLE001
        log_event("THUMBNAIL", "keyframe extraction failed",
                  error=str(exc)[:140])
        return []
    return sorted(out_dir.glob("kf_*.jpg"))


def _frame_interest(path: Path) -> float:
    """How much a candidate frame has going on, and how well exposed it is.

    Deliberately simple and deliberately NOT the variant scorer: this picks
    between frames of the same video, so it wants detail and mid exposure. A
    near-black fade or a flat sky scores low.
    """
    try:
        with Image.open(path) as raw:
            grey = raw.convert("L")
    except Exception:                           # noqa: BLE001
        return -1.0
    from PIL import ImageStat
    stat = ImageStat.Stat(grey)
    detail = ImageStat.Stat(grey.filter(ImageFilter.FIND_EDGES)).mean[0]
    exposure = 1.0 - abs(stat.mean[0] - 118) / 118.0
    return clamp(detail / 24.0) * 0.7 + clamp(exposure) * 0.3


# How much tighter the thumbnail crop is than the frame.
#
# WAS 2.1, AND THAT WAS TOO MUCH. The 2.1 came from measuring PHOTOGRAPHIC
# keyframes, which were wide establishing shots with a face 5-14% of frame
# width - 6-17 pixels at the ~120px an impression is served at. But the
# visuals are now AI illustrations generated from authored briefs like
# "Meera, a six-year-old girl with two plaits, looking up a narrow
# staircase", and the generator already frames its subject. Cropping to 48%
# of a well-composed illustration produced a thumbnail of a forearm and some
# steps, measured, from a real 1920x1080 render.
#
# 1.3 still tightens a loose frame without being able to lose the subject
# entirely, and it upscales by 1.3 rather than 2.1, which keeps the result
# sharp.
DEFAULT_ZOOM = 1.3

# How strongly the crop is pulled back toward frame centre.
#
# Edge energy is the wrong signal for finding a person and there is no better
# one available without a face detector: fabric folds, bangles and brickwork
# are all high-energy while a face is smooth, so the centroid points AWAY
# from the subject. It is still useful for "which half of the frame has the
# content", which is all it is now trusted for.
CENTRE_PULL = 0.55


def _subject_box(img: Image.Image,
                 zoom: float = DEFAULT_ZOOM) -> tuple[int, int, int, int]:
    """A crop `zoom` times tighter, biased toward the busier region.

    The centroid is edge energy over a coarse grid rather than a face
    detector, because a face detector means opencv and there is none in
    requirements.txt - see CENTRE_PULL for why it is only trusted weakly.
    """
    grey = img.convert("L").filter(ImageFilter.FIND_EDGES)
    from PIL import ImageStat
    w, h = grey.size
    cells, total = [], 0.0
    grid = 8
    for gy in range(grid):
        for gx in range(grid):
            cell = grey.crop((gx * w // grid, gy * h // grid,
                              (gx + 1) * w // grid, (gy + 1) * h // grid))
            energy = ImageStat.Stat(cell).mean[0]
            cells.append((energy, gx, gy))
            total += energy
    if total <= 0:
        return (0, 0, w, h)
    # Energy-weighted centroid, biased upward: faces sit above centre.
    cx = sum(e * (gx + 0.5) for e, gx, _ in cells) / total * w / grid
    cy = sum(e * (gy + 0.5) for e, _, gy in cells) / total * h / grid
    cy = cy * 0.88
    # Pull back toward centre. Without this the energy centroid can sit in a
    # corner - a patch of textured wall outscores a face - and the crop then
    # contains none of the subject.
    cx = cx * (1.0 - CENTRE_PULL) + (w / 2) * CENTRE_PULL
    cy = cy * (1.0 - CENTRE_PULL) + (h * 0.45) * CENTRE_PULL

    new_w, new_h = int(w / zoom), int(h / zoom)
    left = int(clamp((cx - new_w / 2) / max(w - new_w, 1)) * (w - new_w))
    top = int(clamp((cy - new_h / 2) / max(h - new_h, 1)) * (h - new_h))
    return (left, top, left + new_w, top + new_h)


def _base_from_video(video: Path, out: Path, at_seconds: float = 0.6,
                    *, zoom: float = DEFAULT_ZOOM) -> Path | None:
    """Choose the best keyframe and crop in on its subject.

    This took a FIXED frame at 0.6 seconds, which on an illustrated video is
    whatever the first scene happens to be - usually a wide establishing shot,
    and sometimes a fade. Scoring keyframes costs seconds against a render
    measured in minutes.

    `at_seconds` is kept as the fallback path for when keyframe extraction
    fails, so the signature stays compatible and there is always a base.
    """
    candidates = _keyframes(video, out.parent / "keyframes")
    if candidates:
        best = max(candidates, key=_frame_interest)
        try:
            with Image.open(best) as raw:
                img = raw.convert("RGB")
                cropped = img.crop(_subject_box(img, zoom=zoom))
                cropped.save(out, "JPEG", quality=94, subsampling=1)
            log_event("THUMBNAIL", "base chosen from keyframes",
                      candidates=len(candidates), picked=best.name,
                      zoom=f"{zoom:.1f}x")
            for stale in candidates:
                stale.unlink(missing_ok=True)
            with suppress(OSError):
                (out.parent / "keyframes").rmdir()
            return out if out.exists() and out.stat().st_size > 2000 else None
        except Exception as exc:                # noqa: BLE001
            log_event("THUMBNAIL", "keyframe crop failed, falling back to a "
                      "fixed frame", error=str(exc)[:140])

    try:
        run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{at_seconds:.2f}",
             "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)],
            timeout=180)
        return out if out.exists() and out.stat().st_size > 2000 else None
    except Exception as exc:
        log_event("THUMBNAIL", "frame grab failed", error=str(exc)[:140])
        return None



# Scripts PIL cannot shape. Everything here needs libass; Latin does not.
_COMPLEX_RANGES = (
    (0x0590, 0x08FF),    # Hebrew, Arabic, Syriac, Thaana, N'Ko
    (0x0900, 0x0DFF),    # Devanagari through Sinhala - all the Indic scripts
    (0x0E00, 0x0FFF),    # Thai, Lao, Tibetan
    (0x1000, 0x109F),    # Myanmar
)


def needs_shaping(text: str) -> bool:
    """True when this text cannot be drawn correctly by PIL.

    PIL without raqm/harfbuzz/fribidi lays glyphs out in logical order with no
    reordering, ligature substitution or mark positioning - so a Devanagari
    conjunct comes apart and its matras land in the wrong place. Checked by
    codepoint range rather than by asking PIL, because PIL does not report
    failure: it draws something wrong and returns success.
    """
    for ch in (text or ""):
        code = ord(ch)
        for low, high in _COMPLEX_RANGES:
            if low <= code <= high:
                return True
    return False


def _ass_escape(text: str) -> str:
    """ASS treats braces as override blocks and newlines as literal."""
    return (text.replace("\\", "").replace("{", "(").replace("}", ")")
            .replace("\n", " ").replace("\r", " ").strip())


def _burn_headline_ass(img: Image.Image, lines: list[str], font_path: Path,
                       size: int, *, anchor: str, accent: tuple[int, int, int],
                       work_dir: Path
                       ) -> tuple[Image.Image, tuple[int, int, int, int] | None]:
    """Draw the headline over `img` with libass. Returns (image, text box).

    The box is recovered by diffing the frame against itself before the burn,
    because libass draws inside ffmpeg and returns no geometry - and the
    scorer needs to know where the text is to judge subject coverage and the
    duration-badge corner.
    """
    from ..video.fonts import family_name

    work_dir.mkdir(parents=True, exist_ok=True)
    before = work_dir / "_ass_in.png"
    after = work_dir / "_ass_out.png"
    ass_path = work_dir / "_headline.ass"
    img.save(before, "PNG")

    w, h = img.size
    family = family_name(font_path, fallback="Sans")
    # ASS colours are &HBBGGRR. The accent is used for the whole headline
    # here rather than one word: per-word colouring needs inline override
    # blocks and the win is not worth the shaping risk.
    primary = f"&H00{accent[2]:02X}{accent[1]:02X}{accent[0]:02X}"
    # 2 = bottom centre, 5 = middle centre in ASS numbering.
    alignment = {"bottom": 2, "center": 5, "top": 8}.get(anchor, 2)
    margin_v = int(h * 0.09) if anchor != "center" else 10
    outline = max(3, int(size * 0.075))
    text = "\\N".join(_ass_escape(line) for line in lines if line.strip())
    if not text:
        return img, None

    ass_path.write_text(
        "[Script Info]\n"
        "; Jaadugar thumbnail headline\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"
        f"PlayResX: {w}\nPlayResY: {h}\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Head,{family},{size},{primary},{primary},&H00101008,"
        f"&H80000000,0,0,0,0,100,100,0,0,1,{outline},3,{alignment},"
        f"{int(w * 0.05)},{int(w * 0.05)},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,0:00:10.00,Head,,0,0,0,,{text}\n",
        encoding="utf-8")

    # `fontsdir` plus the font's OWN family name: libass matches by family,
    # and silently substitutes a default face when the name does not match -
    # which is how captions came out as tofu twice.
    # The SAME escaping the caption burn-in uses. A hand-rolled copy here
    # escaped every colon instead of just the drive letter, ffmpeg rejected
    # the filter, and this function caught it and returned a thumbnail with no
    # text on it at all.
    escaped = ffmpeg_filter_path(ass_path)
    fonts_dir = ffmpeg_filter_path(font_path.parent)
    try:
        run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(before),
             "-vf", f"subtitles=filename='{escaped}':fontsdir='{fonts_dir}'"
                    ":alpha=1",
             "-frames:v", "1", str(after)], timeout=180)
        with Image.open(after) as raw:
            burnt = raw.convert("RGB")
    except Exception as exc:                    # noqa: BLE001
        log_event("THUMBNAIL", "libass headline failed, leaving the image "
                  "without text", error=str(exc)[:140])
        return img, None

    box = _changed_box(img, burnt)
    for temp in (before, after, ass_path):
        temp.unlink(missing_ok=True)
    return burnt, box


def _changed_box(before: Image.Image, after: Image.Image
                 ) -> tuple[int, int, int, int] | None:
    """Bounding box of the pixels libass actually touched."""
    from PIL import ImageChops
    if before.size != after.size:
        return None
    diff = ImageChops.difference(before.convert("L"), after.convert("L"))
    return diff.point(lambda v: 255 if v > 24 else 0).getbbox()


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    src_ratio = img.width / img.height
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        new_w = int(img.height * dst_ratio)
        left = (img.width - new_w) // 2
        img = img.crop((left, 0, left + new_w, img.height))
    elif src_ratio < dst_ratio:
        new_h = int(img.width / dst_ratio)
        top = int((img.height - new_h) * 0.32)
        img = img.crop((0, top, img.width, top + new_h))
    return img.resize((w, h), Image.LANCZOS)


class ThumbnailGenerator:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    # ------------------------------------------------------------------
    def generate(self, *, title: str, out_dir: Path,
                 source_image: Path | None = None,
                 video: Path | None = None,
                 video_format: str = "SHORT",
                 made_for_kids: bool = False,
                 language: str = "",
                 variants: int = 3) -> tuple[Path, list[ThumbnailVariant]]:
        out_dir.mkdir(parents=True, exist_ok=True)
        # Same tofu problem as captions: the headline comes from the title, so
        # a Hindi title needs a face with Devanagari glyphs.
        font_path, _ = display_font(language=language)
        headline = _headline(title, max_words=3 if video_format == "SHORT" else 4)

        base_path: Path | None = None
        if video is not None and video.exists():
            base_path = _base_from_video(video, out_dir / "thumb_base.jpg")
        if base_path is None and source_image is not None and source_image.exists():
            base_path = source_image
        if base_path is None:
            raise RuntimeError("thumbnail needs either a rendered video or a "
                               "source image")

        styles = ["bold_bottom", "split_focus", "minimal_center"][:max(1, variants)]
        built: list[ThumbnailVariant] = []
        for i, style in enumerate(styles):
            target = out_dir / f"thumbnail_{i + 1}.jpg"
            img, text_box = self._render(base_path, headline, style,
                                         font_path,
                                         made_for_kids=made_for_kids,
                                         work_dir=out_dir)
            self._save(img, target)
            variant = ThumbnailVariant(path=target, style=style, text=headline)
            variant.score, variant.metrics = self.score(
                img, headline, title, text_box)
            built.append(variant)

        built.sort(key=lambda v: v.score, reverse=True)
        best = built[0]
        final = out_dir / "thumbnail.jpg"
        final.write_bytes(best.path.read_bytes())
        log_event("THUMBNAIL", "variants generated", count=len(built),
                  best=best.style, score=f"{best.score:.0f}/100")
        return final, built

    # ------------------------------------------------------------------
    def _render(self, base_path: Path, headline: str, style: str,
                font_path: Path, *, made_for_kids: bool,
                work_dir: Path | None = None
                ) -> tuple[Image.Image, tuple[int, int, int, int] | None]:
        with Image.open(base_path) as raw:
            img = _cover(raw.convert("RGB"), THUMB_W, THUMB_H)

        # Global punch: contrast + saturation, gentler for kids content.
        img = ImageEnhance.Contrast(img).enhance(1.10 if made_for_kids else 1.20)
        img = ImageEnhance.Color(img).enhance(1.10 if made_for_kids else 1.22)
        img = img.filter(ImageFilter.UnsharpMask(radius=2.0, percent=85, threshold=3))

        draw = ImageDraw.Draw(img, "RGBA")
        accent = (255, 226, 120) if made_for_kids else (255, 209, 46)

        if style == "bold_bottom":
            # Gradient scrim so text is legible over any image.
            for i in range(int(THUMB_H * 0.46)):
                y = THUMB_H - i
                alpha = int(215 * (i / (THUMB_H * 0.46)) ** 0.85)
                draw.line([(0, y), (THUMB_W, y)], fill=(0, 0, 0, alpha))
            font, lines = _fit_font(font_path, headline,
                                    int(THUMB_W * 0.90), int(THUMB_H * 0.34))
            if needs_shaping(headline):
                img, box = _burn_headline_ass(
                    img, lines, font_path, font.size, anchor="bottom",
                    accent=accent, work_dir=work_dir or base_path.parent)
            else:
                box = _draw_text_block(
                    img, lines, font, anchor="bottom", accent=accent,
                    accent_word=len(headline.split()) - 1)

        elif style == "split_focus":
            # Darken the left third, text stacked there, subject stays visible.
            draw.rectangle([0, 0, int(THUMB_W * 0.52), THUMB_H],
                           fill=(0, 0, 0, 150))
            font, lines = _fit_font(font_path, headline,
                                    int(THUMB_W * 0.46), int(THUMB_H * 0.62))
            sub = Image.new("RGB", (int(THUMB_W * 0.52), THUMB_H))
            sub.paste(img.crop((0, 0, int(THUMB_W * 0.52), THUMB_H)))
            if needs_shaping(headline):
                sub, box = _burn_headline_ass(
                    sub, lines, font_path, font.size, anchor="center",
                    accent=accent, work_dir=work_dir or base_path.parent)
            else:
                box = _draw_text_block(sub, lines, font, anchor="center",
                                       accent=accent, accent_word=0)
            img.paste(sub, (0, 0))
            draw = ImageDraw.Draw(img, "RGBA")
            draw.rectangle([int(THUMB_W * 0.52) - 6, 0,
                            int(THUMB_W * 0.52), THUMB_H], fill=(*accent, 220))

        else:  # minimal_center
            draw.rectangle([0, 0, THUMB_W, THUMB_H], fill=(0, 0, 0, 88))
            font, lines = _fit_font(font_path, headline,
                                    int(THUMB_W * 0.80), int(THUMB_H * 0.40))
            if needs_shaping(headline):
                img, box = _burn_headline_ass(
                    img, lines, font_path, font.size, anchor="center",
                    accent=accent, work_dir=work_dir or base_path.parent)
            else:
                box = _draw_text_block(img, lines, font, anchor="center",
                                       accent=accent, accent_word=-1)

        return img, box

    def _save(self, img: Image.Image, target: Path) -> Path:
        quality = 92
        while quality >= 60:
            img.save(target, "JPEG", quality=quality, optimize=True,
                     subsampling=1, progressive=True)
            if target.stat().st_size <= MAX_BYTES:
                return target
            quality -= 8
        return target

    # ------------------------------------------------------------------
    # The width most impressions are actually served at. Every legibility
    # measurement happens here, because text that reads at 1920px and
    # dissolves at 120px is text nobody reads.
    IMPRESSION_W = 120

    def score(self, img: Image.Image, headline: str, title: str,
              text_box: tuple[int, int, int, int] | None = None
              ) -> tuple[float, dict[str, Any]]:
        """Score a variant on what a viewer at 120px can actually see."""
        from PIL import ImageStat

        w, h = img.size
        grey = img.convert("L")
        small = grey.resize((self.IMPRESSION_W,
                             max(1, round(self.IMPRESSION_W * h / max(w, 1)))),
                            Image.LANCZOS)
        sw, sh = small.size
        scale_x, scale_y = sw / max(w, 1), sh / max(h, 1)

        # ---- legibility, measured where it counts ----------------------
        #
        # Local contrast INSIDE the text box, after downscaling. A gradient
        # scrim raises whole-image contrast without helping the letters; this
        # only rises when the glyphs still separate from their backdrop at
        # thumbnail size.
        if text_box:
            tx0 = max(0, int(text_box[0] * scale_x))
            ty0 = max(0, int(text_box[1] * scale_y))
            tx1 = min(sw, max(tx0 + 1, int(text_box[2] * scale_x)))
            ty1 = min(sh, max(ty0 + 1, int(text_box[3] * scale_y)))
            band = small.crop((tx0, ty0, tx1, ty1))
            legibility = clamp(ImageStat.Stat(band).stddev[0] / 58.0)
        else:
            legibility = 0.0

        # ---- did the text bury the subject? -----------------------------
        edges = grey.filter(ImageFilter.FIND_EDGES)
        grid, cells = 6, []
        for gy in range(grid):
            for gx in range(grid):
                cell = edges.crop((gx * w // grid, gy * h // grid,
                                   (gx + 1) * w // grid, (gy + 1) * h // grid))
                cells.append((ImageStat.Stat(cell).mean[0], gx, gy))
        cells.sort(reverse=True)
        hot = cells[:max(1, grid * grid // 6)]
        if text_box:
            covered = sum(
                1 for _e, gx, gy in hot
                if not (text_box[2] <= gx * w // grid
                        or text_box[0] >= (gx + 1) * w // grid
                        or text_box[3] <= gy * h // grid
                        or text_box[1] >= (gy + 1) * h // grid))
            subject_kept = clamp(1.0 - covered / len(hot))
        else:
            subject_kept = 1.0

        # ---- YouTube's duration badge sits bottom-right -----------------
        badge = (int(w * 0.80), int(h * 0.86), w, h)
        if text_box:
            overlaps = not (text_box[2] <= badge[0] or text_box[0] >= badge[2]
                            or text_box[3] <= badge[1]
                            or text_box[1] >= badge[3])
            badge_clear = 0.0 if overlaps else 1.0
        else:
            badge_clear = 1.0

        # ---- must not dissolve into either theme ------------------------
        #
        # YouTube's dark theme is #0F0F0F and its light theme is white, so a
        # thumbnail whose border is near-black or near-white loses its edge on
        # one of them. Measured: 5 of 20 shipped thumbnails bled into dark.
        border = max(2, int(min(w, h) * 0.012))
        strips = [grey.crop((0, 0, w, border)),
                  grey.crop((0, h - border, w, h)),
                  grey.crop((0, 0, border, h)),
                  grey.crop((w - border, 0, w, h))]
        edge_luma = sum(ImageStat.Stat(s).mean[0] for s in strips) / len(strips)
        edge_safe = 1.0 if 45.0 <= edge_luma <= 200.0 else clamp(
            1.0 - min(abs(edge_luma - 45.0), abs(edge_luma - 200.0)) / 45.0)

        # ---- retained, but they cannot rank variants --------------------
        word_count = len(headline.split())
        text_economy = clamp(1.0 - abs(word_count - 3) / 4.0)
        risky = {"aliens", "proof", "cure", "miracle", "guaranteed", "shocking",
                 "unbelievable", "insane"}
        honesty = clamp(1.0 - len(set(words(title)) & risky) * 0.4)

        parts = {"legibility": legibility, "subject_kept": subject_kept,
                 "badge_clear": badge_clear, "edge_safe": edge_safe,
                 "text_economy": text_economy, "honesty": honesty}
        # The first four discriminate; the last two are reported and lightly
        # weighted so a title-level regression is still visible.
        weights = {"legibility": 0.38, "subject_kept": 0.24,
                   "badge_clear": 0.14, "edge_safe": 0.12,
                   "text_economy": 0.07, "honesty": 0.05}
        score = sum(parts[k] * weights[k] for k in parts) * 100
        stat = ImageStat.Stat(grey)
        return round(score, 1), {**{k: round(v, 3) for k, v in parts.items()},
                                 "mean_luma": round(stat.mean[0], 1),
                                 "edge_luma": round(edge_luma, 1),
                                 "headline_words": word_count,
                                 "measured_at_px": self.IMPRESSION_W}

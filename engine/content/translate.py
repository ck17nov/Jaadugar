"""Caption text in a different language from the narration.

Why this exists: a Hindi-narrated video with English captions reaches people
who will not watch a Hindi video, and the reverse reaches people who will not
read English. Asked for directly - "for hindi voice it should be english
caption and for english voice hindi captions".

The awkward part is timing. There is no "caption text" anywhere else in this
pipeline: captions ARE the TTS word-mark stream re-serialised, so every word on
screen is a word the voice actually spoke, with the offset the synthesiser
reported. A translation has none of that - the words are different words, in a
different order, and there is no per-word timing for them.

So a translated caption cannot be karaoke. It is a per-SCENE block, shown for
the span the scene occupies, which is timing we already know exactly. That is
also why there is deliberately no option to render translated text as karaoke:
highlighting words in an order the voice is not saying them looks broken, and
making it selectable would only invite shipping it.
"""
from __future__ import annotations

from typing import Any

from ..core.logging import log_event
from ..core.models import Scene

SYSTEM = ("You translate video narration for subtitles. You reply with one "
          "JSON object and nothing else.")

_PROMPT = """Translate each numbered line into {target_name} ({target}).

Rules:
- Translate the MEANING for a subtitle, not word for word. It has to be
  readable at a glance while the narrator speaks.
- Keep each line at or below {max_chars} characters. Shorten rather than
  overflow: a subtitle that does not fit is worse than one that loses a
  qualifier.
- Keep names, numbers and units as they are.
- One output line per input line, same numbering, same count. Do not merge,
  split, reorder or omit lines.
- No commentary, no romanisation, no notes.

Return exactly:
{{"lines": [{{"n": 1, "text": "..."}}]}}

LINES:
{lines}
"""

# Language names, so the prompt asks for something unambiguous. A bare code
# gets Hinglish about as often as Hindi.
# Keys are LOWERCASE, because the lookup lowercases its argument - "en-IN"
# against a key of "en-IN" silently missed and the prompt then asked for a
# language called "en-IN".
LANGUAGE_NAMES = {
    "en": "English", "en-in": "Indian English", "hi": "Hindi",
    "hi-latn": "Hinglish (Hindi written in Latin letters)",
    "ta": "Tamil", "te": "Telugu", "bn": "Bengali", "mr": "Marathi",
    "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "es": "Spanish",
}


def language_name(code: str) -> str:
    """A name the model will recognise. Falls back to the BASE language.

    An unmapped regional code such as "en-GB" resolves to "English" rather
    than being handed to the prompt verbatim.
    """
    text = (code or "").strip().lower()
    if not text:
        return "English"
    if text in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[text]
    return LANGUAGE_NAMES.get(text.split("-")[0], code)


def needs_translation(narration_language: str, caption_language: str) -> bool:
    """True only when the two are genuinely different languages.

    "en" against "en-IN" is the same language and must not cost an LLM call.
    An empty caption language means "follow the narration", which is the
    default and the previous behaviour.
    """
    caption = (caption_language or "").strip().lower()
    if not caption:
        return False
    narration = (narration_language or "en").strip().lower()
    return caption.split("-")[0] != narration.split("-")[0]


def translate_scenes(scenes: list[Scene], *, target: str, router: Any,
                     max_chars: int = 90, batch: int = 12) -> int:
    """Fill EMPTY `scene.caption_text`. Returns how many were set.

    Scenes that already carry a caption are left alone. That makes this
    idempotent, and it is what lets a banked script keep the caption its
    author wrote: those are hand-authored translations of that exact scene,
    and machine-translating over them would be a straight downgrade - the
    Hindi captions being wrong is the reason the bank exists.

    Batched because one call per scene on a 300-scene long-form video would be
    300 round trips against a rate-limited free tier.

    Returns 0 and logs rather than raising when translation fails: losing the
    second language is a degradation, but losing the video over it is not a
    trade worth making. The caller then falls back to captions in the
    narration language, which is what every previous version did.
    """
    if router is None or not scenes:
        return 0
    pending = [s for s in scenes if not (s.caption_text or "").strip()]
    authored = len(scenes) - len(pending)
    if authored:
        log_event("CAPTION", "keeping captions that came with the script",
                  authored=authored, translating=len(pending))
    if not pending:
        return 0
    filled = 0
    for start in range(0, len(pending), batch):
        chunk = pending[start:start + batch]
        numbered = "\n".join(
            f"{i + 1}. {scene.narration.strip()}"
            for i, scene in enumerate(chunk) if scene.narration.strip())
        if not numbered:
            continue
        try:
            data, provider = router.complete_json(
                _PROMPT.format(target=target, target_name=language_name(target),
                               max_chars=max_chars, lines=numbered),
                system=SYSTEM,
                # Low, deliberately. At the content default of 0.85 the model
                # paraphrases and drifts off the meaning, which is not what a
                # subtitle is for.
                temperature=0.2, max_tokens=2048)
        except Exception as exc:                  # noqa: BLE001 - see docstring
            log_event("CAPTION", "translation failed, captions will follow the "
                      "narration", target=target, error=str(exc)[:160])
            return filled

        lines = _index_lines(data)
        missing = 0
        for i, scene in enumerate(chunk):
            text = lines.get(i + 1, "").strip()
            if not text:
                missing += 1
                continue
            scene.caption_text = text[:max_chars * 2]
            filled += 1
        if missing:
            log_event("CAPTION", "translation returned fewer lines than sent",
                      missing=missing, of=len(chunk), provider=provider)
    if filled:
        log_event("CAPTION", "captions translated", target=target,
                  scenes=filled, of=len(pending))
    return filled


def _index_lines(data: dict[str, Any]) -> dict[int, str]:
    """Accept the documented shape, and the two the models actually return."""
    out: dict[int, str] = {}
    raw = data.get("lines")
    if isinstance(raw, list):
        for i, item in enumerate(raw, start=1):
            if isinstance(item, dict):
                number = item.get("n", i)
                try:
                    number = int(number)
                except (TypeError, ValueError):
                    number = i
                out[number] = str(item.get("text", ""))
            elif isinstance(item, str):
                # Some models drop the numbering and return a bare list.
                out[i] = item
    elif isinstance(raw, dict):
        for key, value in raw.items():
            try:
                out[int(key)] = str(value)
            except (TypeError, ValueError):
                continue
    return out


def dump(scenes: list[Scene]) -> list[dict[str, Any]]:
    """The translations, for the job directory. Kept as a record of what the
    viewer was shown next to what was said."""
    return [{"index": s.index, "narration": s.narration,
             "caption": getattr(s, "caption_text", "")} for s in scenes]

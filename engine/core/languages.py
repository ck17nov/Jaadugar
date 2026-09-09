"""The languages this project actually ships in.

Asked for directly: "you can remove other languages like tamil/telugu/bengali,
just keep voice: indian english, english, hindi, hinglish. caption: english
and hindi."

WHY A SINGLE MODULE. The list existed in four places - the Android dropdown,
`script.py`'s LANGUAGE_NAMES, `translate.py`'s LANGUAGE_NAMES, and the API's
validation - and they did not agree. Offering Tamil in the app while the voice
engine has no Tamil voice and the font loader has no Tamil font produces a
video with silent audio and boxes for subtitles, and nothing on the way there
says no. Same reason engine/core/groups.py exists.

The wider name tables in script.py and translate.py stay: they are lookups,
they cost nothing, and a job stored before this list narrowed still needs its
language to resolve to a name rather than crash. What narrows is the
SELECTABLE set - what the app offers and what the API will accept.

CAPTIONS ARE DERIVED, NOT CHOSEN. "why is it required? caption should be just
what is in audio" was about not having to pick, and the standing requirement
is "for hindi voice it should be english caption and for english voice hindi
captions". So the Create screen has no caption-language control at all and
`caption_for` decides. One less thing to get wrong per automation, and it
cannot silently drift from what the script bank stores.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Language:
    code: str
    label: str
    # The script the narration is written in. Drives font selection and the
    # Devanagari-purity checks, so it is data rather than inferred from the
    # code - "hi-Latn" is Hindi in Latin letters and gets a Latin font.
    script: str
    # What this narration should be captioned in. The other language, always.
    captions: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "label": self.label,
                "script": self.script, "captions": self.captions}


# Order is display order in the app.
VOICES: tuple[Language, ...] = (
    Language("en-IN", "Indian English", "latin", "hi"),
    Language("en", "English", "latin", "hi"),
    Language("hi", "हिन्दी Hindi", "devanagari", "en"),
    # Hindi words in Latin letters. A real choice for this audience and NOT
    # the same as either: the voice needs a Hindi-capable engine reading Latin
    # text, and the captions have to be English because Latin-script Hindi
    # subtitles under Latin-script Hindi audio would be a transcript.
    Language("hi-Latn", "Hinglish", "latin", "en"),
)

# What a caption track may be written in. Deliberately shorter than VOICES:
# there is no reason to caption in Hinglish, and Indian English captions are
# just English captions.
CAPTIONS: tuple[Language, ...] = (
    Language("en", "English", "latin", ""),
    Language("hi", "हिन्दी Hindi", "devanagari", ""),
)

BY_CODE: dict[str, Language] = {lang.code.lower(): lang for lang in VOICES}


def voice(code: str) -> Language | None:
    """The voice language for a code, tolerating case and a bare base code."""
    wanted = (code or "").strip().lower()
    if not wanted:
        return None
    if wanted in BY_CODE:
        return BY_CODE[wanted]
    # "en-GB" and "en-US" are English; "hi-IN" is Hindi.
    base = wanted.split("-")[0]
    return BY_CODE.get(base)


def is_supported(code: str) -> bool:
    return voice(code) is not None


def caption_for(code: str) -> str:
    """The caption language for a narration language. Never empty.

    This is the whole rule, in one place: Hindi and Hinglish are captioned in
    English, English and Indian English are captioned in Hindi.
    """
    found = voice(code)
    return found.captions if found else "hi"


def script_of(code: str) -> str:
    """"devanagari" or "latin". Used for fonts and the purity checks."""
    found = voice(code)
    return found.script if found else "latin"


def default_voice() -> str:
    return VOICES[0].code


def dump() -> dict[str, Any]:
    """The whole catalogue, for the API and the app."""
    return {"voices": [lang.to_dict() for lang in VOICES],
            "captions": [lang.to_dict() for lang in CAPTIONS],
            "default_voice": default_voice()}

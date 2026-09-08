"""Visual provider contract + image conditioning.

Every asset that enters the pipeline carries source and licence information
(spec section 13).  Nothing is ever taken from another creator's video.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageFilter

from ..core.models import Asset


@dataclass
class VisualRequest:
    scene_index: int
    prompt: str
    keywords: list[str]
    width: int = 1080
    height: int = 1920
    style: str = ""
    seed: int = 0
    made_for_kids: bool = False
    # How long this scene is on screen. Only the video provider uses it, to
    # avoid picking a 2-second clip for an 8-second scene and looping it
    # visibly.
    min_seconds: float = 0.0
    # Appearance of the people in THIS scene, e.g. "RAJU is a 10-year-old boy
    # with short black hair in a green shirt". Only the AI provider uses it,
    # and only for characters the scene actually mentions - sending the whole
    # cast every time buries the scene description and the model starts
    # drawing a character line-up instead of a story beat.
    characters: str = ""


class VisualProvider(Protocol):
    name: str
    license_note: str

    def available(self) -> bool: ...

    def fetch(self, req: VisualRequest, out_path: Path) -> Asset: ...


# --------------------------------------------------------------------------
# Conditioning: cover-crop to the exact frame, then sharpen.
# --------------------------------------------------------------------------
# How much larger than the frame a still is rendered, so a Ken Burns zoom has
# real pixels to pan into rather than magnifying as it moves. Shared with
# engine/video/compose.py, which crops back to this same size - the two were
# separate literals and rounded differently.
OVERSIZE = 1.18


def condition_image(path: Path, width: int, height: int, *,
                    sharpen: bool = True) -> Path:
    """Make any downloaded/generated image render-ready.

    Providers return arbitrary sizes (Pollinations caps around 576x1024, stock
    photos can be 6000px wide).  We cover-crop to the target aspect so nothing
    is letterboxed, upscale with LANCZOS, then unsharp-mask.  The sharpening
    matters: an AI image upscaled ~2x looks soft once Ken Burns zoom is applied
    on top of it.
    """
    with Image.open(path) as raw:
        img = raw.convert("RGB")
        src_w, src_h = img.size
        target_ratio = width / height
        src_ratio = src_w / src_h

        # Cover crop
        if src_ratio > target_ratio:
            new_w = int(src_h * target_ratio)
            left = (src_w - new_w) // 2
            img = img.crop((left, 0, left + new_w, src_h))
        elif src_ratio < target_ratio:
            new_h = int(src_w / target_ratio)
            # Bias slightly above centre: subjects usually sit in the upper half.
            top = int((src_h - new_h) * 0.38)
            img = img.crop((0, top, src_w, top + new_h))

        # Render at 1.18x the frame so Ken Burns zoom has real pixels to pan
        # into. `& ~1` matches compose.py exactly: it rounds the same numbers
        # down to even, and a one-pixel disagreement made ffmpeg silently drop
        # a row on every still.
        render_w = int(width * OVERSIZE) & ~1
        render_h = int(height * OVERSIZE) & ~1

        # Measured against the RENDER width, not the frame width.
        #
        # This was `width / img.size[0]`, which ignores the 1.18 oversize and
        # so understated the real magnification by 15%: a 576x1024 source -
        # what the keyless generator actually returns however large an image
        # you ask it for - is blown up 2.21x to fill a 1274x2265 buffer, while
        # this reported 1.875. The sharpening below is compensation for exactly
        # that factor, so it was being tuned off the wrong number.
        upscale_factor = render_w / max(img.size[0], 1)
        img = img.resize((render_w, render_h), Image.LANCZOS)

        if sharpen and upscale_factor > 1.05:
            # Capped at 110, not 180.
            #
            # A 768x1344 generated image into the 1274x2265 oversize buffer is
            # a 1.66x magnification, and 90*1.66 = 149% unsharp haloes the ink
            # outlines that illustrated styles are built on - then zoompan
            # resamples every frame again on top of it. The old 180 ceiling
            # was tuned for the keyless backend's 2.21x upscale from a
            # heavily-compressed 576px source, where over-sharpening was
            # covering for missing detail. There is real detail now.
            strength = min(110, int(90 * upscale_factor))
            img = img.filter(ImageFilter.UnsharpMask(radius=1.6, percent=strength, threshold=3))
        elif sharpen:
            img = img.filter(ImageFilter.UnsharpMask(radius=1.1, percent=60, threshold=3))

        img.save(path, "JPEG", quality=94, subsampling=1, optimize=True)
    return path


def is_valid_image(path: Path, min_bytes: int = 4000) -> bool:
    try:
        if not path.exists() or path.stat().st_size < min_bytes:
            return False
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False

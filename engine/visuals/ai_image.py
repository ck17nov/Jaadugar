"""AI text-to-image scenes: illustrated storytelling, not stock photography.

This is the provider that changes what the videos look like. Stock libraries
can supply a photograph of a sugarcane field; they cannot supply "the same
three children walking past that field, in the same drawing style, for three
hundred consecutive shots". That is what an illustrated story channel is made
of, and it is the whole gap between a narrated slideshow and something people
watch for half an hour.

Three things here were learned by getting them wrong first:

1. STYLE IS NOT A DECORATION. The previous AI path appended ", highly detailed,
   sharp focus, professional photography" to every prompt. Asking for an anime
   cel-shaded illustration and professional photography in the same breath gets
   neither - the model splits the difference into the soft airbrushed look that
   reads as "AI slop". The style string now comes from the template and nothing
   is bolted on that could contradict it.

2. THE SCENE MUST LEAD. Putting a long style block and a full character sheet
   first, with the scene trailing behind, produced the SAME IMAGE for two
   completely different scenes: a large fixed preamble dominates and the model
   treats the tail as noise. Measured directly - two prompts differing only in
   their scene text returned byte-identical images. The scene leads and the
   style is a short suffix.

   Tested afterwards, and worth recording: with a per-scene seed, moving a
   SHORT style tag to the front changes very little on the current backend.
   Prompt order was never the whole story - see the note on backends below.

3. VARIETY HAS TO BE VERIFIED, NOT ASSUMED. Even with per-scene seeds, a
   generator asked for eleven variations of "a quiet village morning" will
   sometimes return the same picture twice. Duplicates are detected with a
   perceptual hash and regenerated with a different seed, because two identical
   shots in a row look like a broken render.

4. THE BACKEND SETS THE CEILING, NOT THE PROMPT. The keyless endpoint now
   offers exactly one model (it reports `["sana"]`; `flux` is accepted and
   silently mapped). Sana has a strong painterly house style, and no prompt
   phrasing tested here got clean cel-shaded anime line art out of it - three
   prompt shapes on a fixed seed all returned the same soft painted look. That
   is a large improvement on stock photography for illustrated storytelling and
   it is NOT the flat-colour anime of a Ghibli-style channel.

   Confirmed by swapping the backend: the same prompt through FLUX.1-schnell
   returns clean cel-shaded artwork with bold outlines and coherent faces. The
   prompt was never the problem. So the HTTP call sits behind a small backend
   interface that `visuals.ai_image_backend` selects - and the better one is
   metered, seven images on a free account before a 402, against the ~300 a
   half-hour video needs.

Licensing: images are generated from our own prompts. No third-party rights are
claimed and nothing is taken from another creator (spec section 13).
"""
from __future__ import annotations

import threading
import time
import urllib.parse
from pathlib import Path

import httpx
from PIL import Image

from ..core.logging import log_event
from ..core.models import Asset
from .base import VisualRequest, condition_image, is_valid_image

_UA = {"User-Agent": "AutoTubeAI/0.1 (+https://github.com/local/autotube-ai)"}

# Artefacts that ruin a frame regardless of style. Deliberately short: every
# extra clause dilutes the scene description, which is the part that matters.
_CONSTRAINTS = "no text, no watermark, no captions, no signature"

# Child-directed, and phrased for the failure that actually happened.
#
# This was "gentle, friendly, nothing frightening", and a bedtime-story prompt
# came back as an adult woman in a slip dress. The generator will not honour a
# vague reassurance, so the subject and the clothing are now stated outright.
# It is still the character bible's explicit ages that do most of the work.
_KIDS_CONSTRAINTS = ("gentle, friendly, nothing frightening, suitable for "
                     "young children, characters fully and modestly clothed, "
                     "no adult themes")

# Fallback when the template supplies no style. Illustration rather than
# photography: an unstyled AI photograph looks worse than real stock, whereas
# an unstyled AI illustration is at least coherent.
_DEFAULT_STYLE = ("2D illustration, clean line art, flat colours, "
                  "soft natural light")

# For backends that accept a negative prompt. Every item here is something
# that was actually observed in output and could not be removed by asking
# nicely in the positive prompt: watermarks, mangled hands, and the soft
# airbrushed look that reads as machine-made.
# MEASURED, not guessed. An earlier version of this list ended with
# "airbrushed, oversaturated", and those two words cost real quality: at a
# fixed seed and 20 steps, dropping them took mean saturation from 98 to 107
# and contrast (RGB stdev) from 45 to 53. Asking a model not to oversaturate
# is asking it to wash the picture out, which is what it did.
#
# Everything left names a DEFECT rather than a degree.
_NEGATIVE = ("watermark, signature, text, caption, logo, extra limbs, "
             "extra fingers, deformed hands, deformed face, blurry, "
             "low resolution, duplicated subject, two heads, cropped head")


def average_hash(path: Path, size: int = 8) -> int:
    """Perceptual hash: one bit per cell, set when the cell is above the mean.

    Cheap and good enough for the only question being asked - "is this the same
    picture as one we already have" - while tolerating the JPEG differences
    that make byte comparison useless.
    """
    with Image.open(path) as raw:
        small = raw.convert("L").resize((size, size), Image.LANCZOS)
        pixels = list(small.getdata())
    mean = sum(pixels) / len(pixels)
    bits = 0
    for i, value in enumerate(pixels):
        if value > mean:
            bits |= 1 << i
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _as_float(text: str | None) -> float:
    """A header value as a number, or 0.0. Never raises: a malformed
    accounting header must not lose an image that already arrived."""
    try:
        return max(0.0, float(str(text).strip()))
    except (TypeError, ValueError):
        return 0.0


def _trim_bottom(path: Path, fraction: float) -> None:
    """Cut a strip off the bottom of an image, in place.

    For provider watermarks. Re-saved at high quality because the result is
    about to be upscaled several times over and this is not the place to add
    another generation of JPEG loss.
    """
    fraction = max(0.0, min(fraction, 0.2))
    if fraction <= 0:
        return
    try:
        with Image.open(path) as raw:
            img = raw.convert("RGB")
            keep = int(img.height * (1.0 - fraction))
            if keep < 16:
                return
            img.crop((0, 0, img.width, keep)).save(
                path, "JPEG", quality=95, subsampling=1)
    except Exception as exc:                    # noqa: BLE001
        # A failed cosmetic crop must not lose the image.
        log_event("VISUAL", "could not trim the provider watermark",
                  error=str(exc)[:120])


class PollinationsBackend:
    """Keyless and free, and the LAST RESORT rather than the default.

    Measured against the live anonymous endpoint on 2026-09-08, because the
    images it returns were the standing complaint about this app and it was
    worth knowing exactly what it can and cannot do:

    1. HARD 0.59 MEGAPIXEL CAP. It honours the aspect ratio of the size you
       ask for and then scales to a fixed 589,824-pixel budget: 1080x1920 came
       back 576x1024, 1024x1024 came back 768x768, 832x1216 came back 635x928.
       A 1080x1920 frame is 2.07 MP, so every image is upscaled at least 3.5x
       by area - 4.9x once the Ken Burns oversize is included. That is the
       whole of the "face is blurry" complaint and no filter can undo it.

    2. THE `model` PARAMETER IS IGNORED. flux, sana, turbo, flux-realism,
       flux-anime, sdxl and dreamshaper were requested with one prompt and one
       seed and returned SEVEN BYTE-IDENTICAL images (sha256 b691f0b7ca2f16e0).
       There is no model choice on this tier, so `pollinations_model` below is
       decorative.

    3. IT IGNORES ART DIRECTION. Asked for "flat 2D cel-shaded illustration,
       thick clean outlines, flat colour fills, no gradients, no photorealism"
       it returned soft airbrushed semi-realism, which is what it returns for
       everything. The style suffix a template writes cannot reach it.

    4. IT IGNORES `nologo=true` AND "no watermark". Output carries a watermark
       in the bottom-right corner, which is why `watermark_bottom` exists.

    5. IT DISREGARDS THE CHILD-DIRECTED CONSTRAINTS. A bedtime-story prompt
       naming a small girl returned an adult woman in a slip dress. For kids
       content that is a safety failure, not a quality one - so the character
       bible's explicit ages are load-bearing here, not a nicety.

    Kept because it needs no key and no card, and a soft picture beats no
    video. But `build_backend` now prefers anything else that is configured.
    """

    id = "pollinations"
    label = "Pollinations (keyless)"
    # One at a time, measured: at two simultaneous requests this endpoint
    # served one image and refused the other, and on a 71-scene job it
    # refused about half of everything sent.
    max_parallel = 1
    # Fraction of the frame height to discard before conditioning, to remove
    # the watermark this endpoint applies whatever you ask for. Small: the
    # stamp sits in the last ~2% of the frame.
    watermark_bottom = 0.025

    def __init__(self, model: str = "sana", timeout: int = 120):
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        return True

    def fetch(self, prompt: str, *, width: int, height: int, seed: int) -> bytes:
        url = ("https://image.pollinations.ai/prompt/"
               + urllib.parse.quote(prompt, safe="")
               + f"?width={width}&height={height}"
               + f"&nologo=true&model={self.model}&seed={seed}")
        with httpx.Client(timeout=self.timeout, headers=_UA,
                          follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            if "image" not in resp.headers.get("content-type", ""):
                raise RuntimeError("pollinations: response was not an image")
            return resp.content


class CreditExhausted(RuntimeError):
    """The paid quota is gone. Retrying costs time and changes nothing."""


# Same handling, different cause: a free daily allowance that has run out
# rather than money. Both mean "stop asking this backend today".
QuotaExhausted = CreditExhausted


class HuggingFaceBackend:
    """Runs a model that actually draws what you ask for.

    This is the path to the flat-colour illustration the keyless endpoint will
    not produce. Verified: FLUX.1-schnell returns clean cel-shaded artwork with
    bold outlines and coherent faces, in 3 to 12 seconds - the class of image an
    illustrated story channel is made of.

    Two hard-won details about the API:

      * `api-inference.huggingface.co` NO LONGER EXISTS. It does not even
        resolve in DNS. The first version of this class posted to it and would
        never have worked. Serving moved to Inference Providers, routed through
        router.huggingface.co, and the model is dispatched to a third party
        (nscale, fal-ai, replicate, wavespeed). Each provider has its own
        request and response shape, which is why this delegates routing to
        huggingface_hub rather than reimplementing it.

      * IT IS NOT FREE AT VOLUME. A free account gets a small monthly credit
        for Inference Providers - measured here as seven images before a 402,
        against the ~300 a thirty-minute video needs. So 402 is raised as
        CreditExhausted, which the provider treats as terminal for the whole
        job instead of retrying it three times per scene.

    huggingface_hub is imported lazily so a deployment without it degrades to
    keyless generation rather than failing to start.
    """

    id = "huggingface"
    # Untested at concurrency, and its free credit runs out after ~7 images
    # anyway, so there is nothing to gain by pushing it.
    max_parallel = 2

    label = "Hugging Face Inference Providers"

    def __init__(self, model: str, token: str, timeout: int = 180):
        self.model = model
        self.token = token
        self.timeout = timeout
        self._client = None

    def available(self) -> bool:
        if not (self.token and self.model):
            return False
        try:
            import huggingface_hub                       # noqa: F401
        except ImportError:
            log_event("VISUAL", "huggingface_hub not installed",
                      hint="pip install huggingface_hub")
            return False
        return True

    def _client_or_build(self):
        if self._client is None:
            from huggingface_hub import InferenceClient
            # provider="auto" picks whichever third party is currently serving
            # the model. Pinning one would break the day it goes offline, and
            # they do: FLUX.1-schnell reports `together` as errored right now.
            self._client = InferenceClient(api_key=self.token, provider="auto",
                                           timeout=self.timeout)
        return self._client

    def fetch(self, prompt: str, *, width: int, height: int, seed: int) -> bytes:
        import io
        try:
            image = self._client_or_build().text_to_image(
                prompt, model=self.model, width=width, height=height, seed=seed)
        except Exception as exc:
            text = str(exc)
            if "402" in text or "Payment Required" in text:
                raise CreditExhausted(
                    "huggingface: inference credit exhausted (402). Add credit, "
                    "subscribe to PRO, or set visuals.ai_image_backend back to "
                    "pollinations.") from exc
            raise
        buf = io.BytesIO()
        image.save(buf, "PNG")
        return buf.getvalue()


class GeminiImageBackend:
    """Google's own image models, on the key this project already has.

    Worth trying before paying anyone: GEMINI_API_KEY is already configured
    for script writing, and the same key lists image models
    (gemini-3.1-flash-image, gemini-2.5-flash-image, nano-banana-pro and
    friends). If they are usable on the free tier, illustrated video costs
    nothing and needs no new account.

    Whether they ARE usable free is genuinely unresolved. Google stopped
    publishing per-tier limits - the rate-limit page now says to look in AI
    Studio - and the quota IDs that come back on a 429 are named "-FreeTier",
    which is suggestive but not proof. Testing it was inconclusive because the
    project's daily text quota was already spent on script generation, so
    everything returned 429 regardless of model.

    So this is written to find out by itself: put it first in the chain and it
    either works or raises QuotaExhausted, which degrades to the keyless
    backend the same way a Hugging Face 402 does. No decision needed in
    advance, and nothing to undo if the answer is no.
    """

    id = "gemini"
    # Free-tier image quota is 0, so this never runs unattended; no reason
    # to risk a rate limit on the paid path either.
    max_parallel = 1

    label = "Google Gemini image models"
    ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/"

    def __init__(self, model: str, key: str, timeout: int = 180):
        self.model = model
        self.key = key
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.key and self.model)

    def fetch(self, prompt: str, *, width: int, height: int, seed: int) -> bytes:
        import base64
        # No width/height parameter exists: the model is told the shape in
        # words and returns its own size, which condition_image then
        # cover-crops to the frame.
        aspect = "9:16" if height > width else "16:9"
        shape = ("vertical 9:16 portrait composition" if height > width
                 else "horizontal 16:9 widescreen composition")
        body = {
            "contents": [{"parts": [{"text": f"{prompt}. {shape}."}]}],
            "generationConfig": {
                "candidateCount": 1,
                # Ask for the aspect ratio as a PARAMETER, not only in prose.
                #
                # Prose alone got a square image, which the cover-crop then
                # trimmed to 9:16 - keeping about a third of the frame and
                # magnifying whatever survived. That is the "looks stretched
                # and the face is blurry" complaint: the renderer was correct
                # and the source was the wrong shape.
                "imageConfig": {"aspectRatio": aspect},
            },
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.ENDPOINT + self.model + ":generateContent",
                               params={"key": self.key}, json=body)
        if resp.status_code == 429:
            raise QuotaExhausted(
                "gemini: image quota exhausted (429). The Gemini API free tier "
                "may not include image generation at all - set "
                "visuals.ai_image_backend to pollinations or huggingface.")
        resp.raise_for_status()
        payload = resp.json()
        candidates = payload.get("candidates") or []
        if not candidates:
            raise RuntimeError(
                f"gemini: no candidate returned ({str(payload)[:200]})")
        for part in candidates[0].get("content", {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                return base64.b64decode(blob["data"])
        # A refusal comes back as TEXT rather than an error, so say which.
        reason = candidates[0].get("finishReason", "")
        text = " ".join(p.get("text", "") for p in
                        candidates[0].get("content", {}).get("parts", []))
        raise RuntimeError(
            f"gemini: reply contained no image (finishReason={reason}) "
            f"{text[:160]}")


class CloudflareBackend:
    """Workers AI: a real free daily allowance, and FLUX.

    The reason to prefer this over the other free options: Cloudflare gives
    every account 10,000 neurons a day at no cost and no card, and the catalogue
    includes FLUX.1 schnell - the model that actually draws clean cel-shaded
    illustration rather than the painterly blur the keyless endpoint produces.

    The daily allowance in real terms, since the headline "230 images" assumes
    512x512: flux-1-schnell costs about 4.8 neurons per 512x512 tile plus about
    9.6 per step, so a 1024x1024 frame is four tiles. At the documented default
    of 4 steps that is roughly 170 images a day - about 20 Shorts, or half a
    thirty-minute video. Every extra step costs ~10 neurons, which is why the
    default here is 4 rather than something higher.

    Three API details decide the code:

      * flux-1-schnell answers with JSON - {"result": {"image": "<base64>"}} -
        NOT raw image bytes, unlike the stable-diffusion models on the same
        endpoint. Both shapes are handled, because the model is configurable
        and getting this wrong looks like a corrupt download.

      * It has no width or height parameter and returns a SQUARE image. That
        matters: a square cover-cropped into 9:16 keeps a third of the frame
        and magnifies whatever survives, which is exactly the "stretched, and
        the face is blurry" complaint. `stable-diffusion-xl-base-1.0` on the
        same account does accept width and height, so the model is a config
        key rather than a constant.

      * It has no SEED parameter either. The per-scene seed cannot be honoured
        on flux, so a rerun will not reproduce the same pictures - unlike every
        other backend here. Retries still produce something different, because
        unseeded generation is random, which is what the duplicate check needs.
    """

    id = "cloudflare"
    label = "Cloudflare Workers AI"
    # Four at a time, measured: four simultaneous 768x1344 requests at 20
    # steps ALL returned 200, in 35.5s of wall time against roughly 76s for
    # the same four run one after another. Individual requests slow from ~19s
    # to ~35s as capacity is shared, so throughput doubles rather than
    # quadruples - but nothing is refused, which is the difference between a
    # real serverless API and the keyless endpoint that refuses half.
    max_parallel = 4
    BASE = "https://api.cloudflare.com/client/v4/accounts/"

    # SDXL IS UNMETERED, MEASURED. Every response carries a
    # `cf-ai-neurons` header giving what that request cost, and the header is
    # real: flux-1-schnell reports 172.80 for one image and llama-3.1-8b
    # reports 0.45 for a short completion. SDXL reports 0.00, repeatedly, for
    # a 768x1344 image at 20 steps - which agrees with its absence from the
    # published pricing table.
    #
    # So the daily 10,000-neuron allowance is not the binding constraint on
    # image generation that it appears to be, and a 145-image long-form video
    # is affordable where 145 flux images (25,000 neurons) would be two and a
    # half days of allowance. This is a second, independent reason to prefer
    # SDXL over flux here - the first being that flux has no width/height and
    # returns a square the cover crop cuts back to the free backend's
    # resolution.
    #
    # "Unmetered today" is not a promise, which is why `auto` and the
    # fallback chain exist. Watch the header rather than assuming.

    # Models that honour width/height. Everything else returns a square.
    SIZED_MODELS = ("stable-diffusion-xl-base-1.0", "stable-diffusion-xl-lightning",
                    "dreamshaper-8-lcm", "stable-diffusion-v1-5")

    # Models that take `num_steps` (max 20) rather than flux's `steps` (max 8).
    STEP_KEY_NUM = SIZED_MODELS

    # SDXL's TRAINED ASPECT BUCKETS, and the reason this is not just
    # "ask for 1080x1920".
    #
    # The documented range is 256-2048 per side, so 1080x1920 is accepted. But
    # SDXL was trained at 1024x1024 and on a fixed set of aspect buckets, and
    # generating far outside them is what produces the two-headed,
    # duplicated-subject frames the model is notorious for at tall aspects.
    # 768x1344 is a real bucket, is 0.571 against the 0.5625 a 9:16 frame
    # wants - close enough that the cover crop takes under 2% - and is 1.03 MP
    # against the keyless backend's 0.59. That halves the upscale without
    # asking the model for a shape it cannot draw.
    BUCKETS = ((768, 1344), (832, 1216), (1024, 1024), (1216, 832), (1344, 768))

    def __init__(self, account_id: str, token: str,
                 model: str = "@cf/stabilityai/stable-diffusion-xl-base-1.0",
                 steps: int = 8, timeout: int = 120):
        self.account_id = account_id
        self.token = token
        self.model = model
        # Clamped to whatever THIS model allows: flux-1-schnell is a distilled
        # model and stops at 8, SDXL takes up to 20. Neurons are charged per
        # step (9.6 of the daily 10,000 each on flux), so the default is the
        # low end of useful rather than the ceiling.
        self.steps = max(1, min(20 if self._num_steps_model(model) else 8,
                                int(steps)))
        self.timeout = timeout
        # Neurons charged for the last request, and the running total for this
        # process. See the note where they are read.
        self.last_neurons = 0.0
        self.spent_neurons = 0.0

    @staticmethod
    def _num_steps_model(model: str) -> bool:
        return any(name in model for name in CloudflareBackend.STEP_KEY_NUM)

    def _bucket(self, width: int, height: int) -> tuple[int, int]:
        """The trained bucket closest in aspect to the frame we are filling."""
        target = width / max(height, 1)
        return min(self.BUCKETS, key=lambda wh: abs(wh[0] / wh[1] - target))

    def available(self) -> bool:
        return bool(self.account_id and self.token and self.model)

    @property
    def supports_size(self) -> bool:
        return any(name in self.model for name in self.SIZED_MODELS)

    def fetch(self, prompt: str, *, width: int, height: int, seed: int) -> bytes:
        import base64
        body: dict = {"prompt": prompt}
        if self._num_steps_model(self.model):
            body["num_steps"] = self.steps
        else:
            body["steps"] = self.steps
        if self.supports_size:
            # Snap to a trained bucket rather than passing the frame size
            # through. This used to send min(1024, ...), which turned a
            # 1080x1920 request into 1024x1024 - a square, which the cover
            # crop then cut back to 576x1024. That is EXACTLY the resolution
            # the keyless backend gives away for free, so the paid-for
            # upgrade bought nothing. Found by reading the model card: the
            # real ceiling is 2048, not 1024.
            body["width"], body["height"] = self._bucket(width, height)
            body["seed"] = seed
            # The one reliable lever on this model, and the keyless backend
            # has no equivalent: state what must NOT appear. Watermarks and
            # photo-realism are both things the prompt alone failed to
            # suppress elsewhere.
            body["negative_prompt"] = _NEGATIVE
        url = f"{self.BASE}{self.account_id}/ai/run/{self.model}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=body, headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"})

        # What that request actually cost, straight from Cloudflare.
        #
        # Recorded rather than assumed. SDXL measures 0.00 today while flux
        # measures 172.80, and the whole "a long-form video is affordable"
        # conclusion rests on that. If Cloudflare starts metering SDXL this is
        # where it will show up first - as a rising number in the logs, rather
        # than as an unexplained 429 halfway through a 145-image render.
        self.last_neurons = _as_float(resp.headers.get("cf-ai-neurons"))
        if self.last_neurons > 0:
            self.spent_neurons += self.last_neurons
            log_event("VISUAL", "cloudflare charged neurons for this image",
                      neurons=f"{self.last_neurons:.2f}",
                      job_total=f"{self.spent_neurons:.2f}",
                      daily_free=10000, model=self.model)

        if resp.status_code in (429, 402):
            raise QuotaExhausted(
                f"cloudflare: daily neuron allowance exhausted "
                f"({resp.status_code}). It resets at 00:00 UTC. "
                f"This process has spent {self.spent_neurons:.0f} of the "
                f"10,000 free neurons.")
        if resp.status_code == 401:
            raise RuntimeError(
                "cloudflare: token rejected (401). The API token needs the "
                "Workers AI permission and must belong to this account id.")
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "image" in content_type:
            return resp.content              # stable-diffusion style
        payload = resp.json()
        if not payload.get("success", True):
            errors = payload.get("errors") or []
            text = "; ".join(str(e.get("message", e)) for e in errors)
            # Cloudflare reports an exhausted allowance in the BODY with a 200
            # in some cases, so the message has to be inspected too.
            if "neuron" in text.lower() or "quota" in text.lower():
                raise QuotaExhausted(f"cloudflare: {text[:200]}")
            raise RuntimeError(f"cloudflare: {text[:220]}")
        image = (payload.get("result") or {}).get("image")
        if not image:
            raise RuntimeError(
                f"cloudflare: no image in the reply ({str(payload)[:200]})")
        return base64.b64decode(image)


def _cloudflare(cfg):
    return CloudflareBackend(
        account_id=cfg.secret("CLOUDFLARE_ACCOUNT_ID"),
        token=cfg.secret("CLOUDFLARE_API_TOKEN"),
        model=str(cfg.get("visuals.cloudflare_model",
                          "@cf/black-forest-labs/flux-1-schnell")),
        steps=int(cfg.get("visuals.cloudflare_steps", 6)))


def _huggingface(cfg):
    return HuggingFaceBackend(
        model=str(cfg.get("visuals.ai_image_model",
                          "black-forest-labs/FLUX.1-schnell")),
        token=cfg.secret("HF_API_TOKEN"))


def build_backend(cfg) -> object:
    """Pick a backend from config, falling back to the keyless one.

    Falls back rather than failing: a missing HF token should degrade to free
    generation, not stop the job.

    "auto" is the default and means "the best one that is actually
    configured", which exists because the keyless backend is measurably
    unfit (see PollinationsBackend) and the difference between an unusable
    video and a good one was a config line nobody had a reason to edit.
    Dropping CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN into the
    environment is now the whole of the upgrade.
    """
    wanted = str(cfg.get("visuals.ai_image_backend", "auto")).lower()

    if wanted in ("auto", ""):
        # Cloudflare only, and Hugging Face deliberately NOT in this chain.
        #
        # `available()` can see a token but cannot see whether any credit is
        # left behind it, and the HF Inference Providers free credit is a
        # one-off that this project has already spent - it answers 402 after
        # about seven images. Auto-selecting it means every job opens with a
        # guaranteed failed call before degrading. Naming "huggingface"
        # explicitly still works, for when there is credit to spend.
        for build in (_cloudflare,):
            try:
                backend = build(cfg)
            except Exception as exc:            # noqa: BLE001
                log_event("VISUAL", "could not construct an image backend",
                          error=str(exc)[:140])
                continue
            if backend.available():
                log_event("VISUAL", "auto-selected an image backend",
                          backend=backend.id, model=getattr(backend, "model", ""))
                return backend
        log_event("VISUAL", "no image credentials configured, falling back to "
                  "keyless generation - images will be soft and will ignore "
                  "the requested art style",
                  fix="set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN")
        return PollinationsBackend(
            model=str(cfg.get("visuals.pollinations_model", "sana")))

    if wanted == "cloudflare":
        backend = _cloudflare(cfg)
        if backend.available():
            log_event("VISUAL", "using Cloudflare Workers AI",
                      backend=backend.id, model=backend.model,
                      sized=backend.supports_size)
            return backend
        log_event("VISUAL", "Cloudflare credentials missing, using keyless "
                  "generation",
                  need="CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN")
    if wanted == "gemini":
        backend = GeminiImageBackend(
            model=str(cfg.get("visuals.gemini_image_model",
                              "gemini-3.1-flash-image")),
            key=cfg.secret("GEMINI_API_KEY"))
        if backend.available():
            log_event("VISUAL", "using Gemini image generation",
                      backend=backend.id, model=backend.model)
            return backend
        log_event("VISUAL", "no GEMINI_API_KEY, using keyless generation")
    if wanted == "huggingface":
        backend = _huggingface(cfg)
        if backend.available():
            log_event("VISUAL", "using paid image generation",
                      backend=backend.id, model=backend.model)
            return backend
        log_event("VISUAL", "huggingface unavailable, using keyless generation",
                  reason=("no HF_API_TOKEN" if not cfg.secret("HF_API_TOKEN")
                          else "huggingface_hub not installed"))
    return PollinationsBackend(
        model=str(cfg.get("visuals.pollinations_model", "sana")))


class AIImageProvider:
    """Free, keyless text-to-image with prompt discipline and dedup.

    Reality check (spec section 41): no account and no key, but rate limited
    and slow - measured 8 to 45 seconds per image, and slower under load. A
    thirty-minute video needs roughly three hundred images, so generation is
    measured in hours, not minutes. That is a scheduling fact, not a defect,
    and the caller renders overnight.
    """

    name = "ai_image"
    license_note = ("AI-generated from our own prompt - "
                    "no third-party rights claimed")

    # Two images closer than this are treated as the same picture. 8x8 hash,
    # so 64 bits total; 6 is tight enough to catch re-issues of the same
    # generation while ignoring compression noise.
    DUPLICATE_DISTANCE = 6

    def __init__(self, backend=None, max_attempts: int = 3,
                 retry_backoff: float = 4.0, fallback=None):
        self.backend = backend or PollinationsBackend()
        # Where to go when a paid backend runs out of credit. Injectable so it
        # can be exercised without reaching the network - hard-coding the
        # constructor meant the only way to test the swap was to make a real
        # request, which is exactly the kind of test that rots.
        self.fallback = fallback if fallback is not None else PollinationsBackend()
        self.max_attempts = max_attempts
        self.retry_backoff = retry_backoff
        self._seen: dict[int, int] = {}      # hash -> scene that produced it
        self._lock = threading.Lock()

    @property
    def max_parallel(self) -> int:
        """How many images this backend will serve at once.

        A property of the BACKEND, not of the deployment: the keyless endpoint
        refuses the second of any two simultaneous requests, while Cloudflare
        serves four without complaint. Hard-coding one number for both meant
        the good backend ran at the bad backend's speed.
        """
        return max(1, int(getattr(self.backend, "max_parallel", 1)))

    @property
    def model(self) -> str:
        return getattr(self.backend, "model", "")

    def available(self) -> bool:
        return bool(self.backend.available())

    # ------------------------------------------------------------------
    def build_prompt(self, req: VisualRequest) -> str:
        """Scene first, then who is in it, then how it should look.

        The order is the point. See the module docstring: a leading style or
        character block makes the model ignore the scene entirely.
        """
        parts = [req.prompt.strip() or ", ".join(req.keywords[:6])]
        characters = (req.characters or "").strip()
        if characters:
            parts.append(characters)
        # The scene prompt may already carry the art direction: the script
        # post-processor appends `profile.visual_style` to every
        # `visual_prompt` it did not already find it in. Saying it twice in
        # one prompt does not double the effect, it just pushes the scene
        # further from the front - which is the one thing the module docstring
        # says never to do.
        style = req.style.strip() or _DEFAULT_STYLE
        if style and style.lower() not in parts[0].lower():
            parts.append(style)
        parts.append(_CONSTRAINTS)
        if req.made_for_kids:
            parts.append(_KIDS_CONSTRAINTS)
        return ". ".join(p.rstrip(" .,") for p in parts if p.strip())

    def _seed_for(self, req: VisualRequest, attempt: int) -> int:
        """Deterministic per scene, different on every retry.

        Deterministic matters for reruns: regenerating a job after a crash
        should not silently reshuffle every visual. The attempt offset is large
        and prime-ish so a retry lands somewhere unrelated rather than on a
        neighbouring scene's seed.
        """
        base = req.seed or (req.scene_index + 1) * 7919
        return (base + attempt * 104729) % 2_147_483_647

    def _download(self, prompt: str, req: VisualRequest, seed: int,
                  out_path: Path) -> None:
        data = self.backend.fetch(prompt, width=req.width, height=req.height,
                                  seed=seed)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        # Some backends stamp a logo whatever the prompt and query string say.
        # Trimming it here rather than in condition_image keeps the knowledge
        # of WHICH backend watermarks with the backend that does it.
        trim = float(getattr(self.backend, "watermark_bottom", 0.0) or 0.0)
        if trim > 0:
            _trim_bottom(out_path, trim)

    def _claim(self, digest: int, scene_index: int) -> int | None:
        """Register a hash. Returns the clashing scene index, or None if new."""
        with self._lock:
            for known, owner in self._seen.items():
                if owner != scene_index and hamming(known, digest) <= self.DUPLICATE_DISTANCE:
                    return owner
            self._seen[digest] = scene_index
            return None

    def fetch(self, req: VisualRequest, out_path: Path) -> Asset:
        prompt = self.build_prompt(req)
        last_error: Exception | None = None

        for attempt in range(self.max_attempts):
            seed = self._seed_for(req, attempt)
            if attempt:
                # Back off before asking again.
                #
                # The free endpoint limits by concurrency, and three requests
                # fired inside fourteen seconds are all refused - which is how
                # an illustrated scene ended up falling through to a stock
                # PHOTOGRAPH, the one substitution this provider exists to
                # avoid. Waiting is cheaper than the wrong medium.
                time.sleep(self.retry_backoff * attempt)
            try:
                self._download(prompt, req, seed, out_path)
            except CreditExhausted as exc:
                # Terminal for the paid backend, but NOT for the job.
                #
                # Dropping the provider here would send every remaining scene
                # to `procedural`, because a template that prefers illustration
                # has stock removed from its chain - so one 402 would turn the
                # rest of the video into abstract shapes. Falling back to
                # keyless generation keeps the MEDIUM (a drawn scene) and only
                # loses fidelity, which is the smaller loss by a wide margin.
                #
                # Swapped once, under the lock, so twenty concurrent scenes do
                # not each rebuild the backend and re-log the warning.
                with self._lock:
                    if self.backend is not self.fallback:
                        log_event("VISUAL", "paid image credit exhausted, "
                                  "falling back to keyless generation",
                                  was=self.backend.id,
                                  now=self.fallback.id, error=str(exc)[:120])
                        self.backend = self.fallback
                last_error = exc
                continue
            except Exception as exc:            # network, HTTP, wrong type
                last_error = exc
                continue

            if not is_valid_image(out_path):
                last_error = RuntimeError("ai_image: file is not a valid image")
                continue

            digest = average_hash(out_path)
            clash = self._claim(digest, req.scene_index)
            if clash is not None and attempt < self.max_attempts - 1:
                # Same picture as an earlier scene. Reseed and ask again.
                log_event("VISUAL", "duplicate image, regenerating",
                          scene=req.scene_index, matches_scene=clash,
                          attempt=attempt + 1)
                continue

            condition_image(out_path, req.width, req.height, sharpen=True)
            log_event("VISUAL", "AI image generated", scene=req.scene_index,
                      backend=self.backend.id, model=self.model, seed=seed,
                      characters=bool(req.characters))
            return Asset(asset=out_path.name,
                         source=f"generated:{self.backend.id}",
                         license=self.license_note, prompt=prompt[:400],
                         scene_index=req.scene_index)

        # Name the cause. "no usable image after 3 attempts" is unactionable:
        # a rate limit, a cold model and a malformed prompt all read the same,
        # and they need completely different responses.
        detail = f": {str(last_error)[:160]}" if last_error else ""
        raise RuntimeError(
            f"ai_image: no usable image after {self.max_attempts} attempts"
            f"{detail}") from last_error

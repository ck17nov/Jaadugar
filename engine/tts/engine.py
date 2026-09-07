"""Voice generation orchestrator.

Synthesises each scene separately, conditions the audio, then reports exact
per-scene durations back to the caller so the video timeline is driven by the
real narration length rather than an estimate.
"""
from __future__ import annotations

from pathlib import Path

from ..core.config import Config
from ..core.logging import log_event
from ..core.models import Scene
from ..core.util import ensure_dir, ffmpeg_bin, probe_duration, retry, run
from .base import SceneAudio, VoiceSpec, WordMark
from .providers import build_providers, resolve_voice, trim_trailing_silence



def _lookup(table: dict, language: str) -> str:
    """Exact match first, then the base language.

    "en-IN" should find an Indian English voice if one is mapped, and fall
    back to "en" rather than to nothing.
    """
    if not table or not language:
        return ""
    if language in table:
        return str(table[language] or "")
    base = language.split("-")[0]
    return str(table.get(base, "") or "")


def _scale_rate(rate: str, factor: float) -> str:
    """Scale an edge-tts rate string such as "+8%" by a factor."""
    try:
        current = float(str(rate).strip().rstrip("%"))
    except ValueError:
        current = 0.0
    scaled = (100.0 + current) * factor - 100.0
    return f"{scaled:+.0f}%"


class VoiceEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        order = cfg.get("tts.provider_order", ["edge", "piper", "gtts"])
        self.providers = build_providers(list(order))

    # ------------------------------------------------------------------
    def voice_spec(self, language: str, style: str = "energetic",
                   gender: str | None = None) -> VoiceSpec:
        """Resolve a concrete voice for this language and gender.

        `gender` used to be recorded on the spec and then ignored, because the
        voice id came from a single female-only map - so asking for a male
        narrator produced the same female voice.

        `child` is only partly real: Microsoft ships a child voice for US
        English and for nothing else. Rather than name an id that does not
        exist and fail at synthesis, the other languages get the female voice
        pitched up and slowed slightly, which is child-friendly rather than a
        child. That is a deliberate, documented approximation.
        """
        wanted = (gender or self.cfg.get("tts.voice_gender", "female")).lower()
        voices = self.cfg.get("tts.voices", {}) or {}
        female = voices.get("female") or self.cfg.get("tts.voice_map", {}) or {}
        table = voices.get(wanted) or {}

        voice_id = _lookup(table, language)
        pitch = str(self.cfg.get("tts.pitch", "+0Hz"))
        rate = str(self.cfg.get("tts.rate", "+0%"))

        if not voice_id and wanted == "child":
            # No child voice for this language: approximate one.
            voice_id = _lookup(female, language)
            pitch = str(self.cfg.get("tts.child_pitch", "+18Hz"))
            rate = _scale_rate(rate, float(
                self.cfg.get("tts.child_rate_scale", 0.94)))
            log_event("TTS", "no child voice for this language, approximating",
                      language=language, voice=voice_id or "provider default",
                      pitch=pitch)
        if not voice_id:
            # Fall back to the female table so an unmapped language still gets
            # the right LANGUAGE, which matters far more than the gender.
            voice_id = _lookup(female, language)
            if voice_id and wanted != "female":
                log_event("TTS", "no voice for this gender, using the default",
                          language=language, wanted=wanted, voice=voice_id)

        return VoiceSpec(
            language=language,
            voice_id=voice_id,
            gender=wanted,
            rate=rate,
            pitch=pitch,
            style=style,
        )

    def can_change_rate(self, provider_names: set[str]) -> bool:
        """True only if EVERY provider that voiced a scene supports a rate change.

        The duration re-fit re-synthesises at a corrected speaking rate. gTTS
        has no rate parameter and Piper as invoked here has none either, so for
        those the re-fit is a silent no-op - it costs a full re-synthesis and
        returns an identical recording. All-or-nothing because a mixed result
        would leave part of the video corrected and part not, which is worse
        than leaving it alone and letting the duration check report it.
        """
        capable = {p.name for p in self.providers
                   if getattr(p, "supports_rate", False)}
        return bool(provider_names) and provider_names.issubset(capable)

    def synthesize_scenes(self, scenes: list[Scene], out_dir: Path,
                          spec: VoiceSpec) -> list[SceneAudio]:
        """One clip per scene. Falls back down the provider chain per scene."""
        ensure_dir(out_dir)
        results: list[SceneAudio] = []
        attempts = int(self.cfg.get("automation.max_retries", 3))

        for scene in scenes:
            text = (scene.narration or "").strip()
            if not text:
                continue
            target = out_dir / f"scene_{scene.index:02d}.wav"
            audio = self._synthesize_one(text, target, spec, attempts)
            audio.scene_index = scene.index
            audio.duration = trim_trailing_silence(audio.path)
            # Re-clamp any word mark that now sits past the trimmed end.
            audio.words = [
                WordMark(w.start, min(w.duration, max(audio.duration - w.start, 0.05)), w.text)
                for w in audio.words if w.start < audio.duration
            ]
            results.append(audio)
            log_event("TTS", f"scene {scene.index} voiced",
                      provider=audio.provider, seconds=f"{audio.duration:.2f}",
                      words=len(audio.words), exact=audio.exact_timing)
        if not results:
            raise RuntimeError("no narration produced - every scene was empty")
        return results

    def _synthesize_one(self, text: str, target: Path, spec: VoiceSpec,
                        attempts: int) -> SceneAudio:
        errors: list[str] = []
        for provider in self.providers:
            try:
                if not provider.available():
                    errors.append(f"{provider.name}: unavailable")
                    continue
                return retry(
                    lambda p=provider: p.synthesize(text, target, spec),
                    attempts=attempts,
                    backoff=float(self.cfg.get("automation.retry_backoff_seconds", 5)) / 4,
                    tag="TTS", what=f"{provider.name} synthesis")
            except Exception as exc:
                errors.append(f"{provider.name}: {str(exc)[:160]}")
                log_event("TTS", "provider failed, falling back",
                          provider=provider.name, error=str(exc)[:160])
        raise RuntimeError("all TTS providers failed -> " + " | ".join(errors))

    # ------------------------------------------------------------------
    def concat(self, clips: list[SceneAudio], out_path: Path,
               gap: float = 0.16) -> tuple[float, list[tuple[float, SceneAudio]]]:
        """Join scene clips with a short breath gap.

        Returns the total duration and the absolute start offset of each clip,
        which the caption and timeline builders use directly.
        """
        out_path.parent.mkdir(parents=True, exist_ok=True)
        offsets: list[tuple[float, SceneAudio]] = []
        cursor = 0.0
        inputs: list[str] = []
        filters: list[str] = []

        for i, clip in enumerate(clips):
            offsets.append((cursor, clip))
            inputs += ["-i", str(clip.path)]
            # Pad every clip except the last with `gap` seconds of silence.
            pad = gap if i < len(clips) - 1 else 0.0
            if pad > 0:
                filters.append(f"[{i}:a]apad=pad_dur={pad}[a{i}]")
            else:
                filters.append(f"[{i}:a]anull[a{i}]")
            cursor += clip.duration + pad

        concat_inputs = "".join(f"[a{i}]" for i in range(len(clips)))
        filter_complex = (";".join(filters) +
                          f";{concat_inputs}concat=n={len(clips)}:v=0:a=1[out]")
        run([ffmpeg_bin(), "-y", "-loglevel", "error", *inputs,
             "-filter_complex", filter_complex, "-map", "[out]",
             "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out_path)],
            timeout=900)
        total = probe_duration(out_path)
        log_event("TTS", "voice track assembled", seconds=f"{total:.2f}",
                  scenes=len(clips))
        return total, offsets

    def describe(self, spec: VoiceSpec) -> str:
        return resolve_voice(spec)

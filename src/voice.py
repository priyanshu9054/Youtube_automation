import json
import logging
import os
import re
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("voice")

# Piper runs the voice model locally, in this container. That is the whole point
# of using it: edge-tts reached Microsoft's endpoint over the network, and that
# endpoint returns 403 to Railway's datacenter IPs (verified -- the identical
# call with the identical client version succeeds from a residential IP). There
# is no network call here, so there is nothing left to block.
DEFAULT_VOICE = "en_US-ryan-medium"
VOICE_DIR = Path(os.environ.get("PIPER_VOICE_DIR", "voices"))

# edge-tts expressed pitch as an absolute offset on the fundamental. Piper has
# no pitch control at all, so the shift is applied afterwards with ffmpeg, which
# needs a ratio rather than an offset. Male narration voices sit near this F0,
# so -15Hz works out to roughly 15% down.
ASSUMED_F0_HZ = 100.0


@dataclass
class VoiceClip:
    audio_path: Path
    duration_seconds: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _audio_duration(path: Path) -> float:
    # 1. Try ffprobe if available
    if shutil.which("ffprobe"):
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return float(json.loads(result.stdout)["format"]["duration"])
        except Exception as e:
            log.debug("ffprobe failed (%s), falling back to ffmpeg", e)

    # 2. Fallback to ffmpeg
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    result = subprocess.run(
        [ffmpeg_bin, "-i", str(path)],
        capture_output=True,
        text=True,
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", result.stderr)
    if m:
        hours, minutes, seconds = m.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    raise RuntimeError(f"Could not determine audio duration for {path} using ffprobe or ffmpeg")


def _resolve_model(voice: str) -> Path:
    """Map the configured TTS_VOICE to a Piper .onnx model on disk.

    TTS_VOICE used to name an Azure neural voice ("en-US-ChristopherNeural"),
    which means nothing to Piper. The deployed service still has that old value
    in its variables and it cannot be edited from the repo, so an unrecognised
    name falls back to the default voice with a warning instead of failing the
    whole run -- a wrong-sounding narrator beats no video at all.
    """
    candidate = VOICE_DIR / f"{voice}.onnx"
    if candidate.exists():
        return candidate

    default = VOICE_DIR / f"{DEFAULT_VOICE}.onnx"
    if not default.exists():
        raise RuntimeError(
            f"No Piper voice model found. Looked for {candidate} and {default}. "
            f"The Dockerfile downloads {DEFAULT_VOICE} into {VOICE_DIR}; set "
            "PIPER_VOICE_DIR if the models live elsewhere."
        )

    log.warning(
        "TTS_VOICE=%r is not a Piper model in %s (it looks like an old edge-tts "
        "voice name); falling back to %s. Set TTS_VOICE to a Piper voice to "
        "silence this.",
        voice,
        VOICE_DIR,
        DEFAULT_VOICE,
    )
    return default


def _length_scale(rate: str) -> float:
    """Convert an edge-tts rate ("-12%") into a Piper length_scale.

    Rate is a speed multiplier; length_scale is its inverse, since stretching
    phonemes is what makes delivery slower. Unparseable values fall back to 1.0
    rather than raising -- this knob is cosmetic and must not break a run.
    """
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "")
    if not m:
        if rate:
            log.warning("Could not parse TTS_RATE=%r; using default pacing.", rate)
        return 1.0

    speed = 1.0 + float(m.group(1)) / 100.0
    if speed <= 0.1:
        log.warning("TTS_RATE=%r is implausibly slow; using default pacing.", rate)
        return 1.0
    return _clamp(1.0 / speed, 0.5, 2.0)


def _pitch_ratio(pitch: str) -> float:
    """Convert an edge-tts pitch ("-15Hz") into a resampling ratio for ffmpeg.

    Clamped so an aggressive config turns the narrator gravelly rather than
    unintelligible, and so the matching atempo stays inside its 0.5-2.0 range.
    """
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*Hz\s*", pitch or "", re.IGNORECASE)
    if not m:
        if pitch:
            log.warning("Could not parse TTS_PITCH=%r; leaving pitch unchanged.", pitch)
        return 1.0
    return _clamp(1.0 + float(m.group(1)) / ASSUMED_F0_HZ, 0.75, 1.35)


def _synthesize_wav(
    text: str, model: Path, wav_path: Path, length_scale: float, sentence_silence: float
) -> None:
    """Run the piper CLI as a subprocess.

    Piper is GPL-3.0, so it is invoked as a separate process rather than
    imported -- this repo is public and stays at arm's length from it.
    """
    cmd = [
        sys.executable,
        "-m",
        "piper",
        "--model",
        str(model),
        "--output-file",
        str(wav_path),
        "--length-scale",
        f"{length_scale:.3f}",
        "--sentence-silence",
        f"{max(0.0, sentence_silence):.2f}",
    ]
    result = subprocess.run(cmd, input=text, capture_output=True, text=True)
    if result.returncode != 0 or not wav_path.exists():
        raise RuntimeError(
            f"piper failed (exit {result.returncode}): "
            f"{(result.stderr or '').strip()[-500:] or 'no stderr'}"
        )


def _to_mp3(wav_path: Path, out_path: Path, pitch_ratio: float) -> None:
    """Convert Piper's WAV to the mp3 the rest of the pipeline expects.

    When a pitch shift is wanted, resample to move the pitch and then pull the
    duration back with atempo -- otherwise the clip length would change and the
    footage timing computed from it would drift.
    """
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [ffmpeg_bin, "-y", "-i", str(wav_path)]

    if abs(pitch_ratio - 1.0) >= 0.01:
        with wave.open(str(wav_path)) as wf:
            sample_rate = wf.getframerate()
        cmd += [
            "-af",
            f"asetrate={int(sample_rate * pitch_ratio)},"
            f"aresample={sample_rate},"
            f"atempo={1.0 / pitch_ratio:.6f}",
        ]

    cmd += ["-codec:a", "libmp3lame", "-q:a", "4", str(out_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(
            f"ffmpeg failed converting voice to mp3 (exit {result.returncode}): "
            f"{(result.stderr or '').strip()[-500:] or 'no stderr'}"
        )


def synthesize_sentence(
    text: str,
    voice: str,
    out_path: Path,
    rate: str = "-12%",
    pitch: str = "-15Hz",
    sentence_silence: float = 0.4,
) -> VoiceClip:
    # Slower than default + a lowered pitch gives the heavier, darker delivery
    # this niche wants — weighted and deliberate rather than announcer-bright.
    # The trailing silence gives the line room to land instead of running
    # straight into the next sentence's clip.
    model = _resolve_model(voice)
    wav_path = out_path.with_suffix(".piper.wav")
    try:
        _synthesize_wav(text, model, wav_path, _length_scale(rate), sentence_silence)
        _to_mp3(wav_path, out_path, _pitch_ratio(pitch))
    finally:
        wav_path.unlink(missing_ok=True)
    return VoiceClip(audio_path=out_path, duration_seconds=_audio_duration(out_path))


def _synthesize_batch_wavs(
    texts: list[str], model: Path, out_dir: Path, length_scale: float, sentence_silence: float
) -> list[Path]:
    """Synthesize every sentence in ONE piper process.

    Piper reads one line of stdin per utterance and writes one wav per line, so
    the 60MB model is loaded once instead of once per sentence. That reload was
    costing about 21s per sentence on the deployed instance -- roughly two
    minutes of a run -- and each process peaked near 200MB, which is the leading
    suspect for the container being killed mid-run.

    Paths come from piper's own "Wrote <path>" log lines, which are emitted in
    input order, rather than from sorting timestamped filenames.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # One line per sentence is the contract; an embedded newline would silently
    # split one sentence into two utterances and desynchronise every clip.
    payload = "\n".join(t.replace("\n", " ").replace("\r", " ").strip() for t in texts)

    cmd = [
        sys.executable, "-m", "piper",
        "--model", str(model),
        "--output-dir", str(out_dir),
        "--output-dir-naming", "timestamp",
        "--length-scale", f"{length_scale:.3f}",
        "--sentence-silence", f"{max(0.0, sentence_silence):.2f}",
    ]
    result = subprocess.run(cmd, input=payload, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"piper batch failed (exit {result.returncode}): "
            f"{(result.stderr or '').strip()[-500:] or 'no stderr'}"
        )

    written = re.findall(r"Wrote (.+\.wav)", result.stderr or "")
    paths = [Path(p.strip()) for p in written]
    if len(paths) != len(texts) or not all(p.exists() for p in paths):
        raise RuntimeError(
            f"piper batch wrote {len(paths)} files for {len(texts)} sentences"
        )
    return paths


def synthesize_sentences(
    texts: list[str],
    voice: str,
    out_dir: Path,
    rate: str = "-12%",
    pitch: str = "-15Hz",
    sentence_silence: float = 0.4,
) -> list[VoiceClip]:
    """Synthesize all sentences at once, falling back to one-at-a-time.

    The batch path is a large speed and memory win but depends on piper's
    stdout contract; if that ever changes, a slow run beats a broken one.
    """
    model = _resolve_model(voice)
    ratio = _pitch_ratio(pitch)

    try:
        wavs = _synthesize_batch_wavs(
            texts, model, out_dir / "piper", _length_scale(rate), sentence_silence
        )
    except Exception as e:
        log.warning("Batch synthesis failed (%s); falling back to per-sentence.", e)
        clips = []
        for i, text in enumerate(texts):
            clips.append(
                synthesize_sentence(
                    text, voice, out_dir / f"voice_{i:03d}.mp3", rate, pitch, sentence_silence
                )
            )
        return clips

    clips = []
    for i, wav in enumerate(wavs):
        mp3 = out_dir / f"voice_{i:03d}.mp3"
        _to_mp3(wav, mp3, ratio)
        wav.unlink(missing_ok=True)
        clips.append(VoiceClip(audio_path=mp3, duration_seconds=_audio_duration(mp3)))
    return clips

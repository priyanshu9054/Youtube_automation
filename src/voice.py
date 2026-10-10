import asyncio
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import edge_tts

log = logging.getLogger("voice")


@dataclass
class VoiceClip:
    audio_path: Path
    duration_seconds: float


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


async def _synthesize(
    text: str, voice: str, out_path: Path, rate: str, pitch: str
) -> None:
    # Slower than default + a lowered pitch gives the heavier, darker delivery
    # this niche wants — weighted and deliberate rather than announcer-bright.
    # -15Hz is about as low as this goes before it sounds synthetic.
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(str(out_path))


def synthesize_sentence(
    text: str,
    voice: str,
    out_path: Path,
    rate: str = "-12%",
    pitch: str = "-15Hz",
) -> VoiceClip:
    asyncio.run(_synthesize(text, voice, out_path, rate, pitch))
    return VoiceClip(audio_path=out_path, duration_seconds=_audio_duration(out_path))

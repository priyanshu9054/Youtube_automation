import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import edge_tts


@dataclass
class VoiceClip:
    audio_path: Path
    duration_seconds: float


def _ffprobe_duration(path: Path) -> float:
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


async def _synthesize(text: str, voice: str, out_path: Path) -> None:
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_path))


def synthesize_sentence(text: str, voice: str, out_path: Path) -> VoiceClip:
    asyncio.run(_synthesize(text, voice, out_path))
    return VoiceClip(audio_path=out_path, duration_seconds=_ffprobe_duration(out_path))

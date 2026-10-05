import subprocess
from dataclasses import dataclass
from pathlib import Path

TARGET_W = 1080
TARGET_H = 1920
# Installed via Dockerfile (fonts-dejavu-core). On macOS for local testing,
# override with a path to any .ttf you have, e.g. /System/Library/Fonts/Supplemental/Arial.ttf
DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


@dataclass
class Segment:
    clip_path: Path
    audio_path: Path
    text: str
    duration: float


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _normalize_clip(src: Path, duration: float, out_path: Path) -> None:
    """Scale/crop a source clip to 1080x1920, mute it, and trim/loop it to `duration`."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(src),
            "-t",
            str(duration),
            "-an",
            "-vf",
            f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},fps=30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )


def _concat(paths: list[Path], out_path: Path, list_file: Path) -> None:
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in paths))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )


def render_video(
    segments: list[Segment], work_dir: Path, out_path: Path, font_path: str = DEFAULT_FONT
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)

    normalized_clips = []
    for i, seg in enumerate(segments):
        norm_path = work_dir / f"clip_{i:03d}.mp4"
        _normalize_clip(seg.clip_path, seg.duration, norm_path)
        normalized_clips.append(norm_path)

    silent_video = work_dir / "video_silent.mp4"
    _concat(normalized_clips, silent_video, work_dir / "clips.txt")

    audio_concat = work_dir / "audio.mp3"
    _concat([seg.audio_path for seg in segments], audio_concat, work_dir / "audio.txt")

    # Burn one caption per segment, shown for that segment's time window.
    t = 0.0
    draws = []
    for seg in segments:
        start, end = t, t + seg.duration
        escaped = _escape_drawtext(seg.text)
        draws.append(
            f"drawtext=fontfile='{font_path}':text='{escaped}':"
            "fontcolor=white:fontsize=56:borderw=4:bordercolor=black:"
            "x=(w-text_w)/2:y=h-350:line_spacing=10:"
            f"enable='between(t,{start},{end})'"
        )
        t = end
    drawtext_filter = ",".join(draws)

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(silent_video),
            "-i",
            str(audio_concat),
            "-vf",
            drawtext_filter,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-shortest",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path

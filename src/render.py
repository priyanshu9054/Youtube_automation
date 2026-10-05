import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

TARGET_W = 1080
TARGET_H = 1920

DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CANDIDATE_FONTS = [
    Path(DEFAULT_FONT),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
    Path("/System/Library/Fonts/Helvetica.ttc"),
]


def resolve_font_path(font_path: str = DEFAULT_FONT) -> str:
    """Resolve an available system TTF font file for ffmpeg drawtext filter."""
    if font_path and Path(font_path).exists():
        return font_path
    env_font = os.environ.get("FONT_PATH")
    if env_font and Path(env_font).exists():
        return env_font
    for candidate in CANDIDATE_FONTS:
        if candidate.exists():
            return str(candidate)
    return font_path


@dataclass
class Segment:
    clip_path: Path
    audio_path: Path
    text: str
    duration: float


import textwrap


def _srt_timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    h, millis = divmod(millis, 3_600_000)
    m, millis = divmod(millis, 60_000)
    s, millis = divmod(millis, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def _write_srt(segments: list["Segment"], out_path: Path) -> Path:
    lines = []
    t = 0.0
    for i, seg in enumerate(segments, start=1):
        start, end = t, t + seg.duration
        wrapped = textwrap.fill(seg.text, width=32)
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        lines.append(wrapped)
        lines.append("")
        t = end
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _escape_filter_path(path: Path) -> str:
    # ffmpeg filtergraph argument escaping: colons separate filter options.
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _normalize_clip(src: Path, duration: float, out_path: Path) -> None:
    """Scale/crop a source clip to 1080x1920, mute it, and trim/loop it to `duration`."""
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run(
        [
            ffmpeg_bin,
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
            "ultrafast",
            "-threads",
            "1",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )


def _concat(paths: list[Path], out_path: Path, list_file: Path) -> None:
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in paths))
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run(
        [
            ffmpeg_bin,
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
    resolved_font = resolve_font_path(font_path)

    normalized_clips = []
    for i, seg in enumerate(segments):
        norm_path = work_dir / f"clip_{i:03d}.mp4"
        _normalize_clip(seg.clip_path, seg.duration, norm_path)
        normalized_clips.append(norm_path)

    silent_video = work_dir / "video_silent.mp4"
    _concat(normalized_clips, silent_video, work_dir / "clips.txt")

    audio_concat = work_dir / "audio.mp3"
    _concat([seg.audio_path for seg in segments], audio_concat, work_dir / "audio.txt")

    # One subtitles filter (libass) instead of N chained drawtext filters --
    # much lighter peak memory for the same visual result, which matters on
    # memory-constrained hosts (chained drawtext was getting OOM-killed).
    srt_path = _write_srt(segments, work_dir / "captions.srt")
    font_dir = str(Path(resolved_font).parent)
    subtitles_filter = (
        f"subtitles={_escape_filter_path(srt_path)}:fontsdir={_escape_filter_path(Path(font_dir))}:"
        "force_style='FontName=DejaVu Sans,Bold=1,FontSize=16,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=60'"
    )

    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(silent_video),
            "-i",
            str(audio_concat),
            "-vf",
            subtitles_filter,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-threads",
            "1",
            "-c:a",
            "aac",
            "-shortest",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path

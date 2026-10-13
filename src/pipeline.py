import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from google.auth.exceptions import RefreshError

from src.config import Config
from src.footage import download_clip, find_clip_url, generate_fallback_clip
from src.history import load_recent_titles, record_title
from src.music import download_music, find_music_url
from src.render import Segment, render_video
from src.script_writer import generate_script
from src.uploader import upload_short
from src.voice import synthesize_sentence
from src.youtube_client import build_youtube_client

log = logging.getLogger("pipeline")

# Google expires refresh tokens after 7 days while the OAuth consent screen is
# in "Testing", so this failure recurs weekly until the app is published. The
# raw google-auth error is just "invalid_grant", which says nothing about what
# to actually do, and a daily job that fails this way is easy to miss.
TOKEN_EXPIRED_HELP = (
    "YouTube rejected the refresh token (invalid_grant: expired or revoked). "
    "Re-mint it by running scripts/authorize_youtube.py, then update "
    "YOUTUBE_REFRESH_TOKEN in the Railway service variables -- updating .env "
    "alone does not change the deployed service. If the Google OAuth consent "
    "screen is still in Testing mode, tokens expire every 7 days; publish the "
    "app in Google Cloud Console to stop this from recurring."
)


@dataclass(frozen=True)
class RunResult:
    """Outcome of one pipeline run.

    `run_once` used to return just the rendered path, which made a published
    run and a render-only run indistinguishable to /status -- an upload could
    be skipped or fail for days without anything looking wrong.
    """

    video_path: Path
    video_id: Optional[str] = None
    uploaded: bool = False
    upload_skipped_reason: Optional[str] = None

    @property
    def video_url(self) -> Optional[str]:
        return f"https://youtu.be/{self.video_id}" if self.video_id else None


def run_once(cfg: Config, out_dir: Path, upload: bool) -> RunResult:
    recent_titles = load_recent_titles()

    log.info("Generating script with %s...", cfg.groq_model)
    script = generate_script(cfg, recent_titles)
    log.info("Topic: %s", script.title)
    record_title(script.title)

    with tempfile.TemporaryDirectory(prefix="ytauto_") as tmp:
        tmp_path = Path(tmp)
        segments = []

        for i, sentence in enumerate(script.sentences):
            log.info("Synthesizing voice for sentence %d/%d", i + 1, len(script.sentences))
            audio_path = tmp_path / f"voice_{i:03d}.mp3"
            voice_clip = synthesize_sentence(
                sentence.text,
                cfg.tts_voice,
                audio_path,
                rate=cfg.tts_rate,
                pitch=cfg.tts_pitch,
            )

            raw_clip_path = tmp_path / f"raw_{i:03d}.mp4"
            try:
                log.info("Finding footage for: %s", sentence.visual_keywords)
                clip_url = find_clip_url(
                    sentence.visual_keywords, cfg.pixabay_api_key, voice_clip.duration_seconds
                )
                log.info("Downloading footage clip %d/%d: %s", i + 1, len(script.sentences), clip_url)
                download_clip(clip_url, raw_clip_path)
            except Exception as e:
                log.warning("Stock clip fetch failed (%s); generating visual fallback.", e)
                generate_fallback_clip(voice_clip.duration_seconds, raw_clip_path, i)

            segments.append(
                Segment(
                    clip_path=raw_clip_path,
                    audio_path=audio_path,
                    text=sentence.text,
                    duration=voice_clip.duration_seconds,
                )
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        safe_title = re.sub(r"[^\w\s-]", "", script.title[:60]).strip().replace(" ", "_")
        if not safe_title:
            safe_title = "youtube_short"
        out_path = out_dir / f"{safe_title}.mp4"

        music_path = None
        if cfg.enable_music:
            try:
                log.info("Finding background music for mood: %s", script.music_mood)
                music_url = find_music_url(script.music_mood)
                music_path = tmp_path / "music.mp3"
                download_music(music_url, music_path)
            except Exception as e:
                log.warning("Background music fetch failed (%s); rendering without music.", e)
                music_path = None

        log.info("Rendering final video...")
        render_video(
            segments,
            tmp_path / "render",
            out_path,
            music_path=music_path,
            music_volume=cfg.music_volume,
        )

    if upload:
        log.info("Uploading to YouTube (privacy=%s)...", cfg.upload_privacy_status)
        try:
            youtube = build_youtube_client(cfg)
            video_id = upload_short(youtube, out_path, script, cfg.upload_privacy_status)
        except RefreshError as e:
            raise RuntimeError(TOKEN_EXPIRED_HELP) from e
        log.info("Uploaded successfully: https://youtu.be/%s", video_id)
        return RunResult(video_path=out_path, video_id=video_id, uploaded=True)

    reason = "AUTO_UPLOAD is off (or --upload was not passed)"
    log.info("%s; skipping upload.", reason)
    log.info("Rendered video saved at: %s", out_path)
    return RunResult(video_path=out_path, upload_skipped_reason=reason)

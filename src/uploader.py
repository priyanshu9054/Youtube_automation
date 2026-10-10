import logging
from pathlib import Path

from googleapiclient.discovery import Resource
from googleapiclient.http import MediaFileUpload

from src.script_writer import VideoScript

log = logging.getLogger("uploader")


def upload_short(
    youtube: Resource, video_path: Path, script: VideoScript, privacy_status: str
) -> str:
    title = script.title
    if "#Shorts" not in title and len(title) + 8 <= 100:
        title = f"{title} #Shorts"

    description = script.description
    if "#Shorts" not in description:
        description = f"{description}\n\n#Shorts"

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": list(set(script.tags + ["Shorts", "philosophy", "wisdom"])),
            "categoryId": "27",  # Education
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)

    log.info("Starting upload for %r...", title)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("Upload progress: %d%%", int(status.progress() * 100))

    video_id = response["id"]
    log.info("Upload finished! Video ID: %s", video_id)
    _verify_privacy(response, video_id, privacy_status)
    return video_id


def _verify_privacy(response: dict, video_id: str, expected: str) -> None:
    """Confirm YouTube actually applied `expected` to the finished upload.

    The insert response carries the `status` part we asked for, so this needs no
    extra request (and no `youtube.readonly` scope). YouTube can override the
    requested status — an unverified channel, a copyright or policy hold — which
    would otherwise leave a video sitting unseen.
    """
    status = response.get("status") or {}
    actual = status.get("privacyStatus")

    if actual == expected:
        log.info("Verified privacy status: %s", actual)
        return

    if actual is None:
        log.warning(
            "Upload response for %s carried no privacy status; "
            "confirm it is %s in YouTube Studio.",
            video_id,
            expected,
        )
        return

    log.warning(
        "Privacy mismatch for %s: requested %r but YouTube applied %r "
        "(uploadStatus=%s, rejectionReason=%s). Check the video in YouTube Studio.",
        video_id,
        expected,
        actual,
        status.get("uploadStatus"),
        status.get("rejectionReason"),
    )

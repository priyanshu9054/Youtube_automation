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
    return video_id

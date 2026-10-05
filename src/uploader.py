from pathlib import Path

from googleapiclient.discovery import Resource
from googleapiclient.http import MediaFileUpload

from src.script_writer import VideoScript


def upload_short(
    youtube: Resource, video_path: Path, script: VideoScript, privacy_status: str
) -> str:
    body = {
        "snippet": {
            "title": script.title,
            "description": script.description,
            "tags": script.tags,
            "categoryId": "27",  # Education; adjust to your niche
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)

    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
    return response["id"]

import json
from pathlib import Path

DEFAULT_HISTORY_FILE = Path("data/posted_topics.json")


def load_recent_titles(history_file: Path = DEFAULT_HISTORY_FILE, limit: int = 25) -> list[str]:
    if not history_file.exists():
        return []
    titles = json.loads(history_file.read_text())
    return titles[-limit:]


def record_title(title: str, history_file: Path = DEFAULT_HISTORY_FILE) -> None:
    history_file.parent.mkdir(parents=True, exist_ok=True)
    titles = []
    if history_file.exists():
        titles = json.loads(history_file.read_text())
    titles.append(title)
    history_file.write_text(json.dumps(titles, indent=2))

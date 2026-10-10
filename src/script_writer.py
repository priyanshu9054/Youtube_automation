import json
import logging
import random
import time
from dataclasses import dataclass

from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

from src.config import Config
from src.music import ALLOWED_MOODS

log = logging.getLogger("script_writer")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_MAX_TOKENS = 600
MAX_RETRIES = 3


@dataclass
class Sentence:
    text: str
    visual_keywords: str


@dataclass
class VideoScript:
    title: str
    description: str
    tags: list[str]
    sentences: list[Sentence]
    music_mood: str


SYSTEM_PROMPT = """You write scripts for a faceless YouTube Shorts channel.

Hard rules:
- Original commentary/explanation only. Never lightly reword a single existing \
article or script you might have seen; synthesize the idea in your own structure.
- No clickbait that misrepresents the content ("you won't believe..." when you \
obviously will).
- Keep it factually careful: if unsure of a detail, phrase it as approximate \
rather than inventing false precision.
- Disclose nothing false; this is AI-written and AI-narrated, which is fine, but \
the content itself must be genuinely informative, not filler.

Respond with ONLY strict JSON matching this shape, no markdown fences, no prose:
{
  "title": "...",          // <= 90 chars, no ALL CAPS spam
  "description": "...",     // 2-3 sentences + 3-5 relevant hashtags
  "tags": ["...", "..."],
  "music_mood": "...",      // ONE word from the allowed mood list, matching this specific script's tone
  "sentences": [
    {"text": "...", "visual_keywords": "short stock-footage search phrase"}
  ]
}
"""


def generate_script(
    cfg: Config, recent_titles: list[str], client: OpenAI | None = None
) -> VideoScript:
    client = client or OpenAI(api_key=cfg.groq_api_key, base_url=GROQ_BASE_URL)

    avoid = (
        "Avoid repeating these topics already covered on the channel:\n- "
        + "\n- ".join(recent_titles)
        if recent_titles
        else "This is the channel's first video."
    )

    user_prompt = f"""Channel niche: {cfg.channel_niche}

{avoid}

Write a script for one YouTube Short, {cfg.sentence_count} sentences long. \
Each sentence should be short enough to speak in 3-5 seconds. Each needs a \
visual_keywords phrase suitable for searching stock footage (concrete, visual, \
not abstract).

Pick music_mood as whichever single word from this list best fits THIS \
script's specific tone and subject (not a generic default) \
- {", ".join(ALLOWED_MOODS)}"""

    model = getattr(cfg, "groq_model", DEFAULT_MODEL) or DEFAULT_MODEL
    max_tokens = getattr(cfg, "groq_max_tokens", DEFAULT_MAX_TOKENS) or DEFAULT_MAX_TOKENS

    response = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0.8,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            break
        except RateLimitError as e:
            if attempt == MAX_RETRIES:
                log.error("Groq rate limit exceeded after %d attempts: %s", MAX_RETRIES, e)
                raise
            wait_seconds = 10 * attempt
            if hasattr(e, "response") and e.response is not None:
                retry_after = e.response.headers.get("retry-after")
                if retry_after:
                    try:
                        wait_seconds = max(wait_seconds, int(float(retry_after)) + 1)
                    except ValueError:
                        pass
            log.warning(
                "Groq rate limit hit (attempt %d/%d). Retrying in %ds... (%s)",
                attempt,
                MAX_RETRIES,
                wait_seconds,
                e,
            )
            time.sleep(wait_seconds)
        except (APIConnectionError, InternalServerError) as e:
            if attempt == MAX_RETRIES:
                log.error("Groq connection/server error after %d attempts: %s", MAX_RETRIES, e)
                raise
            wait_seconds = 5 * attempt
            log.warning(
                "Groq transient error (attempt %d/%d). Retrying in %ds... (%s)",
                attempt,
                MAX_RETRIES,
                wait_seconds,
                e,
            )
            time.sleep(wait_seconds)

    raw_text = response.choices[0].message.content.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        raw_text = raw_text.removeprefix("json").strip()
    data = json.loads(raw_text)

    music_mood = str(data.get("music_mood", "")).strip().lower()
    if music_mood not in ALLOWED_MOODS:
        music_mood = random.choice(ALLOWED_MOODS)

    return VideoScript(
        title=data["title"],
        description=data["description"],
        tags=data.get("tags", []),
        music_mood=music_mood,
        sentences=[
            Sentence(text=s["text"], visual_keywords=s["visual_keywords"])
            for s in data["sentences"]
        ],
    )

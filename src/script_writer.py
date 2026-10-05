import json
from dataclasses import dataclass

from openai import OpenAI

from src.config import Config

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MODEL = "llama-3.3-70b-versatile"


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
not abstract)."""

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.8,
        max_tokens=2000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    raw_text = response.choices[0].message.content.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        raw_text = raw_text.removeprefix("json").strip()
    data = json.loads(raw_text)

    return VideoScript(
        title=data["title"],
        description=data["description"],
        tags=data.get("tags", []),
        sentences=[
            Sentence(text=s["text"], visual_keywords=s["visual_keywords"])
            for s in data["sentences"]
        ],
    )

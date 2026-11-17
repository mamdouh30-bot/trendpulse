"""Publishing metadata: CTR-optimized title, description, tags, hashtags."""
import json

from . import llm
from .utils import save_state

SEO_SYSTEM = (
    "You are a world-class YouTube growth strategist for cinematic documentary "
    "channels. You know exactly how MrBeast-style curiosity framing and "
    "documentary authority combine into titles that hit 8-12% CTR. You never "
    "promise what the video does not deliver. English only. Raw JSON only."
)


def generate(cfg, run, script, topic, facts, logger):
    srcs = facts.get("sources") or []
    src_lines = "\n".join(f"- {s.get('host','')}: {s.get('url','')}" for s in srcs[:8])
    chapters = [c for c in script.get("chapters", []) if c.get("num") not in (0, 99)]

    prompt = f"""Documentary film data:

TITLE: {script['title']}
TOPIC: {topic.get('title')}
ANGLE: {topic.get('angle', '')}
SUMMARY: {facts.get('summary', '')}
CHAPTERS: {json.dumps([c.get('title') for c in chapters], ensure_ascii=False)}
THUMBNAIL IMAGE SUBJECT: {topic.get('visual_world', '')}

TASK: produce upload metadata that maximizes CTR and search ranking.

Rules:
- title: max 65 characters, curiosity gap + authority, no ALL CAPS, no false claims.
- description: starts with a 2-line hook (first line < 55 chars, second line
  < 70 chars), then a blank line, then a 90-120 word film summary, then the
  chapter list as '00:00 Chapter' style placeholders without timestamps, then
  'Sources:' list, then exactly 3 hashtags. No emoji.
- tags: 14-18 search tags, mix broad + specific, no duplicates.
- thumbnail_text: 2-4 words, UPPERCASE, punchy, readable at 300px wide.
- thumbnail_subject: one concrete dramatic image subject for the thumbnail photo.
- Keep the whole JSON answer compact (short strings, no repetition).

Return ONLY this JSON:
{{
  "title": "...",
  "description": "...",
  "tags": ["..."],
  "hashtags": ["#...", "#...", "#..."],
  "thumbnail_text": "THE VANISHING FILE",
  "thumbnail_subject": "..."
}}"""
    meta = llm.chat_json(prompt, system=SEO_SYSTEM, logger=logger)
    if not meta.get("title") or not meta.get("description"):
        raise RuntimeError("metadata generation incomplete")
    meta.setdefault("tags", [])
    meta.setdefault("hashtags", [])
    meta.setdefault("thumbnail_text", script["title"].upper()[:20])
    meta["source_lines"] = src_lines
    save_state(run["dir"] / "metadata.json", meta)
    logger.info(f"metadata ready: title='{meta['title']}' "
                f"({len(meta.get('tags', []))} tags)")
    return meta


def fallback_meta(script, topic, facts):
    return {
        "title": script["title"][:65],
        "description": facts.get("summary", ""),
        "tags": [topic.get("title", "documentary").lower()],
        "hashtags": ["#documentary", "#trending", "#story"],
        "thumbnail_text": script["title"].upper()[:18],
        "thumbnail_subject": topic.get("visual_world", ""),
        "source_lines": "",
    }

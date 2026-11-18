"""Publishing metadata: Matt-Wolfe-style titles, digest description, tags."""
import datetime as dt
import json

from . import llm
from .utils import save_state

SEO_SYSTEM = (
    "You are a world-class YouTube growth strategist for AI news channels "
    "(Matt Wolfe / AI Explained tier). You know exactly how numbered digests, "
    "curiosity gaps and fresh dates combine into titles that hit 8-12% CTR. "
    "You never promise what the video does not deliver. English only. "
    "Raw JSON only."
)


def _story_lines(facts):
    lines = []
    for s in facts.get("stories") or []:
        lines.append(f"{s.get('num')}. {s.get('title', '')} — "
                     f"{s.get('money_angle', '')[:90]}")
    return "\n".join(lines)


def generate(cfg, run, script, topic, facts, logger):
    srcs = []
    for s in facts.get("stories") or []:
        srcs.extend(s.get("sources") or [])
    seen_hosts = set()
    src_lines = []
    for s in srcs:
        host, url = s.get("host", ""), s.get("url", "")
        if host and host not in seen_hosts:
            seen_hosts.add(host)
            src_lines.append(f"- {host}: {url}")
    src_lines = "\n".join(src_lines[:10])
    n_stories = len(facts.get("stories") or []) or \
        len(topic.get("stories") or []) or 4
    today = dt.date.today().strftime("%b %-d") if hasattr(dt, "locale") else \
        dt.date.today().strftime("%b %d").replace(" 0", " ")

    prompt = f"""Digest episode data:

WORKING TITLE: {script['title']}
DIGEST TYPE: {topic.get('digest_label', 'AI NEWS')} — {n_stories} stories
STORIES:
{_story_lines(facts)}

TASK: produce upload metadata for today's AI digest that maximizes CTR and
search ranking.

Rules:
- title: pick ONE of these proven Matt-Wolfe-style patterns and adapt it to the
  stories (today is {today}):
    1. "AI News: {n_stories} Stories That Actually Matter ({today})"
    2. "AI News: {n_stories} Updates Worth Knowing Today"
    3. "{n_stories} FREE AI Tools That Could Make You Money ({today})"
    4. "AI Just Leveled Up: {n_stories} Things You Need To Know"
  Max 70 characters. Numbers and parentheses are encouraged. No all-caps words
  except AI. No false claims.
- description: starts with a 2-line hook (first line < 55 chars, second line
  < 70 chars), then a blank line, then 'In today's digest:' followed by one
  line per story ('01. Title — 8-word takeaway'), then a blank line, then a
  60-90 word paragraph starting with 'The play:' that summarizes how viewers
  can profit from today's stories, then 'Sources:' list, then exactly 3
  hashtags (#AINews must be first). No emoji.
- tags: 18-22 search tags (AI news, AI tools, make money with AI, specific
  model/tool names), mix broad + specific, no duplicates.
- thumbnail_text: 2-4 words, UPPERCASE, punchy, readable at 300px wide
  (e.g. 'STILL FREE', 'IT BEATS GPT', 'GET RICH?', 'TOO POWERFUL').
- thumbnail_number: the story count as a string ("{n_stories}").
- thumbnail_kind: 'STORIES' or 'TOOLS' (matches the digest type).
- thumbnail_subject: one bold, bright, futuristic visual subject for the
  thumbnail base image.

Return ONLY this JSON:
{{
  "title": "...",
  "description": "...",
  "tags": ["..."],
  "hashtags": ["#AINews", "#AITools", "#AIMoney"],
  "thumbnail_text": "STILL FREE",
  "thumbnail_number": "{n_stories}",
  "thumbnail_kind": "STORIES",
  "thumbnail_subject": "..."
}}"""
    meta = llm.chat_json(prompt, system=SEO_SYSTEM, logger=logger)
    if not meta.get("title") or not meta.get("description"):
        raise RuntimeError("metadata generation incomplete")
    meta.setdefault("tags", [])
    meta.setdefault("hashtags", [])
    meta.setdefault("thumbnail_text", "AI MONEY DIGEST")
    meta.setdefault("thumbnail_number", str(n_stories))
    meta.setdefault("thumbnail_kind", "STORIES")
    meta["source_lines"] = src_lines
    save_state(run["dir"] / "metadata.json", meta)
    logger.info(f"metadata ready: title='{meta['title']}' "
                f"({len(meta.get('tags', []))} tags)")
    return meta


def fallback_meta(script, topic, facts):
    stories = facts.get("stories") or []
    n = max(1, len(stories))
    return {
        "title": script["title"][:70],
        "description": facts.get("summary", "")[:300],
        "tags": ["ai news", "ai tools", "make money with ai"],
        "hashtags": ["#AINews", "#AITools", "#AIMoney"],
        "thumbnail_text": "AI MONEY DIGEST",
        "thumbnail_number": str(n),
        "thumbnail_kind": "STORIES",
        "thumbnail_subject": topic.get("visual_world", "futuristic AI interface"),
        "source_lines": "",
    }

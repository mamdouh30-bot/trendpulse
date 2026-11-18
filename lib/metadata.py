"""Publishing metadata: titles + a DETERMINISTIC, link-rich description.

The description is built in Python (not by the LLM) so every link is real:
  - official tool URLs auto-matched from the narration (validated via HTTP)
  - verified article sources from the research step (validated via HTTP)
  - real chapter timestamps from the render timeline
The LLM only writes the title, tags and thumbnail copy.
"""
import datetime as dt
import json
import re
import urllib.request

from . import llm
from .utils import save_state

SEO_SYSTEM = (
    "You are a world-class YouTube growth strategist for AI news channels "
    "(Matt Wolfe / AI Explained tier). You know exactly how numbered digests, "
    "curiosity gaps and fresh dates combine into titles that hit 8-12% CTR. "
    "You never promise what the video does not deliver. English only. "
    "Raw JSON only."
)


def _fmt_stories(facts):
    lines = []
    for s in facts.get("stories") or []:
        lines.append(f"{s.get('num')}. {s.get('title', '')} — "
                     f"{s.get('money_angle', '')[:110]}")
    return "\n".join(lines)


def validate_url(url, timeout=8):
    """Return True if the URL answers with HTTP < 400."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
                          "Method": "HEAD"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status < 400
        except Exception:  # noqa: BLE001
            # some servers reject HEAD — retry with GET (stream only)
            req2 = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
            with urllib.request.urlopen(req2, timeout=timeout) as r:
                r.read(2048)
                return r.status < 400
    except Exception:  # noqa: BLE001
        return False


def _match_tools(cfg, stories):
    from .visuals import _match_tools as match
    found, order = {}, []
    for s in stories:
        text = " ".join([s.get("title", ""), s.get("summary", ""),
                         s.get("money_angle", ""),
                         " ".join(s.get("key_facts") or [])])
        for key, meta in match(cfg, text):
            if key not in found:
                found[key] = meta
                order.append(key)
    return [(k, found[k]) for k in order]


def _source_links(facts, max_n=10):
    seen, out = set(), []
    for s in facts.get("stories") or []:
        for src in s.get("sources") or []:
            url = str(src.get("url") or "").strip()
            host = str(src.get("host") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"host": host, "url": url})
    return out[:max_n]


def _ts(seconds):
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _chapters_block(cfg, run):
    tl_path = run["dir"] / "render" / "timeline.json"
    try:
        tl = json.loads(tl_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    lines = ["CHAPTERS:"]
    for c in tl.get("chapters", []):
        lines.append(f"{_ts(c.get('start', 0))} {c.get('title', '')}")
    return "\n".join(lines)


def _build_description(cfg, run, meta, topic, facts, logger):
    """Deterministic, link-rich description (never hallucinated)."""
    links_cfg = cfg.get("links", {}) or {}
    sub_url = links_cfg.get("subscribe_url", "")
    persona_name = cfg.get("persona", {}).get("name", "Adam Sterling")

    stories = facts.get("stories") or []
    n = len(stories) or len(topic.get("stories") or []) or 4

    # 1) hook (LLM-written 2 lines, trimmed to hard caps)
    hook = str(meta.get("description_hook") or "").strip()
    hook_lines = [ln.strip() for ln in hook.splitlines() if ln.strip()][:2]
    if not hook_lines:
        hook_lines = [f"{n} AI stories that actually matter today — "
                      f"and the money angle on every one.",
                      f"Hosted by {persona_name}. Links to every tool below."]

    # 2) digest list
    digest_lines = ["In today's digest:"]
    for s in stories:
        num = int(s.get("num", len(digest_lines)))
        title = (s.get("title") or "").strip()
        take = (s.get("money_angle") or "").strip()
        line = f"{num:02d}. {title}"
        if take:
            line += f" — {take}"
        digest_lines.append(line[:150])

    # 3) LINKS: official tool URLs (matched from narration, validated)
    link_lines = []
    for key, tool in _match_tools(cfg, stories):
        url = tool.get("url", "")
        if url and validate_url(url):
            link_lines.append(f"- {tool.get('label', key)}: {url}")
        else:
            logger.warning(f"tool link dropped (invalid): {key} {url}")

    # 4) SOURCES: verified article links from research (validated)
    src_lines = []
    seen_hosts = set()
    for src in _source_links(facts):
        if validate_url(src["url"]):
            label = src.get("host") or src["url"].split("/")[2]
            if label in seen_hosts:
                label = f"{label} ({len(seen_hosts) + 1})"
            seen_hosts.add(label)
            src_lines.append(f"- {label}: {src['url']}")

    # 5) chapters
    chapters = _chapters_block(cfg, run)

    # 6) about + credit
    about = (
        f"AI Money Digest is a twice-daily AI news show hosted by "
        f"{persona_name} — the stories that matter and the money angle on "
        f"every one, with links to every tool mentioned.\n"
        f"SUBSCRIBE so you never miss a digest: {sub_url}"
    )
    credit = cfg.get("music", {}).get("credit", "")

    parts = ["\n".join(hook_lines), "\n".join(digest_lines)]
    if link_lines:
        parts.append("LINKS & TOOLS MENTIONED IN THIS VIDEO:\n"
                     + "\n".join(link_lines))
    if src_lines:
        parts.append("SOURCES:\n" + "\n".join(src_lines))
    if chapters:
        parts.append(chapters)
    parts.append(about)
    if credit:
        parts.append(credit)
    tags = meta.get("hashtags") or ["#AINews", "#AITools", "#AIMoney"]
    parts.append(" ".join(tags))

    desc = "\n\n".join(parts)
    if len(desc) > 4800:
        desc = desc[:4760].rsplit("\n", 1)[0] + "\n" + " ".join(tags)
    logger.info(f"description built: {len(desc)} chars, "
                f"{len(link_lines)} tool links, {len(src_lines)} sources")
    return desc


def generate(cfg, run, script, topic, facts, logger):
    n_stories = len(facts.get("stories") or []) or \
        len(topic.get("stories") or []) or 4
    today = dt.date.today().strftime("%b %-d") if hasattr(dt, "locale") else \
        dt.date.today().strftime("%b %d").replace(" 0", " ")

    prompt = f"""Digest episode data:

WORKING TITLE: {script['title']}
DIGEST TYPE: {topic.get('digest_label', 'AI NEWS')} — {n_stories} stories
STORIES:
{_fmt_stories(facts)}

TASK: produce CTR metadata for today's AI digest (today is {today}).

Rules:
- title: pick ONE of these proven patterns and adapt it to the stories:
    1. "AI News: {n_stories} Stories That Actually Matter ({today})"
    2. "AI News: {n_stories} Updates Worth Knowing Today"
    3. "{n_stories} FREE AI Tools That Could Make You Money ({today})"
    4. "AI Just Leveled Up: {n_stories} Things You Need To Know"
  Max 70 characters. Numbers and parentheses are encouraged. No all-caps
  words except AI. No false claims.
- description_hook: EXACTLY 2 short lines (line1 < 55 chars, line2 < 70
  chars) that make the link-rich description feel worth opening. No links
  in the hook itself.
- tags: 18-22 search tags (AI news, AI tools, make money with AI, specific
  model/tool names from the stories), mix broad + specific, no duplicates.
- thumbnail_text: 2-4 words, UPPERCASE, punchy, readable at 300px wide.
- thumbnail_number: the story count as a string ("{n_stories}").
- thumbnail_kind: 'STORIES' or 'TOOLS' (matches the digest type).
- thumbnail_subject: one bold, bright, futuristic visual subject for the
  thumbnail base image.

Return ONLY this JSON:
{{
  "title": "...",
  "description_hook": "line1\\nline2",
  "tags": ["..."],
  "hashtags": ["#AINews", "#AITools", "#AIMoney"],
  "thumbnail_text": "STILL FREE",
  "thumbnail_number": "{n_stories}",
  "thumbnail_kind": "STORIES",
  "thumbnail_subject": "..."
}}"""
    meta = llm.chat_json(prompt, system=SEO_SYSTEM, logger=logger)
    if not meta.get("title"):
        raise RuntimeError("metadata generation incomplete")
    meta.setdefault("tags", [])
    meta.setdefault("hashtags", ["#AINews", "#AITools", "#AIMoney"])
    meta.setdefault("thumbnail_text", "AI MONEY DIGEST")
    meta.setdefault("thumbnail_number", str(n_stories))
    meta.setdefault("thumbnail_kind", "STORIES")

    meta["description"] = _build_description(cfg, run, meta, topic, facts,
                                             logger)
    meta["persona"] = cfg.get("persona", {}).get("name", "Adam Sterling")
    save_state(run["dir"] / "metadata.json", meta)
    logger.info(f"metadata ready: title='{meta['title']}' "
                f"({len(meta.get('tags', []))} tags)")
    return meta


def fallback_meta(script, topic, facts):
    stories = facts.get("stories") or []
    n = max(1, len(stories))
    desc_lines = [
        f"{n} AI stories that matter today — the money angle on every one.",
        "Links to every tool are in the description below.",
        "",
        "In today's digest:",
    ]
    for s in stories:
        desc_lines.append(f"{int(s.get('num', 1)):02d}. "
                          f"{s.get('title', '')} — {s.get('money_angle', '')}")
    desc_lines += ["", "SUBSCRIBE for the next digest: "
                   "https://www.youtube.com/@aimoneytested"]
    return {
        "title": script["title"][:70],
        "description": "\n".join(desc_lines),
        "tags": ["ai news", "ai tools", "make money with ai"],
        "hashtags": ["#AINews", "#AITools", "#AIMoney"],
        "thumbnail_text": "AI MONEY DIGEST",
        "thumbnail_number": str(n),
        "thumbnail_kind": "STORIES",
        "thumbnail_subject": topic.get("visual_world",
                                       "futuristic AI interface"),
        "persona": "Adam Sterling",
    }

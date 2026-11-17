"""Trend intelligence: discover -> select -> research. All English output."""
import datetime as dt

from . import llm
from .utils import save_state, load_state, safe_name

SELECT_SYSTEM = (
    "You are the head of programming at a top global documentary YouTube studio. "
    "You have an instinct for stories that explode on YouTube: mystery, money, "
    "power, science, scandal, survival. You never pick stories that are mere "
    "breaking-news fragments with no depth; you pick stories with a cinematic "
    "arc that will still make sense in a week."
)


def _gather_items(cfg, logger):
    queries = cfg["trend_discovery"]["queries"]
    month_year = dt.datetime.now().strftime("%B %Y")
    items, seen = [], set()
    for q in queries:
        q = q.replace("{month_year}", month_year)
        for r in llm.web_search(q, num=8, logger=logger):
            name = (r.get("name") or "").strip()
            key = name.lower()[:80]
            if not name or key in seen:
                continue
            seen.add(key)
            items.append({
                "title": name,
                "snippet": (r.get("snippet") or "")[:400],
                "source": r.get("host_name") or "",
                "url": r.get("url") or "",
            })
    logger.info(f"discovery gathered {len(items)} unique raw items")
    return items


def select_topic(cfg, run, logger):
    """Score candidates and pick the single best topic for today's video."""
    items = _gather_items(cfg, logger)
    if not items:
        raise RuntimeError("trend discovery returned nothing")

    hist_path = run["project_root"] / cfg["trend_discovery"]["history_file"]
    history = load_state(hist_path, default=[])
    done_titles = [h.get("topic", "").lower() for h in history][-60:]

    shortlist = [
        {"title": i["title"], "snippet": i["snippet"], "source": i["source"]}
        for i in items[:40]
    ]
    prompt = f"""Today is {dt.date.today().isoformat()}.

Below are {len(shortlist)} raw trending items scraped from global news and trend sources:

{json_items(shortlist)}

TASK: Choose THE single best topic for a 13-minute cinematic English documentary
on the TrendPulse channel. Judge each candidate on:
1. Viral potential and click-through appeal for a global YouTube audience
2. Story depth (enough facts, timeline and twists for 5 chapters)
3. Strong visual potential (real places, objects, events to show)
4. Not purely ephemeral breaking news, not celebrity gossip

Avoid any topic similar to already-produced titles: {done_titles or 'none yet'}

Return ONLY this JSON:
{{
  "chosen": {{
    "title": "clear working title of the chosen topic",
    "angle": "the specific dramatic angle the film will take (1-2 sentences)",
    "why_viral": "1 sentence why this will explode",
    "visual_world": "the visual world of this story (places, objects, era)"
  }},
  "runner_up": "title of the second-best candidate"
}}"""
    sel = llm.chat_json(prompt, system=SELECT_SYSTEM, logger=logger)
    topic = sel.get("chosen") or {}
    if not topic.get("title"):
        raise RuntimeError(f"topic selection failed: {str(sel)[:300]}")
    logger.info(f"selected topic: {topic.get('title')}")
    return topic


def json_items(items):
    import json
    return json.dumps(items, ensure_ascii=False, indent=1)


def research(cfg, run, topic, logger):
    """Collect verified facts, timeline, numbers and sources for the script."""
    n = cfg["trend_discovery"]["research_queries"]
    base_q = topic["title"]
    queries = [
        base_q,
        f"{base_q} timeline chronology of events",
        f"{base_q} key people involved evidence",
        f"{base_q} official findings reports numbers",
        f"{base_q} aftermath consequences latest updates",
    ][:n]
    raw = []
    for q in queries:
        for r in llm.web_search(q, num=8, logger=logger):
            raw.append({
                "q": q,
                "title": r.get("name", ""),
                "snippet": (r.get("snippet") or "")[:500],
                "url": r.get("url", ""),
                "host": r.get("host_name", ""),
            })
    logger.info(f"research: {len(raw)} raw sources collected")

    prompt = f"""TOPIC: {topic['title']}
ANGLE: {topic.get('angle', '')}

Below are {len(raw)} search results (title + snippet + source):

{json_items(raw)}

TASK: Consolidate the VERIFIED core of this story for a documentary script.
Rules:
- Use ONLY facts present in the results. If results conflict or are thin, mark
  it clearly as "unverified".
- Build a chronological spine with concrete dates, numbers and names.
- Keep every array SHORT so the whole answer stays compact.

Return ONLY this JSON:
{{
  "summary": "3-sentence factual summary of the story",
  "timeline": [{{"date": "...", "event": "max 12 words"}}],
  "key_facts": ["exactly 10 short facts"],
  "dramatic_beats": ["4 short beats"],
  "open_questions": ["2 short items"],
  "sources": [{{"host": "bbc.com", "url": "https://..."}}]
}}"""
    facts = llm.chat_json(prompt, system=SELECT_SYSTEM, logger=logger)
    if not facts.get("summary"):
        raise RuntimeError("research consolidation failed")
    srcs = facts.get("sources") or []
    logger.info(f"research consolidated: {len(facts.get('key_facts', []))} facts, "
                f"{len(facts.get('timeline', []))} timeline events, {len(srcs)} sources")
    (run["dir"] / "research").mkdir(exist_ok=True)
    save_state(run["dir"] / "research" / "facts.json", {"topic": topic, "facts": facts})
    return facts

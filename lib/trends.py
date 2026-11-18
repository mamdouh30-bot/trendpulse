"""Trend intelligence for the AI Money digest:
discover -> cluster into a 4-story batch -> verify facts per story.
All English output."""
import datetime as dt

from . import llm
from .utils import save_state, load_state

CURATOR_SYSTEM = (
    "You are the head of programming at AI Money, a fast-growing AI news channel "
    "that competes head-on with Matt Wolfe. You sift through all the AI noise and "
    "select the stories that actually matter to creators, builders and people who "
    "want to make money with AI. You know what clicks: big model releases, free "
    "tools, jaw-dropping demos, industry drama, funding moves, and practical ways "
    "viewers can profit today. You cluster related items into one coherent daily "
    "digest batch of stories that fit together. You never invent stories that are "
    "not supported by the provided items. English only. Raw JSON only."
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
    """Cluster today's AI noise into ONE digest batch of stories."""
    items = _gather_items(cfg, logger)
    if not items:
        raise RuntimeError("trend discovery returned nothing")

    hist_path = run["project_root"] / cfg["trend_discovery"]["history_file"]
    history = load_state(hist_path, default=[])
    done_titles = [h.get("topic", "").lower() for h in history][-60:]

    n_stories = int(cfg["content"].get("chapters", 4))
    shortlist = [
        {"title": i["title"], "snippet": i["snippet"], "source": i["source"]}
        for i in items[:44]
    ]
    prompt = f"""Today is {dt.date.today().isoformat()}.

Below are {len(shortlist)} raw AI-related items scraped from news and trend sources:

{json_items(shortlist)}

TASK: Build THE single best digest batch for today's AI Money video.
Cluster the items into exactly {n_stories} DISTINCT stories that fit one coherent
episode (a mix of: one or two big model/company moves, one or two new tools or
product launches, one money/opportunity story). Each story must be backed by
real items above — never invent facts.

Judge the whole batch on:
1. "Wait, I need to know this" click appeal for a global AI-interested audience
2. Verifiable depth (enough facts for ~9 narration scenes per story)
3. At least one story with a clear "how to make money with this" angle
4. Strong visual potential (products, interfaces, labs, cities, demos)

Rules:
- Skip celebrity gossip and pure rumors.
- Avoid topics similar to already-produced digests: {done_titles or 'none yet'}
- search_queries per story: 2 precise web searches that verify the facts
  (include the company/product name + "news" or "details").

Return ONLY this JSON:
{{
  "digest_label": "AI NEWS" or "AI TOOLS" (pick what fits the batch),
  "title_hint": "working episode title in Matt Wolfe style, e.g. 'AI News: 4 Stories That Actually Matter Today'",
  "stories": [
    {{
      "num": 1,
      "title": "clear story title",
      "hook": "one dramatic opening line for this story (max 18 words)",
      "angle": "what happened + why it matters (2 sentences)",
      "money_angle": "how viewers can profit or use it today (1 sentence)",
      "visual_world": "the imagery of this story: products, interfaces, labs, demos",
      "search_queries": ["verification search 1", "verification search 2"]
    }}
  ],
  "runner_up": "title of the best story you had to leave out"
}}"""
    sel = llm.chat_json(prompt, system=CURATOR_SYSTEM, logger=logger)
    stories = sel.get("stories") or []
    if len(stories) < 2:
        raise RuntimeError(f"digest selection failed: {str(sel)[:300]}")
    for i, s in enumerate(stories, 1):
        s.setdefault("num", i)
        if not s.get("title"):
            raise RuntimeError("a digest story is missing a title")

    topic = {
        "title": sel.get("title_hint") or "AI News: The Stories That Matter Today",
        "digest_label": sel.get("digest_label", "AI NEWS"),
        "angle": "daily AI digest with a money angle on every story",
        "visual_world": "futuristic AI interfaces, data centers, robots, product demos",
        "stories": stories[:n_stories],
    }
    logger.info(f"digest selected: {topic['digest_label']} with "
                f"{len(topic['stories'])} stories: "
                + " | ".join(s["title"][:40] for s in topic["stories"]))
    return topic


def json_items(items):
    import json
    return json.dumps(items, ensure_ascii=False, indent=1)


def research(cfg, run, topic, logger):
    """Verify facts for every digest story via targeted searches."""
    n_q = int(cfg["trend_discovery"].get("research_queries", 2))
    raw = []
    for s in topic["stories"]:
        queries = [q for q in (s.get("search_queries") or [])][:n_q]
        if not queries:
            queries = [s["title"]]
        for q in queries:
            for r in llm.web_search(q, num=8, logger=logger):
                raw.append({
                    "story_num": s["num"],
                    "q": q,
                    "title": r.get("name", ""),
                    "snippet": (r.get("snippet") or "")[:500],
                    "url": r.get("url", ""),
                    "host": r.get("host_name", ""),
                })
    logger.info(f"research: {len(raw)} raw sources across "
                f"{len(topic['stories'])} stories")

    prompt = f"""DIGEST: {topic.get('title')}
STORIES:
{json_items([{k: s.get(k, '') for k in ('num', 'title', 'angle', 'money_angle')}
             for s in topic['stories']])}

Below are {len(raw)} search results grouped by story_num:

{json_items(raw)}

TASK: Verify and consolidate the facts for EVERY story of today's digest.
Rules:
- Use ONLY facts present in the results. If a story's results are thin or
  conflicting, say so inside its summary and lower its fact count.
- Concrete dates, numbers, product names, prices. Never invent.
- money_angle: refine the story's money angle into something specific and
  actionable the viewer can do TODAY (free tool to try, strategy, playbook).
- Keep it COMPACT: summary max 3 sentences, 5-8 key_facts per story, facts
  under 20 words each, at most 4 sources per story.

Return ONLY this JSON:
{{
  "stories": [
    {{
      "num": 1,
      "title": "story title",
      "summary": "max 3 sentence verified factual summary",
      "key_facts": ["fact 1", "fact 2", "... 5 to 8 facts"],
      "money_angle": "the specific, actionable play for the viewer",
      "sources": [{{"host": "techcrunch.com", "url": "https://..."}}]
    }}
  ]
}}"""
    facts = llm.chat_json(prompt, system=CURATOR_SYSTEM, logger=logger)
    got = facts.get("stories") or []
    if not got:
        raise RuntimeError("research consolidation failed")
    by_num = {s.get("num"): s for s in got}
    merged = []
    dropped = []
    for s in topic["stories"]:
        m = by_num.get(s["num"]) or {}
        m.setdefault("title", s["title"])
        m.setdefault("num", s["num"])
        m.setdefault("key_facts", [])
        m.setdefault("money_angle", s.get("money_angle", ""))
        m.setdefault("summary", "")
        # anti-hallucination guard: a story without a single verifiable
        # URL cannot make it to air — drop it (renumber the survivors)
        valid = [x for x in (m.get("sources") or [])
                 if str(x.get("url", "")).startswith(("http://", "https://"))]
        m["sources"] = valid[:4]
        if not valid:
            dropped.append(s["num"])
            continue
        merged.append(m)
    if dropped:
        logger.warning(f"stories dropped for missing verifiable sources: "
                       f"{dropped}")
        if len(merged) >= 2:
            for i, m in enumerate(merged, 1):
                m["num"] = i
        else:
            logger.warning("fewer than 2 verified stories — keeping "
                           "unverified ones to keep the digest viable")
            for s in topic["stories"]:
                if s["num"] in dropped:
                    m = by_num.get(s["num"]) or dict(s)
                    m.setdefault("title", s["title"])
                    m.setdefault("num", s["num"])
                    m.setdefault("key_facts", [])
                    m.setdefault("sources", [])
                    m.setdefault("summary", "")
                    merged.append(m)
            merged.sort(key=lambda x: x.get("num", 0))
    facts = {"digest_label": topic.get("digest_label", "AI NEWS"),
             "stories": merged,
             "summary": "; ".join((m.get("summary") or "")[:80] for m in merged)}
    n_src = sum(len(m.get("sources") or []) for m in merged)
    logger.info(f"research consolidated: {sum(len(m['key_facts']) for m in merged)} "
                f"facts, {n_src} sources")
    (run["dir"] / "research").mkdir(exist_ok=True)
    save_state(run["dir"] / "research" / "facts.json", {"topic": topic, "facts": facts})
    return facts

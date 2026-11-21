"""AI Money digest scriptwriter: hook -> per-story scenes -> outro (JSON).

No outline phase: the digest batch IS the structure, which saves an LLM
round-trip and keeps every episode in the Matt Wolfe format.
"""
from . import llm
from .utils import load_state, save_state

WRITER_SYSTEM = (
    "You ARE Adam Sterling — the professional host of AI Money, a daily AI "
    "news show in the spirit of top tech YouTube hosts like Matt Wolfe. You "
    "talk directly to the viewer ('you'), you cut the fluff, and you always "
    "land the practical takeaway: why this matters and how to profit from "
    "it. Conversational, confident, opinionated but factual. Concrete "
    "numbers, product names and dates from the research — never invent "
    "facts. Short punchy sentences that sound great spoken aloud. English "
    "only. Raw JSON only."
)

HOOK_INSTRUCTION = """HOOK RULES (this decides retention):
- Scene 1 is a cold open: today's AI news is big and you are sifting the noise —
  one energetic line that promises the N stories that actually matter, no
  greetings, no 'welcome back', max 25 words.
- Scene 2-3 tease the biggest story of the batch without fully revealing it.
- Sound like a host, not a documentary: fast, confident, direct."""

STORY_RULES = """STORY RULES:
- Each scene's narration: 1-3 sentences, max 420 characters (spoken aloud).
- Scene 1 of the story opens with the story's hook — the single most gripping
  VERIFIED fact from the research core, no throat-clearing.
- Middle scenes deliver the verified facts with concrete numbers, names and
  dates. Explain what the thing IS before why it matters.
- GROUNDING (critical): only mention companies, products, versions, numbers
  and dates that appear in the RESEARCH CORE. If the research is thin, speak
  about the story more generally — a credible general statement beats an
  invented specific one. You are a real host; your credibility is the show.
- The final 1-2 scenes of every story land the MONEY ANGLE: the specific play
  the viewer can make today — a free tool to try, a strategy, an opportunity.
  When a tool or link is relevant, tell the viewer it is waiting for them in
  the description below (e.g. 'the official link is in the description').
  Concrete and actionable, never generic advice like 'stay informed'.
- The story's last line pivots with energy into the next story.
- Total flow reads like one continuous host monologue, not bullet points."""

VISUAL_RULES = """VISUAL QUERY RULES (used to find REAL moving stock FOOTAGE clips):
- visual_query = one concrete, searchable stock-VIDEO shot, 4-9 words.
- Think like a tech-news cinematographer choosing B-ROLL THAT MOVES: trading
  screens with live charts, robot arms working on a line, data-center aisles
  with blinking lights, city traffic timelapse, hands typing on a laptop,
  engineers walking through a lab, keynote crowd, drone over skyscrapers.
  Examples: 'robot arm assembly line moving', 'stock market screens rising',
  'data center server aisle lights', 'city traffic timelapse night',
  'hands typing laptop closeup', 'engineers laboratory walking'.
- Prefer MOTION verbs (moving, working, rising, walking, flying, timelapse).
- No named people or brands, no abstract concepts ('innovation'), no text-
  on-screen requests. Alternate wide establishing shots with close-ups."""

OUTRO_RULES = """OUTRO RULES (2 scenes):
- Scene 1: rapid-fire one-line recap of every story, in order, with the story's
  key takeaway (one short line per story, separated naturally).
- Scene 2: one direct line inviting the viewer to subscribe for the next digest
  (every 12 hours) + one specific question about today's stories to drive
  comments. No begging."""


# Fixed channel intro — IDENTICAL narration in every single episode.
# It plays before the cold open, welcomes the viewer, introduces the host
# and drives the subscribe call-to-action (rendered with a sliding subscribe
# banner by lib/renderer.py).
INTRO_NARRATION = (
    "Welcome to AI Money — I'm Adam Sterling. Every single day I cut through "
    "the noise and bring you the AI stories that actually matter, with the "
    "money angle you can act on. If that sounds like your kind of edge, hit "
    "subscribe right now — new digests land here every twelve hours, and you "
    "don't want to miss the next one."
)
FIXED_INTRO_SCENE = {
    "narration": INTRO_NARRATION,
    "visual_query": "television studio news desk broadcast lights",
    "mood": "bold",
    "is_fixed_intro": True,
}


def _fmt_stories(facts):
    import json
    stories = facts.get("stories") or []
    slim = []
    for s in stories:
        slim.append({
            "num": s.get("num"),
            "title": s.get("title", ""),
            "summary": s.get("summary", ""),
            "key_facts": (s.get("key_facts") or [])[:12],
            "money_angle": s.get("money_angle", ""),
        })
    return json.dumps(slim, ensure_ascii=False, indent=1)


def write_script(cfg, run, topic, facts, logger):
    """Returns script dict: {title, scenes:[...], chapters:[...]}."""
    stories = (facts.get("stories") or []) or topic.get("stories", [])
    if not stories:
        raise RuntimeError("digest has no stories to script")

    hook_scenes = int(cfg["content"].get("hook_scenes", 3))
    outro_scenes = int(cfg["content"].get("outro_scenes", 2))
    intro_extra = 1 if cfg["content"].get("intro_scene", True) else 0
    sps = int(cfg["content"].get("scenes_per_story", 9))
    n_stories = len(stories)
    max_scenes = int(cfg["content"]["max_scenes"])
    total_target = intro_extra + hook_scenes + n_stories * sps + outro_scenes
    if total_target > max_scenes:
        sps = max(5, (max_scenes - intro_extra - hook_scenes - outro_scenes)
                  // n_stories)
    # duration floor: when research drops stories (e.g. 4 -> 2), the video
    # must NOT fall under the 10-minute target — give every remaining story
    # proportionally more scenes instead (capped to keep scenes readable)
    min_scenes = int(cfg["content"].get("min_scenes", 30))
    floor_sps = -(-(min_scenes - intro_extra - hook_scenes - outro_scenes)
                  // max(1, n_stories))
    sps = max(sps, min(floor_sps, 16))
    body_words = int(cfg["content"]["word_budget"] * 0.82)
    words_per_story = max(260, body_words // n_stories)
    digest_label = topic.get("digest_label", facts.get("digest_label", "AI NEWS"))
    title = topic.get("title", "AI News: The Stories That Matter Today")

    scenes = []
    chapters_out = [{"num": 0, "title": "Intro"}]

    # ---- FIXED channel intro (identical every episode, no LLM call) ----
    if intro_extra:
        scenes.append(dict(FIXED_INTRO_SCENE))

    # per-section cache: resume-safe (each finished section is saved, so a
    # rate-limit crash never re-spends quota on already-written sections)
    sec_path = run["dir"] / "script" / "sections.json"
    sec_cache = load_state(sec_path, default={})

    def gen_section(section_label, scene_count, word_budget, rules_extra,
                    is_hook=False, final_story=False):
        ck = f"{section_label}|{scene_count}"
        if ck in sec_cache:
            got = sec_cache[ck]
            logger.info(f"section '{section_label}: {len(got)} scenes (cached)")
            return got
        per_scene = max(30, word_budget // max(1, scene_count))
        final_note = ("\nThis is the FINAL story: end it by leading into the "
                      "wrap-up, do not tease another story." if final_story else "")
        prompt = f"""Episode: {title}
Digest type: {digest_label} — {n_stories} stories

RESEARCH CORE (verified facts):
{_fmt_stories(facts)}

SECTION: {section_label}
{HOOK_INSTRUCTION if is_hook else ''}
{rules_extra}
{VISUAL_RULES}
Write exactly {scene_count} scenes, about {word_budget} words total for this
section. IMPORTANT: aim for ~{per_scene} words PER SCENE — that is 3 to 4 full
spoken sentences each, up to the 420-character limit. A scene with one short
sentence is a defect; pack it with concrete facts, numbers and consequences
from the research core.{final_note}

Return ONLY this JSON:
{{"scenes": [
  {{"narration": "...", "visual_query": "...",
    "mood": "hype|urgent|practical|curious|bold", "is_key_scene": true|false}}
]}}"""
        data = llm.chat_json(prompt, system=WRITER_SYSTEM, logger=logger)
        got = data.get("scenes") or []
        if not got:
            raise RuntimeError(f"section '{section_label}' produced no scenes")

        # duration guard: regenerate once if the section came out too thin
        words = sum(len(str(s.get("narration", "")).split()) for s in got)
        if words < word_budget * 0.72:
            logger.warning(f"section '{section_label}' too thin "
                           f"({words}/{word_budget} words) — regenerating")
            retry_prompt = (prompt + f"\n\nCRITICAL: your previous version had only {words} words\n"
                            f"for {scene_count} scenes — far too short for a 14-minute video.\n"
                            f"Each scene needs {per_scene}+ words (3-4 full sentences with\n"
                            f"specific facts and numbers). Expand every scene.")
            data2 = llm.chat_json(retry_prompt, system=WRITER_SYSTEM, logger=logger)
            got2 = data2.get("scenes") or []
            words2 = sum(len(str(s.get("narration", "")).split()) for s in got2)
            if got2 and words2 > words:
                got = got2
        sec_cache[ck] = got
        (run["dir"] / "script").mkdir(exist_ok=True)
        save_state(sec_path, sec_cache)
        return got

    # ---- cold open hook ----
    hook = gen_section(
        f"COLD OPEN HOOK ({hook_scenes} scenes) — frame today's digest and tease "
        f"the biggest story",
        hook_scenes, 110, "", is_hook=True)
    scenes += hook

    # ---- one section per story ----
    for si, s in enumerate(stories):
        num = int(s.get("num", len(chapters_out)))
        label = (f"STORY {num}: {s.get('title', '')}\n"
                 f"Story hook to open with: {s.get('hook', s.get('title', ''))}\n"
                 f"Money angle to land at the end: {s.get('money_angle', '')}")
        got = gen_section(label, sps, words_per_story, STORY_RULES,
                          final_story=(si == n_stories - 1))
        scenes += got
        chapters_out.append({"num": num, "title": s.get("title", f"Story {num}")})

    # ---- outro ----
    recap = "; ".join(f"story {s.get('num')}: {s.get('title','')}"
                      for s in stories)
    outro = gen_section(f"OUTRO (2 scenes) — rapid recap of all stories: {recap}",
                        outro_scenes, 90, OUTRO_RULES)
    scenes += outro
    chapters_out.append({"num": 99, "title": "Outro"})

    # ---- validate + renumber ----
    story_titles = {c["num"]: c["title"] for c in chapters_out}
    cleaned = []
    for i, s in enumerate(scenes, 1):
        narr = str(s.get("narration", "")).strip().strip('"')
        if len(narr) > 480:
            cut = narr[:480]
            for sep in (". ", "! ", "? "):
                p = cut.rfind(sep)
                if p > 200:
                    cut = cut[:p + 1]
                    break
            narr = cut
        if not narr:
            continue
        vq = " ".join(str(s.get("visual_query", "")).split()[:14])
        cleaned.append({
            "id": len(cleaned) + 1,
            "chapter": 0,
            "chapter_title": "",
            "chapter_label": "Cold Open",
            "narration": narr,
            "visual_query": vq or "glowing server racks data center blue light",
            "mood": s.get("mood", "hype"),
            "is_key_scene": bool(s.get("is_key_scene")) or (i == 1) or (i == len(scenes)),
            "is_fixed_intro": bool(s.get("is_fixed_intro")),
        })

    # re-assign chapters by position: fixed intro / hook / story sections / outro
    body_start = intro_extra + hook_scenes
    body_end = len(cleaned) - outro_scenes
    for idx, s in enumerate(cleaned):
        if intro_extra and idx == 0:
            s["chapter"] = 0
            s["chapter_label"] = "Intro"
        elif idx < body_start:
            s["chapter"] = 0
            s["chapter_label"] = "Cold Open"
        elif idx >= body_end:
            s["chapter"] = 99
            s["chapter_label"] = "Outro"
        else:
            body_i = idx - body_start
            st = min(n_stories, 1 + body_i // sps)
            s["chapter"] = st
            s["chapter_label"] = f"STORY {st}"
            s["chapter_title"] = story_titles.get(st, "")
            if st == n_stories and s is cleaned[body_end - 1]:
                pass  # last body scene keeps its story assignment

    script = {"title": title, "scenes": cleaned, "chapters": chapters_out}
    (run["dir"] / "script").mkdir(exist_ok=True)
    save_state(run["dir"] / "script" / "script.json", script)
    save_state(run["dir"] / "script" / "narration.txt",
               "\n\n".join(s["narration"] for s in cleaned))
    words = sum(len(s["narration"].split()) for s in cleaned)
    logger.info(f"digest script done: {len(cleaned)} scenes, {words} words, "
                f"title='{title}'")
    return script


MINI_SCRIPT = {
    "title": "AI News: 2 Stories That Actually Matter Today",
    "chapters": [
        {"num": 0, "title": "Intro"},
        {"num": 1, "title": "The Model That shook the Labs"},
        {"num": 99, "title": "Outro"},
    ],
    "scenes": [
        {"id": 1, "chapter": 0, "chapter_label": "Intro", "chapter_title": "",
         "is_fixed_intro": True,
         "narration": INTRO_NARRATION,
         "visual_query": "television studio news desk broadcast lights", "mood": "bold", "is_key_scene": True},
        {"id": 2, "chapter": 0, "chapter_label": "Cold Open", "chapter_title": "",
         "narration": "AI just moved faster than anyone expected — and most people missed it. Two stories today, and one of them can put money in your pocket this week.",
         "visual_query": "glowing server racks data center blue light", "mood": "hype", "is_key_scene": False},
        {"id": 3, "chapter": 0, "chapter_label": "Cold Open", "chapter_title": "",
         "narration": "A new open-source model is beating tools you pay for. And the second story is the one nobody is pricing in yet.",
         "visual_query": "hand holding phone AI app interface closeup", "mood": "curious", "is_key_scene": False},
        {"id": 4, "chapter": 1, "chapter_label": "STORY 1", "chapter_title": "The Model That shook the Labs",
         "narration": "Story one. The new model runs on hardware you already own, and in early benchmarks it beats names that raised billions.",
         "visual_query": "robot arm modern lab technology closeup", "mood": "hype", "is_key_scene": True},
        {"id": 5, "chapter": 1, "chapter_label": "STORY 1", "chapter_title": "The Model That shook the Labs",
         "narration": "The play is simple: try it free today, ship a small product with it this weekend, and you are ahead of ninety percent of builders.",
         "visual_query": "developer coding multiple monitors dark room", "mood": "practical", "is_key_scene": False},
        {"id": 6, "chapter": 99, "chapter_label": "Outro", "chapter_title": "",
         "narration": "That's the digest. Subscribe — the next one lands in twelve hours. Which tool are you trying first? Say it in the comments.",
         "visual_query": "city billboards neon advertisements night", "mood": "bold", "is_key_scene": False},
    ],
}

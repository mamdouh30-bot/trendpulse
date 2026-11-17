"""Two-phase cinematic scriptwriter: outline -> per-section scenes (JSON)."""
from . import llm
from .utils import save_state

WRITER_SYSTEM = (
    "You are an elite Netflix-level documentary scriptwriter writing narration for "
    "a cinematic YouTube channel. Your narration is spoken aloud by a deep male "
    "voice. It is vivid, tense, factual and paced. Short punchy sentences. Present "
    "the story in media res, build escalating tension, land reveals on chapter "
    "climaxes, never insult the viewer with fluff. English only. "
    "You always answer with raw JSON only."
)

HOOK_INSTRUCTION = """HOOK RULES (this decides retention):
- Scene 1 is a cold open in the middle of the most gripping moment: one shocking
  line, no setup, max 25 words.
- Scene 2-3 raise the stakes and tease the revelation, never revealing it.
- Never start with greetings, channel names, or 'today we will talk about'."""

NARRATION_RULES = """NARRATION RULES:
- Each scene's narration: 1-3 sentences, max 300 characters (spoken aloud).
- Total flow must read like one continuous film, not bullet points.
- Use concrete numbers, dates and names from the research. Never invent facts.
- End each chapter on a cliffhanger line that pulls into the next."""

VISUAL_RULES = """VISUAL QUERY RULES (used to find REAL photographs for the film):
- visual_query = a concrete, searchable description of one shot, 6-12 words.
- Think like a cinematographer: places, rooms, objects, silhouettes, documents,
  machines, weather. Examples: 'newsroom at night empty desks monitors',
  'cargo ship ocean storm aerial view', 'old filing cabinet closeup papers'.
- No abstract concepts ('justice', 'fear'), no text-on-screen requests.
- Alternate wide establishing shots with intimate close-ups."""


def _fmt_facts(facts):
    import json
    return json.dumps({
        "summary": facts.get("summary", ""),
        "timeline": facts.get("timeline", [])[:14],
        "key_facts": facts.get("key_facts", [])[:20],
        "dramatic_beats": facts.get("dramatic_beats", []),
        "open_questions": facts.get("open_questions", []),
    }, ensure_ascii=False, indent=1)


def write_script(cfg, run, topic, facts, logger):
    """Returns script dict: {title, scenes:[...], chapters:[...]}."""
    n_ch = int(cfg["content"]["chapters"])
    hook_scenes = 3
    outro_scenes = 2
    total_scenes = int(cfg["content"]["max_scenes"])
    ch_scenes = max(4, (total_scenes - hook_scenes - outro_scenes) // n_ch)

    words_per_ch = int(cfg["content"]["word_budget"] * 0.17)
    outline_prompt = f"""You are scripting a {cfg['content']['target_minutes']}-minute
cinematic documentary for YouTube.

TOPIC: {topic.get('title')}
ANGLE: {topic.get('angle', '')}
VISUAL WORLD: {topic.get('visual_world', '')}

RESEARCH CORE:
{_fmt_facts(facts)}

Design the film spine: {n_ch} chapters that escalate, from the setup to the deepest
reveal, ordered for maximum tension. Chapters must follow the dramatic beats.
Return ONLY this JSON:
{{
  "title_options": ["3 click-worthy documentary titles, max 60 chars, no clickbait lies"],
  "chapters": [
    {{"num": 1, "title": "chapter title", "beat": "what happens + the tension line",
      "scene_count": {ch_scenes}}}
  ]
}}"""
    outline = llm.chat_json(outline_prompt, system=WRITER_SYSTEM, logger=logger)
    chapters = outline.get("chapters") or []
    if not chapters:
        raise RuntimeError("outline produced no chapters")
    title = (outline.get("title_options") or [topic.get("title", "Untitled")])[0]

    scenes = []
    chapters_out = []

    def gen_section(section_label, scene_count, word_budget, beat, is_hook=False,
                    is_outro=False, tail=""):
        """Write a section in small batches (3 scenes per LLM call) so each
        answer fits the free-tier output token budget."""
        all_got = []
        remaining = scene_count
        while remaining > 0:
            n = min(3, remaining)
            prompt = f"""Film: {title}
Topic: {topic.get('title')} — Angle: {topic.get('angle','')}
Research core:
{_fmt_facts(facts)}

SECTION: {section_label}
{HOOK_INSTRUCTION if is_hook else ''}
{'OUTRO RULES: Scene A resolves the story in one powerful line, scene B invites the viewer to subscribe for more deep investigations, no begging, one sentence each.' if is_outro else ''}
{NARRATION_RULES}
{VISUAL_RULES}
Chapter plan: {beat}
{f"Already written scenes end with: {' '.join(str(all_got[-1].get('narration','')).split()[-12:]) if all_got else '(nothing yet)'}" if all_got else ''}
Write exactly {n} scene(s) now, about {word_budget * n // max(scene_count, 1)} words total for this batch.
{"Next chapter begins with: " + tail if (tail and remaining <= n) else ""}

Return ONLY this JSON:
{{"scenes": [
  {{"narration": "...", "visual_query": "...", "mood": "tense|somber|curious|urgent|reflective",
    "is_key_scene": true|false}}
]}}"""
            data = llm.chat_json(prompt, system=WRITER_SYSTEM, logger=logger)
            got = data.get("scenes") or []
            if not got:
                raise RuntimeError(f"section '{section_label}' produced no scenes")
            all_got += got
            remaining -= len(got)
        return all_got

    # Hook
    hook = gen_section("COLD OPEN HOOK (chapter 0)", hook_scenes, 110,
                       "the single most gripping moment of the story",
                       is_hook=True)
    scenes += hook
    chapters_out.append({"num": 0, "title": "Cold Open"})

    # Chapters
    for ch in chapters[:n_ch]:
        num = ch.get("num", len(chapters_out))
        got = gen_section(f"CHAPTER {num}: {ch.get('title','')}",
                          ch.get("scene_count", ch_scenes), words_per_ch,
                          ch.get("beat", ""))
        scenes += got
        chapters_out.append({"num": num, "title": ch.get("title", f"Chapter {num}")})

    # Outro
    outro = gen_section("OUTRO (chapter 99)", outro_scenes, 70,
                        "resolution + call to subscribe", is_outro=True)
    scenes += outro
    chapters_out.append({"num": 99, "title": "Outro"})

    # Validate + renumber
    chapter_titles = {c["num"]: c["title"] for c in chapters_out}
    cleaned = []
    for i, s in enumerate(scenes, 1):
        narr = str(s.get("narration", "")).strip().strip('"')
        if len(narr) > 330:
            cut = narr[:330]
            for sep in (". ", "! ", "? "):
                p = cut.rfind(sep)
                if p > 150:
                    cut = cut[:p + 1]
                    break
            narr = cut
        if not narr:
            continue
        vq = " ".join(str(s.get("visual_query", "")).split()[:14])
        cleaned.append({
            "id": len(cleaned) + 1,
            "chapter": s.get("chapter") or (
                0 if i <= hook_scenes else (99 if i > len(scenes) - outro_scenes else 1)
            ),
            "chapter_title": "",
            "narration": narr,
            "visual_query": vq or "dark empty room dramatic light window",
            "mood": s.get("mood", "tense"),
            "is_key_scene": bool(s.get("is_key_scene")) or (i == 1) or (i == len(scenes)),
        })
    # Map chapter titles correctly for every scene
    hook_ct = hook_scenes
    out_ct = outro_scenes
    per_ch = {}
    for s in cleaned:
        per_ch[s["chapter"]] = per_ch.get(s["chapter"], 0) + 1
    # re-assign chapters by position (LLM may mislabel): hook / chapters / outro
    idx = 0
    for s in cleaned:
        if idx < hook_ct:
            s["chapter"] = 0
        elif idx >= len(cleaned) - out_ct:
            s["chapter"] = 99
        else:
            body_i = idx - hook_ct
            s["chapter"] = 1 + (body_i // ch_scenes)
        s["chapter_title"] = chapter_titles.get(s["chapter"], "")
        idx += 1

    script = {"title": title, "scenes": cleaned, "chapters": chapters_out}
    (run["dir"] / "script").mkdir(exist_ok=True)
    save_state(run["dir"] / "script" / "script.json", script)
    save_state(run["dir"] / "script" / "narration.txt",
               "\n\n".join(s["narration"] for s in cleaned))
    words = sum(len(s["narration"].split()) for s in cleaned)
    logger.info(f"script done: {len(cleaned)} scenes, {words} words, "
                f"title='{title}'")
    return script


MINI_SCRIPT = {
    "title": "The Signal From The Deep",
    "chapters": [
        {"num": 0, "title": "Cold Open"},
        {"num": 1, "title": "The Abyss Listens"},
        {"num": 99, "title": "Outro"},
    ],
    "scenes": [
        {"id": 1, "chapter": 0, "chapter_title": "Cold Open", "narration": "It was 3 a.m. when the hydrophones heard it. A sound no ship, no whale, no machine on Earth could explain.",
         "visual_query": "dark ocean waves night aerial view", "mood": "urgent", "is_key_scene": True},
        {"id": 2, "chapter": 0, "chapter_title": "Cold Open", "narration": "Scientists traced the signal to a trench deeper than Mount Everest is tall. Then it stopped. For twenty years, nothing.",
         "visual_query": "underwater deep sea trench darkness", "mood": "curious", "is_key_scene": False},
        {"id": 3, "chapter": 1, "chapter_title": "The Abyss Listens", "narration": "Chapter One. The machines that listen to the ocean floor were built to catch enemy submarines. Instead, they caught a mystery.",
         "visual_query": "submarine sonar room equipment vintage", "mood": "tense", "is_key_scene": True},
        {"id": 4, "chapter": 1, "chapter_title": "The Abyss Listens", "narration": "When the signal returned in 2016, every expert agreed on one thing: whatever made it was still down there, moving.",
         "visual_query": "research vessel ocean science ship deck", "mood": "somber", "is_key_scene": False},
        {"id": 5, "chapter": 99, "chapter_title": "Outro", "narration": "If stories from the edge of the known world pull you in, subscribe. The next investigation is already underway.",
         "visual_query": "person silhouette looking at sea horizon dusk", "mood": "reflective", "is_key_scene": False},
    ],
}

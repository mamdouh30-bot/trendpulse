"""LLM + web search for TrendPulse (portable free-hosting edition).

chat():
  1. If LLM_API_KEY (or GEMINI_API_KEY) is set -> OpenAI-compatible endpoint
     (Gemini / Z.ai / OpenRouter / Groq / any compatible provider).
  2. Otherwise, or on failure -> Pollinations free text API (no key needed).

web_search(): provider chain
  Tavily -> Brave -> Serper -> DuckDuckGo (ddgs, no key).

Environment variables (all optional except none):
  LLM_API_KEY / GEMINI_API_KEY   LLM_BASE_URL   LLM_MODEL
  TAVILY_API_KEY   BRAVE_API_KEY   SERPER_API_KEY
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .utils import extract_json, load_env

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODEL = "openai"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) TrendPulse/1.0"}


def _env() -> dict:
    env = load_env(PROJECT_ROOT / ".env")
    env.update({k: v for k, v in os.environ.items() if v})
    return env


def _http_json(url, payload=None, headers=None, timeout=120):
    """POST/GET JSON with urllib. Returns parsed body or raises with body."""
    hdrs = dict(UA)
    if headers:
        hdrs.update(headers)
    if payload is not None:
        hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=hdrs, method="POST")
    else:
        req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="ignore")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"HTTP {e.code}: {body}") from None


# ------------------------------------------------------------------- chat ---
def _openai_chat(prompt, system, env, timeout):
    base = (env.get("LLM_BASE_URL") or "").rstrip("/")
    key = env.get("LLM_API_KEY") or env.get("GEMINI_API_KEY")
    if not base or not key:
        raise RuntimeError("no LLM_API_KEY / LLM_BASE_URL configured")
    model = env.get("LLM_MODEL") or "gemini-2.5-flash"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    data = _http_json(
        f"{base}/chat/completions",
        payload={"model": model, "messages": messages},
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout)
    content = (data.get("choices") or [{}])[0].get(
        "message", {}).get("content", "")
    if not content or not content.strip():
        raise ValueError("empty LLM response from OpenAI-compatible provider")
    return content


POLL_MIN_INTERVAL = 46.0        # free tier: 1 reply / 45s per IP
POLL_MODELS = ("openai-fast",)  # GPT-OSS 20B reasoning (only free model now)
_last_poll_call = 0.0
_poll_attempt = 0


def _pollinations_chat(prompt, system, timeout):
    global _last_poll_call, _poll_attempt
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # respect the anonymous free-tier rate limit (1 reply / 45s / IP)
    wait = POLL_MIN_INTERVAL - (time.time() - _last_poll_call)
    if wait > 0:
        time.sleep(wait)
    model = POLL_MODELS[_poll_attempt % len(POLL_MODELS)]
    _poll_attempt += 1
    data = _http_json(
        "https://text.pollinations.ai/openai",
        # max_tokens must exceed the model's hidden reasoning budget,
        # otherwise Pollinations returns 400 "exhausted output token limit"
        payload={"model": model, "messages": messages,
                 "max_tokens": 16000, "reasoning_effort": "low"},
        timeout=timeout)
    _last_poll_call = time.time()
    content = (data.get("choices") or [{}])[0].get(
        "message", {}).get("content", "")
    if not content or not content.strip():
        raise ValueError("empty LLM response from Pollinations")
    return content


def chat(prompt: str, system: str = None, timeout: int = 420, logger=None) -> str:
    """One chat completion. Tries keyed provider first, then free fallback.

    Free providers have hard input limits (Pollinations classic: 16,000
    chars). On 413 PAYLOAD_TOO_LARGE — or the model exhausting its OUTPUT
    token budget before finishing, which oversized prompts usually cause —
    the prompt is truncated in place and retried immediately instead of
    burning every attempt on a permanently-too-large request."""
    env = _env()
    attempts = []
    if env.get("LLM_API_KEY") or env.get("GEMINI_API_KEY"):
        attempts.append(("keyed", "openai", 3))
    attempts.append(("pollinations", "free", 5))

    last_err = None
    p = prompt
    for name, kind, max_tries in attempts:
        for attempt in range(1, max_tries + 1):
            try:
                if kind == "openai":
                    return _openai_chat(p, system, env, timeout)
                return _pollinations_chat(p, system, timeout)
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e)
                # oversized prompt / oversized answer: shrink and retry NOW
                if ("413" in msg or "Prompt too long" in msg
                        or "exhausted its output token limit" in msg):
                    newp = p[:12000] if len(p) > 12000 else p[:8000]
                    if len(newp) < len(p):
                        if logger:
                            logger.warning(
                                f"chat[{name}] prompt too large "
                                f"({len(p)} chars) — truncated to "
                                f"{len(newp)} and retrying immediately")
                        p = newp
                        continue
                if name == "pollinations":
                    m = re.search(r"[Rr]etry after (\d+)", msg)
                    if m:
                        wait = int(m.group(1)) + 3
                    elif "FREE_LIMIT" in msg or "Too many" in msg or \
                            "429" in msg or "400" in msg:
                        wait = POLL_MIN_INTERVAL
                    else:
                        wait = 12 * attempt
                else:
                    wait = 10 * attempt
                wait = min(wait, 180)
                if logger:
                    logger.warning(f"chat[{name}] attempt {attempt}/{max_tries} "
                                   f"failed: {msg[:150]} — retrying in {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"all chat providers failed: {str(last_err)[:200]}")


def chat_json(prompt: str, system: str = None, timeout: int = 420,
              retries: int = 3, logger=None):
    """Chat call that must return JSON; retries with a stricter instruction."""
    last_err = None
    for attempt in range(1, retries + 1):
        p = prompt
        if attempt > 1:
            p = (
                f"{prompt}\n\nCRITICAL: your previous answer was not parseable. "
                f"Return ONLY raw valid JSON. No markdown, no code fences, "
                f"no comments, no trailing commas, no text before or after."
            )
        try:
            content = chat(p, system=system, timeout=timeout, logger=logger)
            return extract_json(content)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if logger:
                logger.warning(f"chat_json attempt {attempt} failed: {str(e)[:160]}")
    raise last_err


# ------------------------------------------------------------- web search ---
def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.replace("www.", "")
    except Exception:  # noqa: BLE001
        return ""


def _norm(url, name, snippet, date=""):
    return {"url": url or "", "name": (name or "").strip(),
            "snippet": (snippet or "")[:500],
            "host_name": _host_of(url or ""), "date": date or ""}


def _tavily(query, num, env):
    data = _http_json(
        "https://api.tavily.com/search",
        payload={"api_key": env["TAVILY_API_KEY"], "query": query,
                 "max_results": num, "search_depth": "basic"},
        timeout=60)
    return [_norm(r.get("url"), r.get("title"), r.get("content"),
                  r.get("published_date", ""))
            for r in data.get("results", [])]


def _serper(query, num, env):
    data = _http_json(
        "https://google.serper.dev/search", payload={"q": query, "num": num},
        headers={"X-API-KEY": env["SERPER_API_KEY"]}, timeout=60)
    return [_norm(r.get("link"), r.get("title"), r.get("snippet"),
                  r.get("date", ""))
            for r in data.get("organic", [])]


def _brave(query, num, env):
    q = urllib.parse.quote(query)
    data = _http_json(
        f"https://api.search.brave.com/res/v1/web/search?q={q}&count={num}",
        headers={"X-Subscription-Token": env["BRAVE_API_KEY"],
                 "Accept": "application/json"}, timeout=60)
    out = []
    for r in (data.get("web") or {}).get("results", []):
        out.append(_norm(r.get("url"), r.get("title"),
                         r.get("description"), r.get("age", "")))
    return out


def _ddg(query, num, env=None):
    try:
        from ddgs import DDGS
    except ImportError:  # pragma: no cover
        from duckduckgo_search import DDGS  # type: ignore
    out = []
    try:
        for r in DDGS().news(query, region="us-en", max_results=num):
            out.append(_norm(r.get("url") or r.get("href"),
                             r.get("title"),
                             r.get("body") or r.get("excerpt"),
                             r.get("date", "")))
    except Exception:  # noqa: BLE001
        pass
    if not out:
        for r in DDGS().text(query, region="us-en", max_results=num):
            out.append(_norm(r.get("href"), r.get("title"), r.get("body")))
    return out


def web_search(query: str, num: int = 10, timeout: int = 120,
               logger=None) -> list:
    """Search results normalized as [{url, name, snippet, host_name, date}]."""
    env = _env()
    chain = []
    if env.get("TAVILY_API_KEY"):
        chain.append(("tavily", _tavily))
    if env.get("SERPER_API_KEY"):
        chain.append(("serper", _serper))
    if env.get("BRAVE_API_KEY"):
        chain.append(("brave", _brave))
    chain.append(("duckduckgo", _ddg))

    for name, fn in chain:
        for attempt in range(1, 3):
            try:
                res = fn(query, num, env)
                if res:
                    return res
            except Exception as e:  # noqa: BLE001
                if logger:
                    logger.warning(f"search[{name}] attempt {attempt} "
                                   f"failed: {str(e)[:120]}")
                time.sleep(6 * attempt)
    if logger:
        logger.warning(f"web_search: all providers failed for "
                       f"'{query[:60]}'")
    return []

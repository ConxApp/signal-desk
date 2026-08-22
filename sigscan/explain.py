"""
Plain-English "why is this trending" for each signal.

Two tiers:
  * heuristic (always on, no keys): assembles a short paragraph from the
    numbers, the headlines and the sample posts.
  * Claude (optional): if ANTHROPIC_API_KEY is set, the same facts are handed
    to Claude for a tighter summary + tone read. Any failure falls back to the
    heuristic text, so the run never depends on it.
"""
from __future__ import annotations

import json, os, re

MODEL = "claude-opus-5"

_STAGE = {
    "rising": "attention is rising",
    "peaking": "attention is at a peak",
    "fading": "attention is fading from a recent peak",
    "quiet": "attention is around normal",
}
_CHANNEL = {"wiki": "Wikipedia lookups", "news": "news coverage",
            "social": "Reddit chatter", "app": "App Store rank"}


def ai_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def _clean(s: str, n: int = 140) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return (s[: n - 1] + "…") if len(s) > n else s


def heuristic(item: dict, headlines: list, examples: list) -> dict:
    att = item.get("attention") or {}
    name = item.get("name", item.get("entity", "?"))
    stage = att.get("stage", "quiet")
    driver = att.get("driver", "wiki")
    ch = att.get("channels", {}) or {}
    parts = [f"{name}: {_STAGE.get(stage, 'attention is around normal')}"]
    if driver in ch:
        parts[0] += f", led by {_CHANNEL.get(driver, driver)} ({ch[driver]:+.1f}σ vs its own baseline)"
    cor = att.get("corroborating") or []
    if cor:
        parts[0] += ", backed by " + ", ".join(_CHANNEL.get(c, c) for c in cor)
    parts[0] += "."

    drivers = []
    if headlines:
        hs = "; ".join(f"“{_clean(h.get('title'), 90)}”" + (f" ({h.get('domain')})" if h.get("domain") else "")
                       for h in headlines[:2])
        parts.append(f"In the news: {hs}.")
        drivers.append("news: " + _clean(headlines[0].get("title"), 80))
    if examples:
        ex = sorted(examples, key=lambda e: -(e.get("score") or 0))[:2]
        quotes = "; ".join(f"“{_clean(e.get('text'), 110)}” (r/{e.get('sub')})" for e in ex)
        parts.append(f"What people are saying: {quotes}.")
        drivers.append("reddit: " + _clean(ex[0].get("text"), 80))
    if att.get("trends_hit"):
        drivers.append("Google trending search")
    if att.get("has_prices"):
        r5 = att.get("price_return_5d", 0.0) * 100
        tag = ""
        if "UNPRICED" in (att.get("flags") or []):
            tag = " — the stock has not reacted yet"
        elif "LATE" in (att.get("flags") or []):
            tag = " — the stock has already moved"
        parts.append(f"Stock: {r5:+.1f}% over 5 days{tag}.")

    tone = att.get("direction", "unknown")
    if tone == "unknown" and headlines:
        tone = "unknown"
    return {"summary": " ".join(parts), "tone": tone, "drivers": drivers[:3], "source": "heuristic"}


def with_claude(item: dict, headlines: list, examples: list, base: dict) -> dict:
    """Ask Claude for a tighter read. Returns base on any problem."""
    try:
        import anthropic
    except Exception:
        return base
    att = item.get("attention") or {}
    facts = {
        "name": item.get("name"), "ticker": item.get("ticker"), "company": item.get("company"),
        "stage": att.get("stage"), "driver": att.get("driver"), "channels_sigma": att.get("channels"),
        "flags": att.get("flags"), "price_return_5d": att.get("price_return_5d"),
        "headlines": [h.get("title") for h in headlines[:6]],
        "reddit_posts": [_clean(e.get("text"), 300) for e in examples[:6]],
        "reddit_direction": att.get("direction"),
    }
    system = (
        "You write two-sentence briefings for a stock-research dashboard about consumer brands "
        "that are getting unusual attention. Be concrete and plain. Sentence 1: the most likely "
        "reason attention is up (name the event/product if the evidence shows it; if the evidence "
        "does not say why, say so). Sentence 2: whether the tone of the public chatter/news is "
        "positive, negative or mixed for the business, and whether the stock appears to have "
        "reacted. Never give investment advice. Reply ONLY with JSON: "
        '{"summary": str (max 60 words), "tone": "positive"|"negative"|"mixed"|"unknown", '
        '"drivers": [up to 3 short strings]}'
    )
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL, max_tokens=600,
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
        )
        if getattr(resp, "stop_reason", "") == "refusal":
            return base
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0) if m else text)
        out = dict(base)
        if data.get("summary"):
            out["summary"] = str(data["summary"]).strip()
        if data.get("tone") in ("positive", "negative", "mixed", "unknown"):
            out["tone"] = data["tone"]
        if isinstance(data.get("drivers"), list):
            out["drivers"] = [str(d)[:100] for d in data["drivers"][:3]]
        out["source"] = "claude"
        return out
    except Exception as e:
        print(f"  ! claude summary failed: {type(e).__name__}")
        return base


def explain(item: dict, headlines: list, examples: list, use_ai: bool = False) -> dict:
    base = heuristic(item, headlines, examples)
    if use_ai:
        return with_claude(item, headlines, examples, base)
    return base

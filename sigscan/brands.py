"""
Discovery universe: config/brands.yaml -> Brand objects with compiled matchers.

A Brand is anything consumers talk about that maps to a listed stock (or is
flagged private so the scanner can say "trending, but no stock"). Matching is
deliberately forgiving about punctuation — "Disney+", "e.l.f.", "M&M's" all
work — and strict about word boundaries so "On" never matches "on".
"""
from __future__ import annotations

import os, re
from dataclasses import dataclass, field

import yaml

_ALNUM = r"A-Za-z0-9"


def compile_term(term: str, plural: bool = True) -> re.Pattern:
    """Phrase -> case-insensitive regex bounded by non-alphanumerics.

    'Hoka'      matches Hoka / Hokas / HOKA, not 'hokage'
    'Disney+'   matches 'Disney+ is' (a \\b would fail after '+')
    're:...'    is taken as a raw regex
    """
    if term.startswith("re:"):
        return re.compile(term[3:], re.I)
    body = re.escape(term.strip())
    tail = r"(?:s|es|'s)?" if plural and re.search(r"[A-Za-z]$", term.strip()) else ""
    return re.compile(rf"(?<![{_ALNUM}]){body}{tail}(?![{_ALNUM}])", re.I)


@dataclass
class Brand:
    key: str
    name: str
    ticker: str = ""
    company: str = ""
    sector: str = "other"
    wiki: str = ""
    terms: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    context: list = field(default_factory=list)
    apps: list = field(default_factory=list)
    private: bool = False
    news_query: str = ""

    def __post_init__(self):
        self.ticker = (self.ticker or "").strip()
        self.private = bool(self.private) or not self.ticker
        self._inc = [compile_term(t) for t in self.terms if t]
        self._exc = [compile_term(t, plural=False) for t in self.exclude if t]
        self._ctx = [compile_term(t, plural=False) for t in self.context if t]
        if not self.news_query:
            # GDELT query: the display name's first segment in quotes
            first = re.split(r"\s*/\s*|\s*\(", self.name)[0].strip()
            self.news_query = f'"{first}"' if first else ""

    def matches(self, text: str) -> bool:
        if not text:
            return False
        if any(x.search(text) for x in self._exc):
            return False
        if not any(i.search(text) for i in self._inc):
            return False
        if self._ctx and not any(c.search(text) for c in self._ctx):
            return False
        return True

    def lint(self) -> list:
        problems = []
        for t in self.terms:
            bare = t[3:] if t.startswith("re:") else t
            if len(bare.strip()) <= 2:
                problems.append(f"{self.key}: term {t!r} is too short")
            if " " not in bare and bare.lower() in _COMMON and not self.context:
                problems.append(f"{self.key}: term {t!r} is a common word — add context or a product name")
        if not self.terms:
            problems.append(f"{self.key}: no terms")
        return problems


_COMMON = {
    "on", "off", "up", "rare", "peak", "core", "arm", "gap", "all", "one", "open",
    "block", "square", "shop", "match", "rush", "wing", "root", "target", "apple",
    "ring", "shark", "prime", "stanley", "monster", "ghost", "nature", "mother",
    "dot", "good", "boom", "member", "tate", "frosty", "fuzzy", "siren", "hinge",
    "tinder", "bumble", "affirm", "wise", "zip", "chime", "grab", "uber", "sphere",
    "lucid", "corona", "celsius", "dove", "crest", "dawn", "tide", "olay", "axe",
}


def load_brands(path: str) -> tuple[list, dict]:
    """Returns (brands, defaults). Unknown keys in YAML are ignored."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    out = []
    allowed = set(Brand.__dataclass_fields__)
    for b in raw.get("brands", []) or []:
        if not isinstance(b, dict) or not b.get("key"):
            continue
        kw = {k: v for k, v in b.items() if k in allowed}
        for lk in ("terms", "exclude", "context", "apps"):
            if kw.get(lk) is None:
                kw[lk] = []
        out.append(Brand(**kw))
    return out, raw.get("defaults", {}) or {}


def app_index(brands: list) -> dict:
    """lower-cased App Store app name -> brand key."""
    idx = {}
    for b in brands:
        for a in b.apps or []:
            idx[a.strip().lower()] = b.key
    return idx


def match_app_name(name: str, idx: dict) -> str | None:
    """Exact (case-insensitive) or prefix match of an App Store name to a brand key."""
    n = (name or "").strip().lower()
    if not n:
        return None
    if n in idx:
        return idx[n]
    # 'ChatGPT' vs 'ChatGPT: AI chat' — try the part before ':' / ' - ' / ' – '
    short = re.split(r"\s*[:\-–—|]\s*", n)[0].strip()
    if short in idx:
        return idx[short]
    for k, v in idx.items():
        if n.startswith(k + " ") or n.startswith(k + ":"):
            return v
    return None


def tickers(brands: list) -> dict:
    """ticker -> [brand keys] (private brands excluded)."""
    out = {}
    for b in brands:
        if b.ticker:
            out.setdefault(b.ticker, []).append(b.key)
    return out

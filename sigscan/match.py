"""
Entity matching and text signal extraction.

The unglamorous part that decides whether the whole thing works. Brand names
are terrible search terms ("On" running shoes, "Rare" beauty, "Oura"). Product
names are excellent ones ("Cloudmonster", "Labubu", "Speedgoat"). The config
format pushes you towards product-level terms and lets you veto false matches.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Purchase-intent and scarcity lexicons.
#
# For consumer-product investing these matter far more than sentiment. Nobody
# writes a glowing review before they buy; they write "just copped these" and
# "sold out everywhere", and those phrases lead revenue by a quarter.
# ---------------------------------------------------------------------------

INTENT = [
    r"\bjust (?:bought|ordered|got|picked up|copped|grabbed)\b",
    r"\b(?:bought|ordered|copped|preordered|pre-ordered)\b",
    r"\bon (?:my|the) way\b", r"\barrived today\b", r"\bunboxing\b",
    r"\badded to (?:cart|basket)\b", r"\bcheckout\b",
    r"\b(?:my|our) (?:kid|daughter|son|wife|husband|gf|bf) wants?\b",
    r"\bgetting (?:a|another|more)\b", r"\bworth (?:it|the money|every penny)\b",
    r"\bbuying (?:a|another|more|again)\b", r"\breplaced my\b",
]

SCARCITY = [
    r"\bsold out\b", r"\bout of stock\b", r"\brestock", r"\bback in stock\b",
    r"\bcan'?t find\b", r"\bwait ?list\b", r"\bqueue", r"\bscalper",
    r"\bresell(?:ing|ers?)?\b", r"\bstock ?x\b", r"\bwent for \$?\d+ on ebay\b",
    r"\beverywhere is (?:out|sold)\b", r"\bimpossible to (?:get|find|buy)\b",
    r"\bdrop(?:ped|s)? (?:today|tomorrow|at \d)", r"\blimited (?:edition|run|drop)\b",
]

NEGATIVE = [
    r"\breturn(?:ed|ing)? (?:it|them|mine)\b", r"\brefund", r"\bregret buying\b",
    r"\boverrated\b", r"\bover ?hyped\b", r"\bnot worth\b", r"\bwaste of money\b",
    r"\bfell apart\b", r"\bbroke after\b", r"\bdupe\b", r"\bknock ?off\b",
    r"\bcheaper alternative\b", r"\bswitching (?:back |away )?to\b",
    r"\bdisappointed\b", r"\bstopped (?:using|wearing)\b", r"\bQC issues?\b",
]

_INTENT_RE = re.compile("|".join(INTENT), re.I)
_SCARCITY_RE = re.compile("|".join(SCARCITY), re.I)
_NEGATIVE_RE = re.compile("|".join(NEGATIVE), re.I)


_COMMON_WORDS = {
    "on", "off", "up", "rare", "peak", "core", "arm", "gap", "all", "one",
    "open", "block", "square", "shop", "match", "rush", "wing", "root", "lulu",
}


def _pluralise(pattern: str) -> str:
    """Let a trailing word-boundary pattern also match its plural.

    Written as \\bcloudmonster\\b, the pattern misses "Cloudmonsters", which is
    how people actually write it. This is the single most common way a
    hand-written watchlist silently under-counts.
    """
    if pattern.endswith(r"\b") and not pattern.endswith(r"s\b"):
        return pattern[:-2] + r"(?:s|es)?\b"
    return pattern


@dataclass
class Entity:
    key: str
    ticker: str
    name: str
    kind: str                    # product | platform | tech
    include: list                # regex strings, case-insensitive
    exclude: list                # regex strings that veto a match
    wikipedia: str = ""
    news_query: str = ""
    note: str = ""

    def __post_init__(self):
        self._inc = [re.compile(_pluralise(p), re.I) for p in self.include]
        self._exc = [re.compile(p, re.I) for p in self.exclude] if self.exclude else []

    def lint(self) -> list:
        """Warn about include patterns likely to generate junk matches."""
        problems = []
        for pat in self.include:
            bare = re.sub(r"\\b|\(\?:|[()?\\]", "", pat)
            if len(bare) <= 3:
                problems.append(f"{self.key}: pattern {pat!r} is very short — expect false matches")
            if bare.lower() in _COMMON_WORDS:
                problems.append(f"{self.key}: pattern {pat!r} is a common English word — "
                                f"add context or use a product name instead")
        return problems

    def matches(self, text: str) -> bool:
        if not text:
            return False
        if any(x.search(text) for x in self._exc):
            return False
        return any(i.search(text) for i in self._inc)


def text_signals(text: str) -> dict:
    """Count intent / scarcity / negative phrase hits in one document."""
    return {
        "intent": len(_INTENT_RE.findall(text or "")),
        "scarcity": len(_SCARCITY_RE.findall(text or "")),
        "negative": len(_NEGATIVE_RE.findall(text or "")),
    }


# ---------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------

def make_sentiment():
    """VADER if available, otherwise a small built-in lexicon fallback.

    VADER is tuned for social text and handles negation, intensifiers and
    punctuation emphasis. It is not great at product slang ("sick", "insane",
    "stupid good" are positive in this domain) so we patch the lexicon.
    """
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        an = SentimentIntensityAnalyzer()
        an.lexicon.update({
            "sick": 2.0, "insane": 1.5, "unreal": 2.0, "goated": 3.0,
            "fire": 2.0, "peak": 1.5, "cop": 1.5, "copped": 1.5,
            "banger": 2.5, "clean": 1.2, "solid": 1.5, "legit": 1.8,
            "mid": -1.8, "cooked": -1.5, "ass": -2.0, "meh": -1.5,
            "overhyped": -2.5, "dupe": -1.0, "knockoff": -1.5,
        })
        return lambda t: an.polarity_scores(t or "")["compound"]
    except Exception:
        pos = set("love great amazing awesome best perfect excellent good happy "
                  "comfy comfortable recommend favourite favorite obsessed worth "
                  "sick insane unreal fire legit solid banger goated".split())
        neg = set("hate bad awful terrible worst broke broken cheap disappointed "
                  "refund return regret overrated overhyped uncomfortable mid "
                  "waste junk garbage flimsy".split())

        def score(t):
            words = re.findall(r"[a-z']+", (t or "").lower())
            if not words:
                return 0.0
            p = sum(w in pos for w in words)
            n = sum(w in neg for w in words)
            if p + n == 0:
                return 0.0
            return round((p - n) / (p + n), 3)
        return score

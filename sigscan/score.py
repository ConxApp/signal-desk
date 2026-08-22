"""
Signal scoring engine.

Turns raw daily mention counts into a defensible answer to:
  "is chatter about this unusually high right now, is it broad, is it early,
   and has the market already priced it in?"

Everything here is pure functions over numbers so it can be unit-tested
without touching the network.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Sequence

# ---------------------------------------------------------------------------
# Robust statistics
# ---------------------------------------------------------------------------

def _median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def robust_z(series: Sequence[float], value: float, min_history: int = 14) -> float:
    """Median/MAD z-score.

    We use median + MAD instead of mean + stdev because a single past viral
    spike poisons a mean-based baseline for weeks afterwards — the thing we
    most want to detect is exactly the thing that would break the detector.

    0.6745 rescales MAD so the result is comparable to a normal-distribution
    standard deviation.
    """
    if len(series) < min_history:
        return 0.0
    med = _median(series)
    mad = _median([abs(x - med) for x in series])
    if mad < 1e-9:
        # Flat history. Fall back to a scaled mean-absolute-deviation so a
        # genuine jump off a zero baseline still registers instead of
        # dividing by ~0 and returning infinity.
        mad = sum(abs(x - med) for x in series) / max(len(series), 1)
        if mad < 1e-9:
            return 6.0 if value > med else 0.0
        mad = max(mad, 1e-6)
    z = 0.6745 * (value - med) / mad
    # Clamp. Beyond ~10 sigma the number is noise from a near-degenerate
    # baseline and a runaway value would distort every downstream average.
    return max(-12.0, min(12.0, z))


def log_share(mentions: float, sampled_docs: float) -> float:
    """Mentions as a log-share of everything sampled that day.

    Two corrections in one:
      * dividing by the denominator removes 'the whole platform was busy
        today' and 'this subreddit has grown 3x since January'
      * log1p makes the measure multiplicative, so 10 -> 30 is the same size
        of move as 100 -> 300, which is how attention actually behaves.
    """
    denom = max(sampled_docs, 1.0)
    return math.log1p(1000.0 * mentions / denom)


def herfindahl(counts: Sequence[float]) -> float:
    """Concentration of mentions across communities. 1.0 = all in one place."""
    total = sum(counts)
    if total <= 0:
        return 1.0
    return sum((c / total) ** 2 for c in counts)


def breadth_score(distinct_communities: int, distinct_authors: int,
                  concentration: float) -> float:
    """0-1. How widely spread the chatter is.

    This is the single most useful false-positive filter. One thread going
    viral in one subreddit produces a big volume spike with terrible breadth.
    A product actually entering the culture shows up in many communities from
    many accounts at once.
    """
    comm = min(distinct_communities / 8.0, 1.0)
    auth = min(distinct_authors / 60.0, 1.0)
    spread = 1.0 - min(concentration, 1.0)
    return round(0.4 * comm + 0.3 * auth + 0.3 * spread, 4)


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

@dataclass
class DailyObservation:
    """One entity, one day, already aggregated."""
    date: str
    entity: str
    consumer_mentions: int = 0
    consumer_denominator: int = 0
    finance_mentions: int = 0
    finance_denominator: int = 0
    distinct_communities: int = 0
    distinct_authors: int = 0
    community_counts: list = field(default_factory=list)
    sentiment_mean: float = 0.0        # -1..1
    intent_hits: int = 0               # "bought", "ordered", "copped"
    scarcity_hits: int = 0             # "sold out", "restock", "can't find"
    negative_hits: int = 0             # "returned", "refund", "overrated"
    wiki_views: int = 0
    news_articles: int = 0
    close: float = 0.0
    volume: float = 0.0
    app_rank: float = 0.0              # App Store top-free rank (0 = not charting)
    trends_hit: int = 0                # appeared in Google Trends daily feed (approx traffic)
    missing: tuple = ()                # channels NOT observed this day: any of wiki, news, app
                                       # (absent != zero; the attention engine skips these days)


@dataclass
class Signal:
    entity: str
    date: str
    social_z: float
    finance_z: float
    wiki_z: float
    news_z: float
    breadth: float
    acceleration: float
    lead_ratio: float
    sentiment: float
    intent_rate: float
    scarcity_rate: float
    unpriced: float
    direction: str
    breadth_gate: float
    price_return_5d: float
    volume_z: float
    arb_score: float
    flags: list
    why: list

    def to_dict(self):
        return asdict(self)


def _pct_change(series: Sequence[float], lookback: int) -> float:
    if len(series) <= lookback or series[-lookback - 1] <= 0:
        return 0.0
    return (series[-1] / series[-lookback - 1]) - 1.0


def score_entity(history: Sequence[DailyObservation],
                 weights: dict | None = None,
                 baseline_days: int = 45) -> Signal | None:
    """Score the most recent day given its own trailing history."""
    if not history:
        return None
    w = {
        "social": 0.34,
        "breadth": 0.18,
        "acceleration": 0.12,
        "lead": 0.14,
        "corroboration": 0.10,
        "unpriced": 0.12,
    }
    if weights:
        w.update(weights)

    today = history[-1]
    past = history[:-1][-baseline_days:]

    cons_series = [log_share(o.consumer_mentions, o.consumer_denominator) for o in past]
    cons_today = log_share(today.consumer_mentions, today.consumer_denominator)
    social_z = robust_z(cons_series, cons_today)

    fin_series = [log_share(o.finance_mentions, o.finance_denominator) for o in past]
    fin_today = log_share(today.finance_mentions, today.finance_denominator)
    finance_z = robust_z(fin_series, fin_today)

    wiki_z = robust_z([math.log1p(o.wiki_views) for o in past], math.log1p(today.wiki_views))
    news_z = robust_z([math.log1p(o.news_articles) for o in past], math.log1p(today.news_articles))

    breadth = breadth_score(today.distinct_communities, today.distinct_authors,
                            herfindahl(today.community_counts))

    # Acceleration: is the z-score itself still climbing? Something at z=3 and
    # rising is a live trend; z=5 and falling is a news cycle you already missed.
    # Acceleration compares the last 3 days against the 4 before them, in the
    # same normalised units as the z-score. Comparing single days (an earlier
    # version of this) was far too noisy — it fired COOLING on names that were
    # still climbing, just with a slightly lower final day.
    accel = 0.0
    if len(history) >= 8:
        recent = [log_share(o.consumer_mentions, o.consumer_denominator) for o in history[-3:]]
        prior = [log_share(o.consumer_mentions, o.consumer_denominator) for o in history[-7:-3]]
        scale = _median([abs(x - _median(cons_series)) for x in cons_series]) if cons_series else 0.0
        scale = max(scale, 1e-3)
        accel = 0.6745 * (sum(recent) / len(recent) - sum(prior) / len(prior)) / scale
        accel = max(-8.0, min(8.0, accel))

    # Lead ratio: consumer chatter running ahead of finance chatter is the
    # whole thesis. Once the investing subs catch up, the edge is priced.
    # Breadth GATES the volume signal rather than merely adding to it.
    # Without this, one thread with 4,000 comments in a single subreddit
    # produces a 12-sigma reading and outranks a genuine cross-community
    # trend. Volume without spread is not evidence.
    gate = max(0.15, min(breadth / 0.45, 1.0))

    lead_ratio = (max(social_z, 0.0) * gate + 0.5) / (max(finance_z, 0.0) + 1.0)
    lead_ratio = min(lead_ratio, 6.0)

    total_hits = max(today.intent_hits + today.scarcity_hits + today.negative_hits, 1)
    intent_rate = today.intent_hits / max(today.consumer_mentions, 1)
    scarcity_rate = today.scarcity_hits / max(today.consumer_mentions, 1)

    closes = [o.close for o in history if o.close > 0]
    vols = [o.volume for o in history if o.volume > 0]
    ret5 = _pct_change(closes, 5) if len(closes) > 5 else 0.0
    volume_z = robust_z([math.log1p(v) for v in vols[:-1]], math.log1p(vols[-1])) if len(vols) > 15 else 0.0

    # Unpriced: high attention that the tape has not reacted to yet.
    price_quiet = 1.0 - min(abs(ret5) / 0.12, 1.0)
    vol_quiet = 1.0 - min(max(volume_z, 0.0) / 3.0, 1.0)
    unpriced = round(0.6 * price_quiet + 0.4 * vol_quiet, 4)

    # --- composite -----------------------------------------------------
    n_social = min(max(social_z, 0.0) * gate / 4.0, 1.0)
    n_accel = min(max(accel, 0.0) / 2.0, 1.0)
    n_lead = min(lead_ratio / 3.0, 1.0)
    corroboration = min((max(wiki_z, 0.0) + max(news_z, 0.0)) / 6.0, 1.0)

    arb = (w["social"] * n_social + w["breadth"] * breadth +
           w["acceleration"] * n_accel + w["lead"] * n_lead +
           w["corroboration"] * corroboration + w["unpriced"] * unpriced)

    # Direction is reported separately from magnitude. A brand melting down is
    # a real, loud, high-attention event — it just is not the same finding as a
    # brand taking off, and averaging the two into one number hides which is
    # which.
    sent = today.sentiment_mean
    pos_hits = today.intent_hits + today.scarcity_hits
    neg_hits = today.negative_hits
    hit_tilt = (pos_hits - neg_hits) / max(pos_hits + neg_hits, 1)
    if sent < -0.2 or hit_tilt < -0.35:
        direction = "negative"
    elif sent > 0.15 and hit_tilt > 0.2:
        direction = "positive"
    else:
        direction = "mixed"

    tilt = 1.0 + 0.15 * sent + 0.10 * min(scarcity_rate * 4, 1.0)
    if social_z >= 3.0 and breadth < 0.45:
        # A big number from one corner of one community. Penalised explicitly
        # rather than left to rank on raw volume.
        tilt *= 0.55
    if direction == "negative":
        # Downweight hard: this is not a long idea, and letting it sit near the
        # top of a list you scan for opportunities is how you misread the page.
        tilt *= 0.55
    arb = round(max(0.0, min(arb * tilt, 1.0)) * 100, 1)

    flags, why = [], []
    if social_z >= 3.0 and breadth >= 0.45:
        flags.append("BROAD_SPIKE")
        why.append(f"chatter {social_z:.1f}σ above its own 45-day baseline, spread across "
                   f"{today.distinct_communities} communities and {today.distinct_authors} accounts")
    if social_z >= 3.0 and breadth < 0.45:
        flags.append("NARROW_SPIKE")
        why.append(f"chatter is {social_z:.1f}σ high but concentrated — likely one viral thread, not a trend")
    if lead_ratio >= 2.5 and social_z >= 2.0 and breadth >= 0.45:
        flags.append("AHEAD_OF_THE_STREET")
        why.append("consumer chatter is running well ahead of investing-forum chatter")
    if finance_z >= 3.0 and social_z < 2.0:
        flags.append("FINANCE_ONLY")
        why.append("the investing forums are talking but consumers are not — this is a stock story, not a product story")
    if unpriced >= 0.7 and social_z >= 2.5 and breadth >= 0.45:
        flags.append("UNPRICED")
        why.append(f"price is flat ({ret5*100:+.1f}% over 5 days) and volume is normal despite the attention")
    if social_z >= 2.0 and ret5 > 0.15:
        flags.append("LATE")
        why.append(f"already ran {ret5*100:+.1f}% in 5 days — the move may be behind you")
    if scarcity_rate > 0.08 and today.consumer_mentions >= 10 and direction != "negative":
        flags.append("SCARCITY")
        why.append("unusual volume of sold-out / restock / can't-find language")
    if sent < -0.25 and today.consumer_mentions >= 10:
        flags.append("NEGATIVE_TURN")
        why.append("sentiment has turned negative")
    if accel < -1.0 and social_z >= 2.0 and social_z < 5.0:
        flags.append("COOLING")
        why.append("still elevated but decelerating")

    return Signal(
        entity=today.entity, date=today.date,
        social_z=round(social_z, 2), finance_z=round(finance_z, 2),
        wiki_z=round(wiki_z, 2), news_z=round(news_z, 2),
        breadth=breadth, acceleration=round(accel, 2),
        lead_ratio=round(lead_ratio, 2), sentiment=round(sent, 3),
        intent_rate=round(intent_rate, 3), scarcity_rate=round(scarcity_rate, 3),
        unpriced=unpriced, direction=direction, breadth_gate=round(gate, 3),
        price_return_5d=round(ret5, 4),
        volume_z=round(volume_z, 2), arb_score=arb,
        flags=flags, why=why,
    )

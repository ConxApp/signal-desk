"""
Attention scoring — the part of the engine that works on day one, with or
without Reddit, for the whole discovery universe.

It answers, per brand:
  * how unusual is attention right now (Wikipedia lookups, news volume, Reddit
    chatter, App Store rank, Google Trends) against the brand's own baseline
  * which channel is driving it, and do others corroborate it
  * is it rising, peaking or fading
  * has the stock already moved (5d / 20d return, volume) — "unpriced"
  * what happened to the stock after this brand's previous attention spikes

Each channel is judged on the days it was actually OBSERVED. Wikipedia runs a
day behind, the App Store chart only exists from the day we started reading it,
Reddit only on sampled days — a day with no observation is not a zero, it is
simply absent, and the channel's "today" is its most recent observed day.
A DailyObservation lists absent channels in `missing`.

Everything is pure functions over numbers so it can be unit-tested offline.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, asdict, field
from typing import Sequence

from sigscan.score import (DailyObservation, robust_z, log_share, _median,
                           breadth_score, herfindahl)

MIN_OBS = 14          # observed days a channel needs before it gets a z-score


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------

def rolling_z(series: Sequence[float], baseline_days: int = 45, min_history: int = MIN_OBS) -> list:
    """Robust z of each point against the window before it. 0.0 while the
    window is shorter than min_history."""
    out = []
    for i, v in enumerate(series):
        past = series[max(0, i - baseline_days):i]
        out.append(robust_z(past, v, min_history) if len(past) >= min_history else 0.0)
    return out


def acceleration(series: Sequence[float], baseline: Sequence[float]) -> float:
    """Last 3 observations vs the 4 before, in robust-sigma units of the baseline."""
    if len(series) < 8:
        return 0.0
    recent, prior = series[-3:], series[-7:-3]
    base = list(baseline) if baseline else list(series[:-3])
    med = _median(base)
    scale = _median([abs(x - med) for x in base]) if base else 0.0
    scale = max(scale, 1e-3)
    a = 0.6745 * (sum(recent) / len(recent) - sum(prior) / len(prior)) / scale
    return max(-8.0, min(8.0, a))


def trend_stage(z_series: Sequence[float], accel: float) -> str:
    """quiet | rising | peaking | fading, judged over the last 14 observations."""
    if not z_series:
        return "quiet"
    recent = list(z_series[-14:])
    z_today = recent[-1]
    peak = max(recent)
    if peak < 2.0:
        return "quiet"
    if z_today >= 2.0 and accel > 0.8:
        return "rising"
    if z_today >= 1.5 and z_today >= 0.75 * peak:
        return "peaking"
    return "fading"


def app_signal(rank: float) -> float:
    """App Store rank -> attention value (higher = better). 0 when not charting."""
    if not rank or rank <= 0:
        return 0.0
    return max(0.0, math.log(201.0 / min(rank, 200.0)))


def forward_returns(dates: Sequence[str], closes: Sequence[float], spike_dates: Sequence[str],
                    horizon: int = 10) -> list:
    """For each spike date, the % change from the first close on/after the spike
    to the close `horizon` trading days later. Skips spikes without enough data."""
    pairs = [(d, c) for d, c in zip(dates, closes) if c and c > 0]
    if len(pairs) < horizon + 1:
        return []
    ds = [d for d, _ in pairs]
    cs = [c for _, c in pairs]
    out = []
    for sd in spike_dates:
        i = bisect_left(ds, sd)
        if i + horizon < len(cs) and cs[i] > 0:
            out.append(cs[i + horizon] / cs[i] - 1.0)
    return out


def spike_dates(dates: Sequence[str], z_series: Sequence[float], threshold: float = 2.5,
                gap: int = 5, exclude_last: int = 10) -> list:
    """Dates where z crossed the threshold, de-clustered (one per `gap` days),
    ignoring the most recent `exclude_last` days (their outcome is unknown)."""
    out, last_i = [], -10**9
    n = len(dates)
    for i, z in enumerate(z_series):
        if i >= n - exclude_last:
            break
        if z >= threshold and i - last_i >= gap:
            out.append(dates[i])
            last_i = i
    return out


def _observed(o: DailyObservation, channel: str) -> bool:
    miss = getattr(o, "missing", ()) or ()
    if channel in miss:
        return False
    if channel == "social":
        return o.consumer_denominator > 0
    if channel == "price":
        return o.close > 0
    return True


def channel_series(history: Sequence[DailyObservation], channel: str) -> tuple:
    """(dates, values) of a channel over the days it was observed."""
    dates, vals = [], []
    for o in history:
        if not _observed(o, channel):
            continue
        if channel == "wiki":
            v = math.log1p(max(o.wiki_views, 0))
        elif channel == "news":
            v = math.log1p(max(o.news_articles, 0.0))
        elif channel == "app":
            v = app_signal(o.app_rank or 0.0)
        elif channel == "social":
            v = log_share(o.consumer_mentions, o.consumer_denominator)
        else:
            continue
        dates.append(o.date)
        vals.append(v)
    return dates, vals


# ---------------------------------------------------------------------------
# result object
# ---------------------------------------------------------------------------

@dataclass
class Attention:
    entity: str
    date: str
    attention_z: float
    driver: str                 # wiki | news | social | app
    channels: dict              # channel -> z at its latest observation
    corroborating: list         # channels (other than driver) with z >= 2
    stage: str
    acceleration: float
    trends_hit: int
    finance_noticed: bool
    price_return_5d: float
    price_return_20d: float
    volume_z: float
    unpriced: float
    has_prices: bool
    has_social: bool
    direction: str              # positive | negative | mixed | unknown
    attention_score: float
    past_spikes: dict           # {"n": int, "median_10d": float, "hit_rate": float}
    flags: list = field(default_factory=list)
    why: list = field(default_factory=list)
    z_spark: list = field(default_factory=list)       # driver channel z, last 30 observations
    driver_spark: list = field(default_factory=list)  # driver channel raw values, last 30 observations
    price_spark: list = field(default_factory=list)   # closes, last 30 trading days
    channel_dates: dict = field(default_factory=dict)  # channel -> date of its latest observation
    app_rank: float = 0.0

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def score_attention(history: Sequence[DailyObservation], baseline_days: int = 45,
                    finance_noticed: bool = False) -> Attention | None:
    """Score the most recent observation of one entity across every channel."""
    if not history:
        return None
    today = history[-1]
    dates_all = [o.date for o in history]

    # --- channel series (observed days only) ---------------------------------
    chan = {}
    for ch in ("wiki", "news", "app", "social"):
        d, v = channel_series(history, ch)
        if ch == "app":
            if len(v) >= MIN_OBS:
                chan[ch] = (d, v)
        elif v and any(x > 0 for x in v):
            chan[ch] = (d, v)
    has_social = "social" in chan
    if not chan:
        return None

    zs = {ch: rolling_z(v, baseline_days) for ch, (d, v) in chan.items()}

    # social is gated by breadth, exactly as in the watchlist engine
    gate = 1.0
    if has_social:
        last_social = next(o for o in reversed(history) if _observed(o, "social"))
        breadth = breadth_score(last_social.distinct_communities, last_social.distinct_authors,
                                herfindahl(last_social.community_counts))
        gate = max(0.15, min(breadth / 0.45, 1.0))

    channel_z, channel_dates = {}, {}
    for ch, z_series in zs.items():
        z = z_series[-1]
        if ch == "social" and z > 0:
            z *= gate
        if ch == "news":
            z *= 0.9          # news is noisier and often a lagging echo
        channel_z[ch] = round(z, 2)
        channel_dates[ch] = chan[ch][0][-1]

    driver = max(channel_z, key=lambda c: channel_z[c])
    base_z = max(channel_z[driver], 0.0)
    corroborating = [c for c, z in channel_z.items() if c != driver and z >= 2.0]
    attention_z = round(min(base_z + min(0.4 * len(corroborating), 1.2), 12.0), 2)

    drv_dates, drv_series = chan[driver]
    drv_z = zs[driver]
    accel = acceleration(drv_series, drv_series[-(baseline_days + 1):-1])
    stage = trend_stage(drv_z, accel)

    # --- price reaction -----------------------------------------------------
    closes = [o.close for o in history if o.close > 0]
    vols = [o.volume for o in history if o.volume > 0]
    has_prices = len(closes) >= 10

    def pct(lookback):
        if len(closes) <= lookback or closes[-lookback - 1] <= 0:
            return 0.0
        return closes[-1] / closes[-lookback - 1] - 1.0

    ret5, ret20 = pct(5), pct(20)
    volume_z = robust_z([math.log1p(v) for v in vols[:-1]], math.log1p(vols[-1])) if len(vols) > 15 else 0.0
    if has_prices:
        price_quiet = 1.0 - min(abs(ret5) / 0.12, 1.0)
        vol_quiet = 1.0 - min(max(volume_z, 0.0) / 3.0, 1.0)
        unpriced = round(0.6 * price_quiet + 0.4 * vol_quiet, 4)
    else:
        unpriced = 0.5   # unknown: neutral

    # --- what happened after past spikes ---------------------------------
    spikes = spike_dates(drv_dates, drv_z)
    fwd = forward_returns(dates_all, [o.close for o in history], spikes, horizon=10) if has_prices else []
    past = {"n": len(fwd),
            "median_10d": round(_median(fwd), 4) if fwd else 0.0,
            "hit_rate": round(sum(1 for r in fwd if r > 0) / len(fwd), 2) if fwd else 0.0}

    # --- direction (only when we have text) --------------------------------
    direction = "unknown"
    if has_social:
        ls = next(o for o in reversed(history) if _observed(o, "social"))
        if ls.consumer_mentions >= 5:
            pos_hits = ls.intent_hits + ls.scarcity_hits
            neg_hits = ls.negative_hits
            tilt = (pos_hits - neg_hits) / max(pos_hits + neg_hits, 1)
            s = ls.sentiment_mean
            if s < -0.2 or tilt < -0.35:
                direction = "negative"
            elif s > 0.15 and tilt > 0.2:
                direction = "positive"
            else:
                direction = "mixed"

    # --- composite 0-100 ---------------------------------------------------
    n_att = min(attention_z / 4.0, 1.0)
    corroboration = min(len(corroborating) / 2.0, 1.0)
    n_accel = min(max(accel, 0.0) / 2.0, 1.0)
    score = 0.45 * n_att + 0.20 * corroboration + 0.15 * n_accel + 0.20 * unpriced
    score *= {"rising": 1.0, "peaking": 0.9, "fading": 0.6, "quiet": 0.75}[stage]
    if direction == "negative":
        score *= 0.6
    trends_hit = int(today.trends_hit or 0)
    if trends_hit:
        score += 0.05
    attention_score = round(max(0.0, min(score, 1.0)) * 100, 1)

    # --- flags & plain-English reasons -------------------------------------
    flags, why = [], []
    names = {"wiki": "Wikipedia lookups", "news": "news coverage", "social": "Reddit chatter", "app": "App Store rank"}
    app_rank = float(today.app_rank or 0.0) if _observed(today, "app") else 0.0
    if attention_z >= 3.0:
        flags.append("ATTENTION_SPIKE")
        why.append(f"{names[driver]} are {channel_z[driver]:.1f}σ above this name's own {baseline_days}-day baseline")
    if corroborating:
        flags.append("MULTI_CHANNEL")
        why.append("confirmed by " + ", ".join(names[c] for c in corroborating))
    if trends_hit:
        flags.append("TRENDING_SEARCH")
        why.append("appeared in Google's daily trending searches")
    if "app" in channel_z and channel_z["app"] >= 2.0:
        flags.append("APP_CLIMBING")
        why.append(f"app is climbing the App Store chart (rank {int(app_rank) if app_rank else '?'})")
    elif app_rank:
        why.append(f"app is on the App Store top-100 chart (rank {int(app_rank)}) — baseline still forming")
    if finance_noticed:
        flags.append("FINANCE_NOTICED")
        why.append("the ticker is trending on Yahoo Finance — the investing crowd is already looking")
    if has_prices and unpriced >= 0.7 and attention_z >= 2.5:
        flags.append("UNPRICED")
        why.append(f"price is flat ({ret5*100:+.1f}% over 5 days) and volume normal despite the attention")
    if has_prices and attention_z >= 2.0 and ret5 > 0.15:
        flags.append("LATE")
        why.append(f"already ran {ret5*100:+.1f}% in 5 days — the move may be behind you")
    if stage == "fading" and max(drv_z[-14:] or [0]) >= 3.0:
        flags.append("FADING")
        why.append("attention is coming off a recent peak")
    if stage == "rising" and attention_z >= 2.0:
        flags.append("RISING")
        why.append(f"still accelerating ({accel:+.1f}σ over the last 3 days vs the 4 before)")
    if direction == "negative":
        flags.append("NEGATIVE_TONE")
        why.append("the consumer posts skew negative (returns / refunds / overhyped)")
    if past["n"] >= 3:
        why.append(f"after its last {past['n']} attention spikes the stock was up 10 days later "
                   f"{int(past['hit_rate']*100)}% of the time (median {past['median_10d']*100:+.1f}%)")

    return Attention(
        entity=today.entity, date=today.date, attention_z=attention_z, driver=driver,
        channels=channel_z, corroborating=corroborating, stage=stage,
        acceleration=round(accel, 2), trends_hit=trends_hit,
        finance_noticed=bool(finance_noticed),
        price_return_5d=round(ret5, 4), price_return_20d=round(ret20, 4),
        volume_z=round(volume_z, 2), unpriced=unpriced, has_prices=has_prices,
        has_social=has_social, direction=direction, attention_score=attention_score,
        past_spikes=past, flags=flags, why=why,
        z_spark=[round(z, 2) for z in drv_z[-30:]],
        driver_spark=[round(v, 3) for v in drv_series[-30:]],
        price_spark=[round(c, 4) for c in closes[-30:]],
        channel_dates=channel_dates, app_rank=app_rank,
    )

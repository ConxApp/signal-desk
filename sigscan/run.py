#!/usr/bin/env python3
"""
Daily run. Collect -> match -> store -> score -> explain -> publish.

Called by .github/workflows/scan.yml. Safe to run repeatedly on the same day;
it overwrites that day's rows rather than double-counting.

Two universes are scored:
  * the WATCHLIST (config/watchlist.yaml) — your names, with the Reddit-centric
    social-arbitrage score (arb_score) plus the cross-channel attention score
  * the DISCOVERY universe (config/brands.yaml) — ~400 consumer brands mapped to
    tickers, scored on attention only, surfaced when they light up so you see
    names you are NOT watching yet.
"""
from __future__ import annotations

import datetime as dt
import json, os, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from sigscan import collect, dashboard, alert, explain as explain_mod
from sigscan.collect import (Reddit, wikipedia_views, gdelt_volume, gdelt_headlines,
                             google_news_headlines, yahoo_prices, yahoo_trending,
                             apple_top_apps, google_trends_rss, traffic_to_int, status_report)
from sigscan.match import Entity, text_signals, make_sentiment
from sigscan.score import DailyObservation, score_entity
from sigscan.attention import score_attention
from sigscan.brands import load_brands, app_index, match_app_name
from sigscan.store import History

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = time.time()
TIME_BUDGET_S = int(os.environ.get("SCAN_TIME_BUDGET", "1380"))   # 23 min; workflow timeout is 30


def elapsed() -> float:
    return time.time() - T0


def budget_left(seconds_needed: float) -> bool:
    return elapsed() + seconds_needed < TIME_BUDGET_S


def load_config():
    with open(os.path.join(ROOT, "config/watchlist.yaml"), encoding="utf-8") as f:
        wl = yaml.safe_load(f)
    with open(os.path.join(ROOT, "config/sources.yaml"), encoding="utf-8") as f:
        src = yaml.safe_load(f)
    entities = [Entity(**{k: v for k, v in e.items() if k in Entity.__dataclass_fields__})
                for e in wl["entities"]]
    return entities, src, wl.get("defaults", {})


# ---------------------------------------------------------------------------
# Reddit: sample once, match every document against both universes
# ---------------------------------------------------------------------------

def _new_agg():
    return {"consumer_mentions": 0, "finance_mentions": 0,
            "authors": set(), "subs": defaultdict(int),
            "sent": [], "intent": 0, "scarcity": 0, "negative": 0, "examples": []}


def collect_reddit(matchers: list, src: dict):
    """matchers: objects with .key and .matches(text). Returns (agg, denom) or (None, None)."""
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        print("! REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set — skipping Reddit")
        return None, None

    rd = Reddit(cid, secret)
    sentiment = make_sentiment()
    limit = src.get("sample_limit", 100)
    agg = {m.key: _new_agg() for m in matchers}
    denom = {"consumer": 0, "finance": 0}
    universes = [("consumer", src.get("consumer_subreddits", [])),
                 ("finance", src.get("finance_subreddits", []))]

    for universe, subs in universes:
        for sub in sorted(set(subs)):
            if not budget_left(120):
                print("  ! time budget: stopping Reddit sampling early")
                break
            docs = rd.sample(sub, limit)
            denom[universe] += len(docs)
            print(f"  r/{sub}: {len(docs)} docs [{universe}]")
            for d in docs:
                text = d["text"]
                if not text:
                    continue
                hit = set()   # a watchlist Entity and a Brand can share a key: count each doc once
                for m in matchers:
                    if m.key in hit or not m.matches(text):
                        continue
                    hit.add(m.key)
                    a = agg[m.key]
                    a[f"{universe}_mentions"] += 1
                    if universe == "consumer":
                        a["subs"][sub] += 1
                        if d["author"] and d["author"] != "[deleted]":
                            a["authors"].add(d["author"])
                        ts = text_signals(text)
                        a["intent"] += ts["intent"]
                        a["scarcity"] += ts["scarcity"]
                        a["negative"] += ts["negative"]
                        a["sent"].append(sentiment(text[:1500]))
                        if len(a["examples"]) < 6 and d["kind"] == "post":
                            a["examples"].append({"sub": sub, "text": text[:220],
                                                  "url": d["permalink"], "score": d["score"]})
    if denom["consumer"] == 0:
        # auth/API failure or budget cut: never store an empty sample over a real one
        print("  ! Reddit returned no consumer documents — not storing this sample")
        return None, None
    return agg, denom


def store_reddit(hist: History, keys: list, agg: dict, denom: dict, ds: str):
    for key in keys:
        a = agg.get(key)
        if a is None:
            continue
        sent = sum(a["sent"]) / len(a["sent"]) if a["sent"] else 0.0
        hist.upsert({
            "entity": key, "date": ds,
            "consumer_mentions": a["consumer_mentions"],
            "consumer_denominator": denom["consumer"],
            "finance_mentions": a["finance_mentions"],
            "finance_denominator": denom["finance"],
            "distinct_communities": len(a["subs"]),
            "distinct_authors": len(a["authors"]),
            "community_counts": sorted(a["subs"].values(), reverse=True),
            "sentiment_mean": round(sent, 4),
            "intent_hits": a["intent"], "scarcity_hits": a["scarcity"],
            "negative_hits": a["negative"],
            "examples": a["examples"],
        })


# ---------------------------------------------------------------------------
# observations
# ---------------------------------------------------------------------------

def to_obs(key: str, rows: list) -> list:
    obs = []
    for r in rows:
        obs.append(DailyObservation(
            date=r["date"], entity=key,
            consumer_mentions=r.get("consumer_mentions", 0) or 0,
            consumer_denominator=r.get("consumer_denominator", 0) or 0,
            finance_mentions=r.get("finance_mentions", 0) or 0,
            finance_denominator=r.get("finance_denominator", 0) or 0,
            distinct_communities=r.get("distinct_communities", 0) or 0,
            distinct_authors=r.get("distinct_authors", 0) or 0,
            community_counts=r.get("community_counts", []) or [],
            sentiment_mean=r.get("sentiment_mean", 0.0) or 0.0,
            intent_hits=r.get("intent_hits", 0) or 0,
            scarcity_hits=r.get("scarcity_hits", 0) or 0,
            negative_hits=r.get("negative_hits", 0) or 0,
            wiki_views=r.get("wiki_views", 0) or 0,
            news_articles=r.get("news_articles", 0) or 0,
            close=r.get("close", 0.0) or 0.0, volume=r.get("volume", 0.0) or 0.0,
            app_rank=r.get("app_rank", 0.0) or 0.0,
            trends_hit=r.get("trends_hit", 0) or 0,
            # a channel is "observed" only if the row carries the key — a day with
            # no Wikipedia number (today's is always a day behind) or no App Store
            # read is absent, not zero
            missing=tuple(ch for ch, k in (("wiki", "wiki_views"), ("news", "news_articles"),
                                           ("app", "app_rank")) if k not in r),
        ))
    return obs


def prune(hist: History, keep_days: int = 150):
    cutoff = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    hist.prune(cutoff)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    today = dt.date.today()
    ds = today.isoformat()
    entities, src, defaults = load_config()
    brands, bdefaults = load_brands(os.path.join(ROOT, "config/brands.yaml"))
    baseline_days = defaults.get("baseline_days", 45)
    wl_tickers = {e.ticker for e in entities if e.ticker}
    ent_by_key = {e.key: e for e in entities}
    brand_by_key = {b.key: b for b in brands}

    problems = [p for e in entities for p in e.lint()]
    bproblems = [p for b in brands for p in b.lint()]
    if problems:
        print("Watchlist warnings:")
        for p in problems:
            print("  ⚠ " + p)
    if bproblems:
        print(f"Brand-universe warnings: {len(bproblems)} (see tests/check_brands)")

    hist = History(os.path.join(ROOT, "data/history.jsonl"))
    dhist = History(os.path.join(ROOT, "data/discovery.jsonl"))
    start = today - dt.timedelta(days=90)
    reddit_enabled = False

    # 1. Reddit ---------------------------------------------------------------
    print(f"\n=== Reddit sample {ds} ===")
    agg, denom = collect_reddit(list(entities) + list(brands), src)
    if agg is not None:
        reddit_enabled = True
        store_reddit(hist, [e.key for e in entities], agg, denom, ds)
        store_reddit(dhist, [b.key for b in brands], agg, denom, ds)
    print(f"  elapsed {elapsed():.0f}s")

    # 2. App Store charts -------------------------------------------------------
    print("\n=== App Store / Google Trends / Yahoo trending ===")
    idx = app_index(brands)
    best_rank = {}
    apps_seen = {}
    charts_ok = 0
    for country in ("us", "au"):
        chart = apple_top_apps(country, 100)
        charts_ok += bool(chart)
        for a in chart:
            k = match_app_name(a["name"], idx)
            if k:
                best_rank[k] = min(best_rank.get(k, 999), a["rank"])
                apps_seen.setdefault(k, []).append(f"{a['name']} #{a['rank']} {country.upper()}")
    if charts_ok:
        # Record an explicit observation for EVERY brand that has app names, so
        # "not on the chart today" (0) is distinguishable from "chart not read".
        for b in brands:
            if b.apps:
                dhist.patch(b.key, ds, app_rank=best_rank.get(b.key, 0))
    print(f"  app chart matches: {len(best_rank)} (charts read: {charts_ok})")

    # 3. Google Trends ----------------------------------------------------------
    trends_raw = {}
    trend_hits = {}
    for geo in ("US", "AU"):
        items = google_trends_rss(geo)
        trends_raw[geo] = [{"title": i["title"], "traffic": i["traffic"]} for i in items[:12]]
        for it in items:
            text = it["title"] + " " + " ".join(n["title"] for n in it.get("news", []))
            val = max(traffic_to_int(it["traffic"]), 1)
            for m in list(entities) + list(brands):
                if m.matches(text):
                    trend_hits[m.key] = max(trend_hits.get(m.key, 0), val)
    for k, v in trend_hits.items():
        if k in ent_by_key:
            hist.patch(k, ds, trends_hit=v)
        if k in brand_by_key:
            dhist.patch(k, ds, trends_hit=v)
    print(f"  trending-search matches: {len(trend_hits)}")

    # 4. Yahoo trending tickers -------------------------------------------------
    fin_trending = set(yahoo_trending("US")) | set(yahoo_trending("AU"))
    print(f"  yahoo trending tickers: {len(fin_trending)}")

    # 5. Wikipedia backfill (watchlist + every brand) ---------------------------
    print("\n=== Wikipedia pageviews ===")
    for e in entities:
        for d, v in wikipedia_views(e.wikipedia, start, today).items():
            hist.patch(e.key, d, wiki_views=v)
    nb = 0
    for b in brands:
        if not b.wiki:
            continue
        if not budget_left(420):
            print("  ! time budget: stopping Wikipedia early")
            break
        for d, v in wikipedia_views(b.wiki, start, today).items():
            dhist.patch(b.key, d, wiki_views=v)
        nb += 1
        time.sleep(0.05)
    print(f"  brands fetched: {nb}  elapsed {elapsed():.0f}s")

    # 6. Provisional attention to pick candidates for the slow sources -------
    def provisional(hstore, keys):
        out = {}
        by = hstore.by_entity()
        for k in keys:
            rows = by.get(k)
            if not rows:
                continue
            a = score_attention(to_obs(k, rows), baseline_days)
            if a:
                out[k] = a
        return out

    prov = provisional(dhist, [b.key for b in brands])
    ranked = sorted(prov.items(), key=lambda kv: -kv[1].attention_z)
    candidates = [k for k, a in ranked if a.attention_z >= 1.5][:40]
    # always include anything that lit up on apps / trends
    for k in list(best_rank) + list(trend_hits):
        if k in brand_by_key and k not in candidates:
            candidates.append(k)
    print(f"\n=== candidates for prices/news: {len(candidates)} ===")

    # 7. Prices (Yahoo) ---------------------------------------------------------
    print("=== prices ===")
    px_cache = {}

    def prices_for(ticker):
        if not ticker:
            return {}
        if ticker not in px_cache:
            px_cache[ticker] = yahoo_prices(ticker)
        return px_cache[ticker]

    for e in entities:
        for d, v in prices_for(e.ticker).items():
            if d >= start.isoformat():
                hist.patch(e.key, d, close=v["close"], volume=v["volume"])
    for k in candidates:
        b = brand_by_key[k]
        if not b.ticker or not budget_left(300):
            continue
        for d, v in prices_for(b.ticker).items():
            if d >= start.isoformat():
                dhist.patch(k, d, close=v["close"], volume=v["volume"])
    print(f"  tickers fetched: {len(px_cache)}  elapsed {elapsed():.0f}s")

    # 8. GDELT news volume (throttled: ~5s each, hard cap on wall time) --------
    print("=== news volume (GDELT) ===")
    news_n = 0
    news_t0 = time.time()
    NEWS_CAP_S = int(os.environ.get("NEWS_CAP_S", "420"))

    def news_ok():
        return budget_left(240) and (time.time() - news_t0) < NEWS_CAP_S

    for e in entities:
        if not news_ok():
            break
        for d, v in gdelt_volume(e.news_query, days=60).items():
            hist.patch(e.key, d, news_articles=v)
        news_n += 1
    for k in candidates[:25]:
        if not news_ok():
            print("  ! news cap reached: stopping news early")
            break
        b = brand_by_key[k]
        for d, v in gdelt_volume(b.news_query, days=60).items():
            dhist.patch(k, d, news_articles=v)
        news_n += 1
    print(f"  queries: {news_n}  elapsed {elapsed():.0f}s  gdelt={dict(collect.STATUS.get('gdelt', {}))}")

    prune(hist, 400)
    prune(dhist, 150)
    hist.save()
    dhist.save()

    # 9. Score ------------------------------------------------------------------
    print("\n=== Scoring ===")
    by_entity = hist.by_entity()
    signals = []
    for key, rows in by_entity.items():
        e = ent_by_key.get(key)
        if not e:
            continue
        obs = to_obs(key, rows)
        sig = score_entity(obs, baseline_days=baseline_days)
        att = score_attention(obs, baseline_days, finance_noticed=e.ticker in fin_trending)
        if not sig:
            continue
        d = sig.to_dict()
        social_days = sum(1 for o in obs if o.consumer_denominator > 0)
        d.update({"name": e.name, "ticker": e.ticker, "kind": e.kind, "note": e.note,
                  "mentions": obs[-1].consumer_mentions,
                  "examples": rows[-1].get("examples", []),
                  "sparkline": [r.get("consumer_mentions", 0) for r in rows[-30:]],
                  "wiki_spark": [r.get("wiki_views", 0) for r in rows[-30:]],
                  "history_days": len(rows), "social_days": social_days,
                  "attention": att.to_dict() if att else None,
                  "rank_score": d["arb_score"] if social_days >= 14 else (att.attention_score if att else 0.0),
                  "headlines": [], "explain": None})
        signals.append(d)
        print(f"  {e.name:28s} arb={d['arb_score']:5.1f} att={att.attention_score if att else 0:5.1f} "
              f"z={d['social_z']:+.2f} {att.stage if att else '-'}")

    dby = dhist.by_entity()
    discovered = []
    for key, rows in dby.items():
        b = brand_by_key.get(key)
        if not b:
            continue
        att = score_attention(to_obs(key, rows), baseline_days,
                              finance_noticed=bool(b.ticker) and b.ticker in fin_trending)
        if not att:
            continue
        d = att.to_dict()
        d.update({"key": key, "name": b.name, "ticker": b.ticker, "company": b.company,
                  "sector": b.sector, "private": b.private, "on_watchlist": b.ticker in wl_tickers,
                  "mentions": rows[-1].get("consumer_mentions", 0),
                  "examples": rows[-1].get("examples", []),
                  "apps": apps_seen.get(key, []),
                  "history_days": len(rows),
                  "attention": att.to_dict(),
                  "headlines": [], "explain": None})
        discovered.append(d)
    discovered.sort(key=lambda x: (-x["attention_score"], -x["attention_z"]))
    hot = [x for x in discovered if x["attention_z"] >= 1.5 or x["flags"]]
    keep = hot[:60] if len(hot) >= 12 else discovered[:25]
    print(f"  discovery universe scored: {len(discovered)}, kept {len(keep)}")

    # 10. Headlines + explanations for the names that matter -------------------
    print("\n=== Headlines / explanations ===")
    use_ai = explain_mod.ai_available()
    top_sig = sorted(signals, key=lambda s: -s["rank_score"])[:8]
    top_disc = keep[:15]
    hl_t0 = time.time()
    HL_CAP_S = int(os.environ.get("HEADLINES_CAP_S", "200"))
    targets = [(s, ent_by_key[s["entity"]].news_query, "US") for s in top_sig] + \
              [(x, brand_by_key[x["key"]].news_query, "AU" if x.get("sector") == "aus" else "US") for x in top_disc]
    for item, q, gl in targets:
        if budget_left(90) and (time.time() - hl_t0) < HL_CAP_S:
            # Google News RSS is fast and keyless; GDELT (rate-limited from shared IPs) is the fallback
            item["headlines"] = google_news_headlines(q, days=7, n=6, gl=gl)
            if not item["headlines"] and (time.time() - hl_t0) < HL_CAP_S * 0.6:
                item["headlines"] = gdelt_headlines(q, days=4, n=6)
        item["explain"] = explain_mod.explain(item, item["headlines"], item.get("examples") or [],
                                              use_ai=use_ai and budget_left(120))
    for item in signals + keep:
        if item["explain"] is None:
            item["explain"] = explain_mod.explain(item, [], item.get("examples") or [], use_ai=False)
    print(f"  ai summaries: {'on' if use_ai else 'off'}  elapsed {elapsed():.0f}s")

    signals.sort(key=lambda s: -s["rank_score"])
    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
        "date": ds,
        "reddit_enabled": reddit_enabled,
        "sources": status_report(),
        "signals": signals,
        "discovered": keep,
        "trends_raw": trends_raw,
        "finance_trending": sorted(fin_trending)[:40],
        "warnings": problems,
        "stats": {"watchlist": len(entities), "brands": len(brands),
                  "brands_scored": len(discovered), "elapsed_s": int(elapsed()),
                  "ai": use_ai},
    }

    os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
    with open(os.path.join(ROOT, "site/data.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
    dashboard.render(payload, os.path.join(ROOT, "site/index.html"))
    alert.dispatch(payload)
    print(f"\nWrote site/index.html — {len(signals)} watchlist, {len(keep)} discovered, {int(elapsed())}s")


if __name__ == "__main__":
    main()

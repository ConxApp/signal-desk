#!/usr/bin/env python3
"""
Daily run. Collect -> match -> store -> score -> publish.

Called by .github/workflows/scan.yml. Safe to run repeatedly on the same day;
it overwrites that day's row rather than double-counting.
"""
from __future__ import annotations

import datetime as dt
import json, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from sigscan.collect import Reddit, wikipedia_views, gdelt_volume, stooq_prices
from sigscan.match import Entity, text_signals, make_sentiment
from sigscan.score import DailyObservation, score_entity
from sigscan.store import History
from sigscan import dashboard, alert

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config():
    with open(os.path.join(ROOT, "config/watchlist.yaml")) as f:
        wl = yaml.safe_load(f)
    with open(os.path.join(ROOT, "config/sources.yaml")) as f:
        src = yaml.safe_load(f)
    entities = [Entity(**{k: v for k, v in e.items() if k in Entity.__dataclass_fields__})
                for e in wl["entities"]]
    return entities, src, wl.get("defaults", {})


def collect_reddit(entities, src, today):
    """Sample subreddits once, then match every document against every entity."""
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        print("! REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set — skipping Reddit")
        return {}, {}

    rd = Reddit(cid, secret)
    sentiment = make_sentiment()
    limit = src.get("sample_limit", 100)

    agg = {e.key: {"consumer_mentions": 0, "finance_mentions": 0,
                   "authors": set(), "subs": defaultdict(int),
                   "sent": [], "intent": 0, "scarcity": 0, "negative": 0,
                   "examples": []} for e in entities}
    denom = {"consumer": 0, "finance": 0}

    universes = [("consumer", src.get("consumer_subreddits", [])),
                 ("finance", src.get("finance_subreddits", []))]

    for universe, subs in universes:
        for sub in sorted(set(subs)):
            docs = rd.sample(sub, limit)
            denom[universe] += len(docs)
            print(f"  r/{sub}: {len(docs)} docs [{universe}]")
            for d in docs:
                text = d["text"]
                if not text:
                    continue
                for e in entities:
                    if not e.matches(text):
                        continue
                    a = agg[e.key]
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
    return agg, denom


def main():
    today = dt.date.today()
    ds = today.isoformat()
    entities, src, defaults = load_config()

    problems = [p for e in entities for p in e.lint()]
    if problems:
        print("Watchlist warnings:")
        for p in problems:
            print("  ⚠ " + p)

    hist = History(os.path.join(ROOT, "data/history.jsonl"))

    print(f"\n=== Reddit sample {ds} ===")
    agg, denom = collect_reddit(entities, src, today)

    for e in entities:
        a = agg.get(e.key)
        if a is None:
            continue
        sent = sum(a["sent"]) / len(a["sent"]) if a["sent"] else 0.0
        hist.upsert({
            "entity": e.key, "date": ds,
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

    # --- backfillable sources -------------------------------------------
    # These give real history from day one, so the dashboard is useful
    # immediately while the Reddit baseline builds up.
    print(f"\n=== Wikipedia / GDELT / prices ===")
    start = today - dt.timedelta(days=90)
    for e in entities:
        views = wikipedia_views(e.wikipedia, start, today)
        for d, v in views.items():
            hist.patch(e.key, d, wiki_views=v)

        news = gdelt_volume(e.news_query, days=60)
        for d, v in news.items():
            hist.patch(e.key, d, news_articles=v)

        px = stooq_prices(e.ticker)
        for d, v in px.items():
            if d >= start.isoformat():
                hist.patch(e.key, d, close=v["close"], volume=v["volume"])
        print(f"  {e.key}: wiki={len(views)}d news={len(news)}d px={len(px)}d")

    hist.save()

    # --- score ----------------------------------------------------------
    print("\n=== Scoring ===")
    by_entity = hist.by_entity()
    signals = []
    ent_by_key = {e.key: e for e in entities}
    for key, rows in by_entity.items():
        e = ent_by_key.get(key)
        if not e:
            continue
        obs = []
        for r in rows:
            obs.append(DailyObservation(
                date=r["date"], entity=key,
                consumer_mentions=r.get("consumer_mentions", 0),
                consumer_denominator=r.get("consumer_denominator", 0),
                finance_mentions=r.get("finance_mentions", 0),
                finance_denominator=r.get("finance_denominator", 0),
                distinct_communities=r.get("distinct_communities", 0),
                distinct_authors=r.get("distinct_authors", 0),
                community_counts=r.get("community_counts", []),
                sentiment_mean=r.get("sentiment_mean", 0.0),
                intent_hits=r.get("intent_hits", 0),
                scarcity_hits=r.get("scarcity_hits", 0),
                negative_hits=r.get("negative_hits", 0),
                wiki_views=r.get("wiki_views", 0),
                news_articles=r.get("news_articles", 0),
                close=r.get("close", 0.0), volume=r.get("volume", 0.0),
            ))
        sig = score_entity(obs, baseline_days=defaults.get("baseline_days", 45))
        if not sig:
            continue
        d = sig.to_dict()
        d.update({"name": e.name, "ticker": e.ticker, "kind": e.kind,
                  "note": e.note,
                  "mentions": obs[-1].consumer_mentions,
                  "examples": rows[-1].get("examples", []),
                  "sparkline": [r.get("consumer_mentions", 0) for r in rows[-30:]],
                  "wiki_spark": [r.get("wiki_views", 0) for r in rows[-30:]],
                  "history_days": len(rows)})
        signals.append(d)
        print(f"  {e.name:28s} arb={d['arb_score']:5.1f} z={d['social_z']:+.2f} "
              f"lead={d['lead_ratio']:.2f} {','.join(d['flags']) or '-'}")

    signals.sort(key=lambda s: -s["arb_score"])
    payload = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(),
               "date": ds, "signals": signals,
               "warnings": problems}

    os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
    with open(os.path.join(ROOT, "site/data.json"), "w") as f:
        json.dump(payload, f, indent=1)
    dashboard.render(payload, os.path.join(ROOT, "site/index.html"))
    alert.dispatch(payload)
    print(f"\nWrote site/index.html — {len(signals)} entities scored")


if __name__ == "__main__":
    main()

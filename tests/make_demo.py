"""
Build a demo payload with SYNTHETIC data.

Important: the numbers are not hand-written. Synthetic daily observations are
pushed through the real scoring engine, so every relationship on the demo page
(z-score vs breadth vs lead ratio vs flags) is internally consistent with what
the live system would produce.
"""
import sys, os, math, random, datetime as dt, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sigscan.score import DailyObservation, score_entity
from sigscan import dashboard

random.seed(1337)
TODAY = dt.date(2026, 8, 22)

# name, ticker, kind, scenario
CAST = [
    ("Pop Mart / Labubu", "9992.HK", "product", "broad_early"),
    ("On Holding",        "ONON",    "product", "broad_early_mild"),
    ("e.l.f. Beauty",     "ELF",     "product", "scarcity"),
    ("Deckers (Hoka)",    "DECK",    "product", "late"),
    ("Celsius Holdings",  "CELH",    "product", "negative"),
    ("Robinhood",         "HOOD",    "platform","finance_only"),
    ("Crocs",             "CROX",    "product", "narrow"),
    ("AMD",               "AMD",     "tech",    "quiet"),
    ("Tesla",             "TSLA",    "product", "quiet_busy"),
    ("Atlassian",         "TEAM",    "platform","quiet"),
    ("CRISPR Therapeutics","CRSP",   "tech",    "quiet"),
    ("Bloom Energy",      "BE",      "tech",    "quiet"),
    ("Dutch Bros",        "BROS",    "product", "cooling"),
    ("Birkenstock",       "BIRK",    "product", "quiet"),
    ("Oddity Tech",       "ODD",     "product", "quiet"),
]

EXAMPLES = {
    "Pop Mart / Labubu": ("my niece asked for a Labubu for her birthday and I had no idea "
                          "what it was, now I'm seeing them clipped to every bag at school pickup", "Parenting"),
    "On Holding": ("finally tried the Cloudmonster after everyone here wouldn't shut up about "
                   "them — the ride is genuinely different to anything else I own", "RunningShoeGeeks"),
    "e.l.f. Beauty": ("Halo Glow has been sold out at three Targets near me, is there a restock "
                      "date anyone knows about", "MakeupAddiction"),
    "Deckers (Hoka)": ("Speedgoat 6 review after 400km — still the best trail shoe I've run in", "running"),
    "Celsius Holdings": ("switched back to regular coffee, the Celsius cans started tasting off "
                         "and I was getting jittery all afternoon", "EnergyDrinks"),
    "Robinhood": ("anyone else getting weird fills on HOOD options this week", "options"),
    "Crocs": ("this one Jibbitz thread has 4k comments somehow", "streetwear"),
    "Dutch Bros": ("the new location opened and the queue was 40 minutes, seems to have "
                   "calmed down now though", "Coffee"),
}


def make(scenario, n=70):
    """Generate a plausible 70-day history for one scenario."""
    h = []
    base_m = {"quiet": 6, "quiet_busy": 22, "narrow": 5, "finance_only": 9}.get(scenario, 7)
    for i in range(n):
        day = TODAY - dt.timedelta(days=n - 1 - i)
        # weekly rhythm — Reddit is busier at weekends
        wk = 1.0 + 0.18 * math.sin(i * 2 * math.pi / 7)
        m = max(0, int(random.gauss(base_m * wk, base_m * 0.25)))
        o = DailyObservation(
            date=day.isoformat(), entity=scenario,
            consumer_mentions=m, consumer_denominator=random.randint(5200, 6400),
            finance_mentions=max(0, int(random.gauss(5, 1.6))),
            finance_denominator=random.randint(3400, 4100),
            distinct_communities=max(1, min(m, random.randint(2, 4))),
            distinct_authors=max(1, int(m * 0.8)),
            community_counts=[max(1, m // 3)] * 3,
            sentiment_mean=round(random.gauss(0.18, 0.12), 3),
            wiki_views=max(50, int(random.gauss(2400, 320))),
            news_articles=max(0, int(random.gauss(4, 1.6))),
            close=round(100 * (1 + 0.004 * math.sin(i / 6)), 2),
            volume=random.randint(900_000, 1_400_000),
        )
        h.append(o)

    t = h[-1]
    # --- ramp the final stretch according to the scenario -----------------
    if scenario == "broad_early":
        for k, o in enumerate(h[-9:]):
            f = 1 + 2.0 * (k / 8) ** 1.9
            o.consumer_mentions = int(o.consumer_mentions * f * 5)
            o.distinct_communities = min(14, 3 + int(9 * k / 8))
            o.distinct_authors = int(o.consumer_mentions * 0.9)
            o.community_counts = [max(1, o.consumer_mentions // max(1, o.distinct_communities))] * o.distinct_communities
            o.wiki_views = int(o.wiki_views * (1 + 2.4 * (k / 8) ** 2))
            o.sentiment_mean = 0.42
        t.intent_hits, t.scarcity_hits, t.negative_hits = 34, 21, 3
        t.news_articles = 9
    elif scenario == "broad_early_mild":
        for k, o in enumerate(h[-7:]):
            o.consumer_mentions = int(o.consumer_mentions * (1 + 2.2 * k / 6))
            o.distinct_communities = min(9, 3 + k)
            o.distinct_authors = int(o.consumer_mentions * 0.85)
            o.community_counts = [max(1, o.consumer_mentions // max(1, o.distinct_communities))] * o.distinct_communities
            o.wiki_views = int(o.wiki_views * (1 + 0.8 * k / 6))
            o.sentiment_mean = 0.38
        t.intent_hits, t.scarcity_hits, t.negative_hits = 19, 5, 2
    elif scenario == "scarcity":
        for k, o in enumerate(h[-6:]):
            o.consumer_mentions = int(o.consumer_mentions * (1 + 2.6 * k / 5))
            o.distinct_communities = min(8, 3 + k)
            o.distinct_authors = int(o.consumer_mentions * 0.8)
            o.community_counts = [max(1, o.consumer_mentions // max(1, o.distinct_communities))] * o.distinct_communities
            o.sentiment_mean = 0.31
        t.intent_hits, t.scarcity_hits, t.negative_hits = 22, 26, 4
        t.wiki_views = int(t.wiki_views * 1.9)
    elif scenario == "late":
        for k, o in enumerate(h[-8:]):
            o.consumer_mentions = int(o.consumer_mentions * (1 + 1.8 * k / 7))
            o.distinct_communities = min(10, 3 + k)
            o.distinct_authors = int(o.consumer_mentions * 0.85)
            o.community_counts = [max(1, o.consumer_mentions // max(1, o.distinct_communities))] * o.distinct_communities
            o.close = round(100 * (1 + 0.041 * k), 2)
            o.volume = int(o.volume * (1 + 0.5 * k))
            o.sentiment_mean = 0.36
        t.intent_hits, t.scarcity_hits = 14, 6
    elif scenario == "negative":
        for k, o in enumerate(h[-6:]):
            o.consumer_mentions = int(o.consumer_mentions * (1 + 2.0 * k / 5))
            o.distinct_communities = min(9, 3 + k)
            o.distinct_authors = int(o.consumer_mentions * 0.85)
            o.community_counts = [max(1, o.consumer_mentions // max(1, o.distinct_communities))] * o.distinct_communities
            o.sentiment_mean = -0.44
        t.intent_hits, t.scarcity_hits, t.negative_hits = 3, 2, 31
    elif scenario == "finance_only":
        for k, o in enumerate(h[-5:]):
            o.finance_mentions = int(o.finance_mentions * (1 + 3.2 * k / 4))
    elif scenario == "narrow":
        t.consumer_mentions = int(t.consumer_mentions * 13)
        t.distinct_communities = 1
        t.distinct_authors = 7
        t.community_counts = [t.consumer_mentions]
        t.sentiment_mean = 0.2
    elif scenario == "cooling":
        for k, o in enumerate(h[-14:]):
            bump = math.exp(-((k - 4) ** 2) / 9.0)
            o.consumer_mentions = int(o.consumer_mentions * (1 + 3.0 * bump))
            o.distinct_communities = min(9, 3 + int(5 * bump))
            o.distinct_authors = int(o.consumer_mentions * 0.85)
            o.community_counts = [max(1, o.consumer_mentions // max(1, o.distinct_communities))] * o.distinct_communities
        t.intent_hits, t.scarcity_hits = 6, 2
    return h


signals = []
for name, ticker, kind, scenario in CAST:
    h = make(scenario)
    sig = score_entity(h)
    d = sig.to_dict()
    ex = EXAMPLES.get(name)
    d.update({
        "name": name, "ticker": ticker, "kind": kind, "note": "",
        "mentions": h[-1].consumer_mentions,
        "sparkline": [o.consumer_mentions for o in h[-30:]],
        "wiki_spark": [o.wiki_views for o in h[-30:]],
        "history_days": len(h),
        "examples": [{"sub": ex[1], "text": ex[0], "url": "#", "score": 0}] if ex else [],
    })
    signals.append(d)

signals.sort(key=lambda s: -s["arb_score"])
payload = {"generated": "2026-08-22T21:05:00+00:00 (SYNTHETIC)",
           "date": "2026-08-22", "signals": signals, "warnings": []}

for s in signals:
    print(f"{s['name']:24s} arb={s['arb_score']:5.1f} z={s['social_z']:+6.2f} "
          f"br={s['breadth']:.2f} lead={s['lead_ratio']:5.2f} unp={s['unpriced']:.2f} "
          f"{','.join(s['flags'])}")

out = os.path.join(os.path.dirname(__file__), "..", "site")
os.makedirs(out, exist_ok=True)
json.dump(payload, open(os.path.join(out, "demo_data.json"), "w"), indent=1)
dashboard.render(payload, os.path.join(out, "demo.html"), demo=True)
dashboard.render_fragment(payload, os.path.join(out, "demo_fragment.html"), demo=True)
print("\nwrote site/demo.html + site/demo_fragment.html")

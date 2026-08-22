"""Offline tests for the attention engine and the brand matcher. Run: python tests/test_attention.py"""
import sys, os, math, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sigscan.score import DailyObservation
from sigscan.attention import (rolling_z, trend_stage, acceleration, app_signal,
                               forward_returns, spike_dates, score_attention)
from sigscan.brands import Brand, compile_term, load_brands, match_app_name, app_index

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
        print("  FAIL", msg)
    else:
        print("  ok  ", msg)


def series(days, base=1000, spike_at=None, spike_mult=4.0, seed=1):
    random.seed(seed)
    out = []
    for i in range(days):
        v = base * (1 + random.uniform(-0.15, 0.15))
        if spike_at is not None and i >= spike_at:
            v *= spike_mult
        out.append(v)
    return out


def obs_from(wiki, closes=None, ranks=None, key="x"):
    out = []
    for i, w in enumerate(wiki):
        d = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
        out.append(DailyObservation(
            date=d, entity=key, wiki_views=int(w),
            close=(closes[i] if closes else 0.0),
            volume=(1e6 if closes else 0.0),
            app_rank=(ranks[i] if ranks else 0.0),
        ))
    return out


print("rolling_z / stage")
flat = series(60)
z = rolling_z([math.log1p(v) for v in flat])
check(len(z) == 60 and all(abs(x) < 3 for x in z[20:]), "flat series stays inside 3 sigma")
sp = series(60, spike_at=57)
z2 = rolling_z([math.log1p(v) for v in sp])
check(z2[-1] > 3, f"4x spike produces z>3 (got {z2[-1]:.1f})")
check(trend_stage(z2, acceleration([math.log1p(v) for v in sp], [math.log1p(v) for v in sp[:-3]])) == "rising",
      "fresh spike is 'rising'")
fade = series(75, spike_at=55)
for i in range(65, 75):
    fade[i] = fade[54]            # back to baseline for the last 10 days
zf = rolling_z([math.log1p(v) for v in fade])
st = trend_stage(zf, acceleration([math.log1p(v) for v in fade], [math.log1p(v) for v in fade[:-3]]))
check(st in ("fading", "quiet"), f"spike that returned to baseline is fading/quiet (got {st})")
check(trend_stage([0.1] * 30, 0.0) == "quiet", "flat is quiet")

print("app_signal")
check(app_signal(0) == 0.0 and app_signal(1) > app_signal(50) > app_signal(200) >= 0, "rank ordering")

print("forward returns")
dates = [f"2026-01-{d:02d}" for d in range(1, 29)]
closes = [100 + i for i in range(28)]
fr = forward_returns(dates, closes, ["2026-01-05"], horizon=10)
check(len(fr) == 1 and abs(fr[0] - (114 / 104 - 1)) < 1e-9, "10-day forward return computed")
sd = spike_dates(dates, [0] * 10 + [3.0, 3.1, 0, 0, 0, 0, 2.6] + [0] * 11, threshold=2.5, gap=5)
check(sd == ["2026-01-11", "2026-01-17"], f"spikes de-clustered ({sd})")

print("score_attention")
a = score_attention(obs_from(series(60, spike_at=57), closes=[50.0] * 60))
check(a is not None and a.driver == "wiki" and a.attention_z > 3, "wiki spike detected as driver")
check(a.has_prices and a.unpriced > 0.7 and "UNPRICED" in a.flags, "flat price -> UNPRICED")
check(a.attention_score > 40, f"score is meaningful ({a.attention_score})")
b = score_attention(obs_from(series(60)))
check(b is not None and b.stage == "quiet" and b.attention_score < 35, "quiet brand scores low")
c = score_attention(obs_from(series(60), ranks=[0] * 55 + [0, 40, 20, 8, 3]))
check(c is not None and c.channels.get("app", 0) > 2 and "APP_CLIMBING" in c.flags, "app climbing flag")
d = score_attention([])
check(d is None, "empty history -> None")
# App Store chart only read on the LAST day: the channel must not exist yet (no fake spike),
# but a brand with 14+ observed chart days (mostly off-chart) CAN spike.
unobs = obs_from(series(60))
for o in unobs[:-1]:
    o.missing = ("app",)
unobs[-1].app_rank = 3
f = score_attention(unobs)
check(f is not None and "app" not in f.channels and "APP_CLIMBING" not in f.flags,
      f"single observed app day -> no app channel ({f.channels if f else None})")
check(f is not None and f.app_rank == 3 and any("baseline still forming" in w for w in f.why),
      "but the chart rank is still reported")
# Wikipedia is a day behind: a missing 'today' must not read as a -12 sigma crash
wk = obs_from(series(60))
wk[-1].wiki_views = 0
wk[-1].missing = ("wiki",)
g = score_attention(wk)
check(g is not None and abs(g.channels.get("wiki", 0)) < 3, f"missing today's wiki -> uses yesterday ({g.channels if g else None})")
e = score_attention(obs_from(series(10, spike_at=9)))
check(e is not None and e.attention_z == 0.0, "short history -> no z (baseline not formed)")

print("staleness / watchlist engine on observed days")
# a channel whose latest observation is weeks old must not drive today's score
stale = obs_from(series(60, spike_at=57))
for o in stale[-20:]:                      # wiki not observed for the last 20 days
    o.missing = ("wiki",)
    o.wiki_views = 0
h2 = score_attention(stale)
check(h2 is None or h2.channels.get("wiki", 0) == 0.0, f"stale wiki channel is neutralised ({h2.channels if h2 else None})")
from sigscan.score import score_entity
sparse = obs_from(series(60))
for o in sparse:
    o.close = 50.0; o.volume = 1e6
# Reddit observed on the last day only: the social baseline is NOT 59 zero days
sparse[-1].consumer_mentions = 3; sparse[-1].consumer_denominator = 5000
sparse[-1].distinct_communities = 2; sparse[-1].distinct_authors = 3; sparse[-1].community_counts = [2, 1]
sg = score_entity(sparse)
check(sg is not None and sg.social_z == 0.0 and "BROAD_SPIKE" not in sg.flags and "NARROW_SPIKE" not in sg.flags,
      f"one Reddit day on a backfilled history -> no fake social spike (z={sg.social_z if sg else None})")
wk2 = obs_from(series(60))
wk2[-1].wiki_views = 0; wk2[-1].missing = ("wiki",)
sg2 = score_entity(wk2)
check(sg2 is not None and abs(sg2.wiki_z) < 3, f"watchlist engine: missing today's wiki -> uses yesterday ({sg2.wiki_z if sg2 else None})")

print("brand matcher")
hoka = Brand(key="hoka", name="Hoka", ticker="DECK", terms=["Hoka", "Clifton 9"])
check(hoka.matches("just copped the new Hokas") and not hoka.matches("hokage naruto"), "plural + boundaries")
vans = Brand(key="vans", name="Vans", ticker="VFC", terms=["Vans"], exclude=["minivan"], context=["shoe", "skate"])
check(vans.matches("my Vans skate shoes") and not vans.matches("we rented two vans") and not vans.matches("minivan vans shoe"),
      "context gating and exclude veto")
plus = Brand(key="d", name="Disney", ticker="DIS", terms=["Disney+"])
check(plus.matches("Disney+ raised prices") and not plus.matches("disney+plus"), "punctuated term boundaries")
elf = compile_term("e.l.f.")
check(elf.search("love e.l.f. primer") is not None, "dotted term")
rx = compile_term("re:\\bGTA\\s?6\\b")
check(rx.search("GTA6 trailer") is not None, "raw regex term")

print("brands.yaml")
brands, defaults = load_brands(os.path.join(ROOT, "config/brands.yaml"))
check(len(brands) > 300, f"universe loads ({len(brands)} brands)")
keys = [b.key for b in brands]
check(len(keys) == len(set(keys)), "keys unique")
bad = [b.key for b in brands if not b.terms]
check(not bad, f"every brand has terms {bad[:5]}")
idx = app_index(brands)
check(match_app_name("Temu: Shop Like a Billionaire", idx) == "pdd", "app name exact match")
check(match_app_name("Duolingo - Language Lessons", idx) == "duolingo", "app name with suffix")
check(match_app_name("Some Random App", idx) is None, "unknown app -> None")
lint = [p for b in brands for p in b.lint()]
print(f"  lint warnings: {len(lint)}")
for p in lint[:10]:
    print("   ", p)
# spot-check a few false-positive traps
traps = {
    "on": "I turned it on and went running",
    "target": "my target for the year is 10k",
    "apple": "apple pie recipe",
    "stanley nhl": "Oilers win the Stanley Cup final",
    "corona": "coronavirus cases rising",
    "hinge": "the door hinge broke",
    "uber": "uber cool jacket",
}
hits = {}
for name, text in traps.items():
    hits[name] = [b.key for b in brands if b.matches(text)]
check(not hits["on"], f"'on' does not match On Running ({hits['on']})")
check(not hits["stanley nhl"], f"NHL Stanley Cup does not match tumbler ({hits['stanley nhl']})")
check(not hits["corona"], f"coronavirus does not match Corona beer ({hits['corona']})")
check(not hits["hinge"], f"door hinge does not match Hinge app ({hits['hinge']})")
check("apple" not in hits["apple"], f"apple pie does not match Apple ({hits['apple']})")
check("target" not in hits["target"], f"'target for the year' does not match Target ({hits['target']})")

print()
if FAIL:
    print(f"{len(FAIL)} FAILED")
    sys.exit(1)
print("all green")

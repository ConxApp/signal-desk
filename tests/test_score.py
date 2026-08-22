import sys, os, random, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sigscan.score import (robust_z, log_share, herfindahl, breadth_score,
                           score_entity, DailyObservation)

def obs(day, **kw):
    base = dict(date=f"2026-06-{day:02d}", entity="TEST",
                consumer_denominator=4000, finance_denominator=3000,
                wiki_views=1000, news_articles=3, close=100.0, volume=1_000_000)
    base.update(kw)
    return DailyObservation(**base)

def build(n=60, mentions=8, **last):
    random.seed(7)
    h = []
    for i in range(n):
        h.append(obs(i % 28 + 1, consumer_mentions=mentions + random.randint(-2, 2),
                     finance_mentions=4 + random.randint(-1, 1),
                     distinct_communities=3, distinct_authors=8,
                     community_counts=[4, 3, 1]))
    if last:
        for k, v in last.items():
            setattr(h[-1], k, v)
    return h

def test_quiet_history_does_not_flag():
    sig = score_entity(build())
    assert sig.social_z < 2.0, sig.social_z
    assert sig.arb_score < 45, sig.arb_score
    assert not [f for f in sig.flags if "SPIKE" in f]

def test_broad_spike_flags():
    sig = score_entity(build(mentions=8, consumer_mentions=90,
                             distinct_communities=11, distinct_authors=140,
                             community_counts=[12,11,10,9,9,8,8,7,6,5,5]))
    assert sig.social_z >= 3.0
    assert sig.breadth >= 0.45
    assert "BROAD_SPIKE" in sig.flags

def test_narrow_spike_is_separated_from_broad():
    sig = score_entity(build(mentions=8, consumer_mentions=90,
                             distinct_communities=1, distinct_authors=6,
                             community_counts=[90]))
    assert "NARROW_SPIKE" in sig.flags
    assert "BROAD_SPIKE" not in sig.flags

def test_denominator_normalisation_kills_platform_wide_growth():
    """Doubling every count AND the denominator must not create a signal."""
    h = build()
    for o in h[-10:]:
        o.consumer_mentions *= 3
        o.consumer_denominator *= 3
    sig = score_entity(h)
    assert abs(sig.social_z) < 2.0, sig.social_z

def test_past_spike_does_not_poison_baseline():
    """A mean/stdev baseline would be wrecked by this; MAD should shrug."""
    h = build()
    h[20].consumer_mentions = 400
    h[21].consumer_mentions = 350
    h[-1].consumer_mentions = 80
    h[-1].distinct_communities = 10
    h[-1].distinct_authors = 120
    h[-1].community_counts = [10]*8
    sig = score_entity(h)
    assert sig.social_z >= 3.0, sig.social_z

def test_finance_only_story_is_labelled():
    sig = score_entity(build(finance_mentions=90))
    assert "FINANCE_ONLY" in sig.flags
    assert sig.lead_ratio < 1.0

def test_lead_ratio_collapses_when_street_catches_up():
    early = score_entity(build(consumer_mentions=90, distinct_communities=10,
                               distinct_authors=120, community_counts=[9]*10))
    late = score_entity(build(consumer_mentions=90, finance_mentions=80,
                              distinct_communities=10, distinct_authors=120,
                              community_counts=[9]*10))
    assert early.lead_ratio > late.lead_ratio * 2
    assert "AHEAD_OF_THE_STREET" in early.flags

def test_already_ran_is_marked_late_not_unpriced():
    h = build(consumer_mentions=90, distinct_communities=10, distinct_authors=120,
              community_counts=[9]*10)
    for i, o in enumerate(h[-6:]):
        o.close = 100 * (1 + 0.05 * i)
    sig = score_entity(h)
    assert "LATE" in sig.flags
    assert "UNPRICED" not in sig.flags

def test_flat_price_with_spike_is_unpriced():
    sig = score_entity(build(consumer_mentions=90, distinct_communities=10,
                             distinct_authors=120, community_counts=[9]*10))
    assert "UNPRICED" in sig.flags
    assert sig.unpriced >= 0.7

def test_scores_are_bounded():
    sig = score_entity(build(consumer_mentions=100000, distinct_communities=99,
                             distinct_authors=9999, community_counts=[1]*99,
                             wiki_views=10**7, news_articles=5000))
    assert 0 <= sig.arb_score <= 100, sig.arb_score
    assert abs(sig.social_z) <= 12

def test_short_history_is_silent():
    sig = score_entity(build(n=6))
    assert sig.social_z == 0.0
    assert not sig.flags

# --- regressions found by eyeballing the first rendered demo ---------------

def test_narrow_spike_cannot_outrank_a_broad_one():
    """A single 4000-comment thread produced a 12-sigma reading and nearly
    topped the board. Volume without spread is not evidence."""
    narrow = score_entity(build(consumer_mentions=1200, distinct_communities=1,
                                distinct_authors=7, community_counts=[1200]))
    broad = score_entity(build(consumer_mentions=70, distinct_communities=11,
                               distinct_authors=130, community_counts=[7]*11))
    assert narrow.arb_score < broad.arb_score, (narrow.arb_score, broad.arb_score)
    assert narrow.arb_score < 60, narrow.arb_score

def test_lead_ratio_is_capped_and_breadth_gated():
    narrow = score_entity(build(consumer_mentions=1200, distinct_communities=1,
                                distinct_authors=7, community_counts=[1200]))
    assert narrow.lead_ratio <= 6.0
    assert "AHEAD_OF_THE_STREET" not in narrow.flags

def test_negative_story_is_labelled_and_downweighted():
    neg = score_entity(build(consumer_mentions=80, distinct_communities=9,
                             distinct_authors=110, community_counts=[9]*9,
                             sentiment_mean=-0.45, negative_hits=40, intent_hits=2))
    pos = score_entity(build(consumer_mentions=80, distinct_communities=9,
                             distinct_authors=110, community_counts=[9]*9,
                             sentiment_mean=0.42, intent_hits=30, scarcity_hits=18))
    assert neg.direction == "negative"
    assert pos.direction == "positive"
    assert neg.arb_score < pos.arb_score * 0.75, (neg.arb_score, pos.arb_score)
    assert "NEGATIVE_TURN" in neg.flags
    assert "SCARCITY" not in neg.flags   # contradictory with a negative turn

def test_still_climbing_is_not_marked_cooling():
    """Acceleration used to compare single days and fired COOLING on names
    that were plainly still accelerating."""
    h = build()
    for k, o in enumerate(h[-9:]):
        o.consumer_mentions = int(8 * (1 + 2.0 * (k / 8) ** 1.9) * 5)
        o.distinct_communities = 3 + k
        o.distinct_authors = int(o.consumer_mentions * .9)
        o.community_counts = [o.consumer_mentions // max(1, 3 + k)] * (3 + k)
    sig = score_entity(h)
    assert sig.social_z > 4
    assert "COOLING" not in sig.flags, sig.flags
    assert sig.acceleration > 0, sig.acceleration

def test_genuinely_fading_is_marked_cooling():
    h = build()
    for k, o in enumerate(h[-14:]):
        bump = math.exp(-((k - 6) ** 2) / 6.0)
        o.consumer_mentions = int(8 * (1 + 3.0 * bump))
        o.distinct_communities = 3 + int(4 * bump)
        o.distinct_authors = int(o.consumer_mentions * .85)
        o.community_counts = [max(1, o.consumer_mentions // max(1, o.distinct_communities))] * o.distinct_communities
    # peak was ~6 days ago; the last three days are clearly below the four
    # before them, which is what "cooling" has to mean
    for k, o in enumerate(h[-3:]):
        o.consumer_mentions = 12 - 2 * k
        o.distinct_communities = 5
        o.distinct_authors = 11
        o.community_counts = [3, 3, 2, 2, 2]
    sig = score_entity(h)
    assert sig.acceleration < 0, sig.acceleration

def test_herfindahl_and_breadth_directions():
    assert herfindahl([100]) == 1.0
    assert herfindahl([10]*10) < 0.2
    assert breadth_score(10, 100, 0.1) > breadth_score(1, 5, 0.95)

if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1; print(f"  FAIL  {name}: {e}")
            except Exception as e:
                fails += 1; print(f"  ERR   {name}: {type(e).__name__}: {e}")
    print("\n" + ("all green" if not fails else f"{fails} failing"))
    sys.exit(1 if fails else 0)

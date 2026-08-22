# Signal Desk

A social-arbitrage scanner. It watches consumer communities for products getting
talked about unusually often, checks whether the investing crowd has noticed yet,
and flags the gap.

It does **not** trade. It reads, scores, and tells you when something changed.

---

## What it actually measures

The naive version of this — "count mentions, alert if today is double yesterday" —
produces a firehose of garbage. Five things stop that:

| | |
|---|---|
| **Normalised counts** | Mentions as a share of everything sampled that day, so "Reddit was busy" and "this subreddit tripled in size" don't register as signal. |
| **Robust baselines** | Median/MAD instead of mean/stdev. A spike six weeks ago would poison a mean-based baseline for a month — which would blind you to exactly the thing you're watching for. |
| **Breadth gating** | A spike is only credited to the extent it's spread across many communities and many accounts. One thread with 4,000 comments is *not* a trend, and the score reflects that. |
| **The lead ratio** | Consumer chatter ÷ investing-forum chatter. This is the whole thesis: when r/stocks catches up, the edge is gone. Tracked explicitly so you can see it disappear. |
| **Unpriced check** | Attention is only interesting if the tape hasn't reacted. High chatter + flat price + normal volume is the setup; high chatter after a 20% run is you being late. |

Direction (positive / negative / mixed) is reported **separately** from the score,
because a brand melting down is a loud, high-attention event that isn't the same
finding as a brand taking off.

## What it costs

Nothing. Reddit's free API tier, Wikipedia pageviews, GDELT news volume, and Stooq
prices — no paid plan, no credit card. It runs on GitHub Actions' free scheduled
runners and publishes to GitHub Pages.

## Setup (about ten minutes, once)

1. **Create a Reddit app.** <https://www.reddit.com/prefs/apps> → *create another
   app* → choose **script** → any name → redirect URI `http://localhost:8080`.
   Note the client ID (under the app name) and the secret.

2. **Fork/create the repo**, then add repository secrets under
   *Settings → Secrets and variables → Actions*:

   | Secret | Value |
   |---|---|
   | `REDDIT_CLIENT_ID` | from step 1 |
   | `REDDIT_CLIENT_SECRET` | from step 1 |
   | `USER_AGENT` | `signaldesk/0.1 by u/yourusername` |
   | `SLACK_WEBHOOK_URL` *or* `DISCORD_WEBHOOK_URL` | optional — where alerts go |

3. **Enable Pages**: *Settings → Pages → Source: GitHub Actions*.

4. **Run it once by hand**: *Actions → signal scan → Run workflow*.

After that it runs twice a day on its own and you never touch it again unless you
want to change the watchlist.

## The one file you edit

`config/watchlist.yaml`. Two rules decide whether this works at all:

- **Product names beat brand names.** `Cloudmonster` is unambiguous; `On` is
  useless; `Labubu` is perfect.
- **Always `exclude` everyday meanings.** `Celsius` without an exclusion list is a
  temperature unit and will bury the real signal in noise.

`python tests/test_score.py` runs the engine's test suite; the `tests` workflow
also lints the watchlist and warns about patterns likely to generate junk matches.

## Timeline

- **Day 1** — Wikipedia, news and price signals are backfilled 90 days, so those work
  immediately.
- **Day 1–21** — Reddit baselines are still forming. Scores exist but the alerting
  layer deliberately stays quiet below 21 days of history.
- **Day 21+** — fully operational.

Reddit history cannot be backfilled (Pushshift is closed to the public), so the
social baseline has to be accumulated by running. That wait is unavoidable.

## Limits worth knowing

- **Sampling, not census.** It reads ~100 recent posts + ~100 recent comments per
  subreddit per run. Fast-moving subs are undersampled. That's fine for relative
  z-scores against a consistent baseline; it is not a complete count.
- **No X, no TikTok.** X's API is pay-per-use and TikTok has no usable public
  trend API. Both plug in behind `collect.py`'s interface later without touching
  the scoring engine.
- **Wikipedia pageviews stand in for Google Trends**, since `pytrends` no longer
  works. It's a good free proxy for "how many people are looking this up", not an
  identical one.
- **Non-US tickers** (e.g. `9992.HK`) often have no Stooq price data, so the
  unpriced/late checks silently degrade for them.

## This is a research tool, not advice

A social spike is a hypothesis about attention, not a forecast of revenue or price.
Plenty of things trend hard and never sell; plenty of good businesses never trend.
Nothing here accounts for valuation, position sizing, or the fact that a private
brand's viral moment may sit inside a listed company too large for it to matter to.

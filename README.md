# Signal Desk

A social-arbitrage scanner. It watches where consumers actually talk and look
things up, finds products and brands getting unusually more attention than
their own normal, maps them to a listed stock, checks whether the investing
crowd and the share price have noticed yet, and publishes the result as a
dashboard twice a day.

**Dashboard:** https://conxapp.github.io/signal-desk/ · **Data:** `site/data.json`

It does **not** trade. It reads, scores, explains, and tells you what changed.

---

## What it does, in one run

1. **Samples attention** across free, keyless sources:
   - **Wikipedia pageviews** — how many people are looking a brand up (Google-Trends stand-in, 90-day backfill)
   - **GDELT** — news volume + the actual headlines
   - **Apple App Store** top-100 charts (US + AU) — consumer adoption for app companies
   - **Google Trends** daily trending searches (US + AU)
   - **Yahoo Finance** — daily prices/volume, and the *trending tickers* list (what the finance crowd is looking at)
   - **Reddit** *(optional, needs free API keys)* — consumer-community chatter vs. finance-forum chatter, sentiment, buying/scarcity language
2. **Scores two universes**
   - **Your watchlist** (`config/watchlist.yaml`) — hand-tuned names, with the Reddit-centric *social-arbitrage score* once 14+ days of Reddit history exist
   - **The discovery universe** (`config/brands.yaml`, ~440 consumer brands → tickers) — scored on cross-channel *attention* so names you are **not** watching surface when they light up
3. **Explains each flagged name** — which channel is driving it, whether it is rising / peaking / fading, the latest headlines, sample posts, what the stock did over 5/20 days, and what happened after that name's previous spikes. (With an `ANTHROPIC_API_KEY` secret the summary is written by Claude; otherwise it is assembled from the facts.)
4. **Publishes** `site/index.html` + `site/data.json` to GitHub Pages and commits the day's data back so baselines accumulate.

## How the scoring works

| | |
|---|---|
| **Own baseline, robust stats** | Every name is compared with *its own* trailing 45 days using median/MAD z-scores, so a mega-brand and a micro-brand are comparable and one old viral day cannot poison the baseline. |
| **Attention z** | The strongest channel's z-score, plus a bonus when other channels corroborate (Wikipedia + news + app chart + Reddit). |
| **Stage** | ↗ rising (still accelerating) · ▲ peaking · ↘ fading · quiet — from the last 14 days of the driving channel. |
| **Unpriced / late** | Attention is only interesting if the tape has not reacted: flat 5-day price and normal volume = *unpriced*; +15% in 5 days = *late*. |
| **Finance noticed** | Ticker is already on Yahoo's trending list — the edge is smaller. |
| **Past spikes** | Forward 10-day return after the name's previous ≥2.5σ spikes (small samples, context only). |
| **Social score (watchlist)** | Normalised mention share, breadth gating (many subs, many accounts), consumer-vs-finance lead ratio, buying/scarcity/negative language, direction reported separately. |

Scores are 0–100. 60+ is worth a look, 40–60 is "watch", below 40 is background.

## Setup (once, ~5 minutes)

The repo runs as-is on GitHub Actions' free runners with **no keys**. Two optional upgrades, both as repository secrets (*Settings → Secrets and variables → Actions*):

| Secret | What it unlocks |
|---|---|
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `USER_AGENT` | Reddit consumer-chatter channel. Create a free app at <https://www.reddit.com/prefs/apps> → *create another app* → type **script** → redirect URI `http://localhost:8080`. `USER_AGENT` = `signaldesk/0.2 by u/yourname`. Leave the "about url" box **empty**. |
| `ANTHROPIC_API_KEY` | Claude-written two-sentence briefings per flagged name (otherwise heuristic text). |
| `SLACK_WEBHOOK_URL` or `DISCORD_WEBHOOK_URL` | Push alerts for strong watchlist signals (only after 21 days of history, 5-day cooldown). |

Pages is already enabled with the *GitHub Actions* source; the `signal scan` workflow runs at 07:10 and 19:10 UTC and can be run by hand from the Actions tab.

## Files you might edit

- `config/watchlist.yaml` — your names. Product names beat brand names (`Cloudmonster` not `On`); always `exclude` everyday meanings.
- `config/brands.yaml` — the discovery universe. Each entry: display name, company, Yahoo-style ticker, Wikipedia article, consumer phrases, optional `exclude`/`context`, App Store app names. `private: true` keeps a non-investable brand visible as "trending, no stock".
- `config/sources.yaml` — which subreddits are sampled (consumer vs. finance are kept disjoint on purpose).

`python tests/test_score.py` and `python tests/test_attention.py` run offline; the `tests` workflow runs them on every push and checks that `brands.yaml` loads, keys are unique and common-word traps don't match.

## Timeline

- **Day 1** — Wikipedia, news, prices and App Store are backfilled/available, so discovery works immediately.
- **Day 1–21** — Reddit baselines (if enabled) are still forming; alerts stay quiet below 21 days.
- **Day 21+** — fully operational.

## Limits worth knowing

- Reddit history cannot be backfilled; the social baseline only accumulates by running.
- No X or TikTok: neither has a usable free API. Both plug in behind `collect.py` later without touching the scoring.
- GDELT allows one request per 5 seconds, so news volume is fetched for the watchlist plus the ~25 most interesting discovery names each run, headlines for the top ~15.
- Non-US tickers use Yahoo-style symbols (`9992.HK`, `WOW.AX`, `ADS.DE`); a few thinly traded names may have gaps.
- Brand → ticker mapping is a curated list; it will have gaps and occasional wrong parents. Fix them in `brands.yaml`.

## This is a research tool, not advice

A spike in attention is a hypothesis about demand, not a forecast of revenue or price. Plenty of things trend hard and never sell; plenty of good businesses never trend. Nothing here accounts for valuation, position sizing, or the fact that a viral brand may sit inside a listed company too large for it to matter.

"""
Data collectors. Every source here is free and needs no paid plan.

  Reddit          — free OAuth app, ~100 req/min. The main signal.
  Wikipedia       — pageviews API, no key, unlimited. Best free proxy for
                    "how many people are looking this up", and the closest
                    free stand-in for Google Trends now that pytrends is dead.
  GDELT           — free news-volume timeline, no key.
  Stooq           — free daily OHLCV CSV, no key.

Paid sources (X, TikTok, proper Google Trends) plug in behind the same
interface later without touching the scoring engine.
"""
from __future__ import annotations

import csv, io, json, os, time, uuid, datetime as dt
from urllib.parse import quote
import urllib.request, urllib.error

UA = os.environ.get("USER_AGENT", "sigscan/0.1 (personal research; contact: set USER_AGENT)")
TIMEOUT = 25


def _get(url: str, headers: dict | None = None, retries: int = 3):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt + 1)
                continue
            raise
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise last


def _get_json(url, headers=None):
    return json.loads(_get(url, headers))


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

class Reddit:
    """Read-only Reddit client.

    Two auth modes:
      * client_credentials  — app-only, needs just client id + secret
      * installed_client    — anonymous device grant, also id + secret

    We sample whole subreddits rather than running keyword searches. That costs
    the same number of requests but gives us the denominator (how much was
    posted at all today) for free, which is what makes the z-scores meaningful.
    """

    def __init__(self, client_id: str, client_secret: str):
        self.id, self.secret = client_id, client_secret
        self.token = None
        self.expires = 0

    def _auth(self):
        if self.token and time.time() < self.expires - 60:
            return
        import base64
        body = f"grant_type=https://oauth.reddit.com/grants/installed_client&device_id={uuid.uuid4()}"
        basic = base64.b64encode(f"{self.id}:{self.secret}".encode()).decode()
        req = urllib.request.Request(
            "https://www.reddit.com/api/v1/access_token",
            data=body.encode(),
            headers={"Authorization": f"Basic {basic}", "User-Agent": UA,
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.loads(r.read())
        self.token = d["access_token"]
        self.expires = time.time() + d.get("expires_in", 3600)

    def _api(self, path: str):
        self._auth()
        return _get_json(f"https://oauth.reddit.com{path}",
                         {"Authorization": f"Bearer {self.token}"})

    def sample(self, subreddit: str, limit: int = 100) -> list:
        """Recent posts + comments from one subreddit, flattened to documents."""
        docs = []
        for kind, path in (("post", f"/r/{subreddit}/new?limit={limit}"),
                           ("comment", f"/r/{subreddit}/comments?limit={limit}")):
            try:
                data = self._api(path)
            except Exception as e:
                print(f"  ! r/{subreddit} {kind}s: {type(e).__name__}: {e}")
                continue
            for child in data.get("data", {}).get("children", []):
                d = child.get("data", {})
                text = " ".join(filter(None, [d.get("title", ""),
                                              d.get("selftext", ""),
                                              d.get("body", "")]))[:4000]
                docs.append({
                    "kind": kind, "subreddit": subreddit,
                    "author": d.get("author", "") or "",
                    "created": d.get("created_utc", 0),
                    "score": d.get("score", 0),
                    "text": text,
                    "permalink": "https://reddit.com" + d.get("permalink", ""),
                })
            time.sleep(1.1)   # stay far under the 100/min ceiling
        return docs


# ---------------------------------------------------------------------------
# Wikipedia pageviews — the free Google Trends substitute
# ---------------------------------------------------------------------------

def wikipedia_views(article: str, start: dt.date, end: dt.date) -> dict:
    if not article:
        return {}
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
           f"en.wikipedia/all-access/user/{quote(article, safe='')}/daily/"
           f"{start:%Y%m%d}/{end:%Y%m%d}")
    try:
        data = _get_json(url)
    except Exception as e:
        print(f"  ! wikipedia {article}: {type(e).__name__}")
        return {}
    out = {}
    for item in data.get("items", []):
        ts = item["timestamp"][:8]
        out[f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"] = item.get("views", 0)
    return out


# ---------------------------------------------------------------------------
# GDELT news volume
# ---------------------------------------------------------------------------

def gdelt_volume(query: str, days: int = 60) -> dict:
    if not query:
        return {}
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
           f"{quote(query)}&mode=timelinevol&format=json&timespan={days}d")
    try:
        data = _get_json(url)
    except Exception as e:
        print(f"  ! gdelt {query}: {type(e).__name__}")
        return {}
    out = {}
    for series in data.get("timeline", []):
        for pt in series.get("data", []):
            day = pt["date"][:8]
            out[f"{day[:4]}-{day[4:6]}-{day[6:8]}"] = float(pt.get("value", 0))
    return out


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def stooq_prices(ticker: str) -> dict:
    """Daily OHLCV. Stooq wants lowercase with a market suffix: aapl.us"""
    if not ticker:
        return {}
    sym = ticker.lower()
    if "." not in sym:
        sym += ".us"
    try:
        raw = _get(f"https://stooq.com/q/d/l/?s={sym}&i=d").decode()
    except Exception as e:
        print(f"  ! stooq {ticker}: {type(e).__name__}")
        return {}
    out = {}
    for row in csv.DictReader(io.StringIO(raw)):
        try:
            out[row["Date"]] = {"close": float(row["Close"]), "volume": float(row["Volume"])}
        except (KeyError, ValueError):
            continue
    return out

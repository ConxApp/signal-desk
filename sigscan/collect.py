"""
Data collectors. Every source here is free and needs no paid plan or key.

  Reddit          — free OAuth app (needs REDDIT_CLIENT_ID/SECRET), ~100 req/min.
                    The consumer-chatter signal. Skipped cleanly when keys are absent.
  Wikipedia       — pageviews API, no key. Best free proxy for "how many people are
                    looking this up" (Google Trends stand-in). Backfills 90 days.
  GDELT           — free news-volume timeline + headlines, no key. Hard limit of
                    one request every 5 seconds, enforced here.
  Yahoo Finance   — daily close/volume via the public chart endpoint, no key.
                    (Stooq, the previous source, now blocks automated clients.)
  Yahoo trending  — which tickers the finance crowd is looking at right now.
  Apple App Store — top-free app chart (US + AU) — consumer adoption proxy.
  Google Trends   — the daily "trending searches" RSS feed (US + AU).

Every function returns an empty result on failure and records the outcome in
STATUS so the dashboard can show which sources were healthy this run.
"""
from __future__ import annotations

import csv, io, json, os, re, time, uuid, datetime as dt
from collections import defaultdict
from urllib.parse import quote
import urllib.request, urllib.error
import xml.etree.ElementTree as ET

UA = os.environ.get("USER_AGENT") or "signal-desk/0.2 (+https://github.com/ConxApp/signal-desk; research bot)"
TIMEOUT = 25

# source -> {"ok": n, "fail": n, "note": str}
STATUS: dict = defaultdict(lambda: {"ok": 0, "fail": 0, "note": ""})


def _mark(source: str, ok: bool, note: str = ""):
    s = STATUS[source]
    s["ok" if ok else "fail"] += 1
    if note and not ok:
        s["note"] = note[:120]


def _get(url: str, headers: dict | None = None, retries: int = 3, timeout: int = TIMEOUT) -> bytes:
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if headers:
        h.update(headers)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                wait = 2 ** attempt + 1
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra and str(ra).isdigit():
                    wait = min(int(ra), 30)
                time.sleep(wait)
                continue
            raise
        except Exception as e:  # timeouts, connection resets
            last = e
            time.sleep(2 ** attempt)
    raise last


def _get_json(url, headers=None, retries: int = 3):
    raw = _get(url, headers, retries=retries)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

class Reddit:
    """Read-only Reddit client using the app-only "installed client" grant.

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
                _mark("reddit", True)
            except Exception as e:
                print(f"  ! r/{subreddit} {kind}s: {type(e).__name__}: {e}")
                _mark("reddit", False, f"r/{subreddit}: {type(e).__name__}")
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
    """{'YYYY-MM-DD': views}. Empty dict if the article is missing or the API fails."""
    if not article:
        return {}
    title = article.replace(" ", "_")
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
           f"en.wikipedia/all-access/user/{quote(title, safe='')}/daily/"
           f"{start:%Y%m%d}/{end:%Y%m%d}")
    try:
        data = _get_json(url, retries=2)
        _mark("wikipedia", True)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  ! wikipedia {article}: HTTP {e.code}")
        _mark("wikipedia", False, f"{article}: HTTP {e.code}")
        return {}
    except Exception as e:
        print(f"  ! wikipedia {article}: {type(e).__name__}")
        _mark("wikipedia", False, f"{article}: {type(e).__name__}")
        return {}
    out = {}
    for item in data.get("items", []):
        ts = item["timestamp"][:8]
        out[f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"] = item.get("views", 0)
    return out


# ---------------------------------------------------------------------------
# GDELT news — volume timeline and headlines. Hard rate limit: 1 req / 5 s.
# ---------------------------------------------------------------------------

_GDELT_LAST = 0.0
GDELT_GAP = 5.2


def _gdelt(url: str):
    """Throttled GDELT call. Returns parsed JSON or None."""
    global _GDELT_LAST
    wait = GDELT_GAP - (time.time() - _GDELT_LAST)
    if wait > 0:
        time.sleep(wait)
    try:
        raw = _get(url, retries=2)
    except Exception as e:
        _GDELT_LAST = time.time()
        _mark("gdelt", False, type(e).__name__)
        return None
    _GDELT_LAST = time.time()
    txt = raw.decode("utf-8", "replace").strip()
    if not txt.startswith("{"):
        # GDELT returns plain-text messages (rate limit, bad query) with HTTP 200
        _mark("gdelt", False, txt[:80])
        if "limit requests" in txt:
            time.sleep(6)
        return None
    try:
        d = json.loads(txt)
    except json.JSONDecodeError:
        _mark("gdelt", False, "bad json")
        return None
    _mark("gdelt", True)
    return d


def gdelt_volume(query: str, days: int = 60) -> dict:
    """{'YYYY-MM-DD': volume_intensity}. Empty on failure."""
    if not query:
        return {}
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
           f"{quote(query)}&mode=timelinevol&format=json&timespan={days}d")
    data = _gdelt(url)
    if not data:
        return {}
    out = {}
    for series in data.get("timeline", []):
        for pt in series.get("data", []):
            day = pt["date"][:8]
            out[f"{day[:4]}-{day[4:6]}-{day[6:8]}"] = float(pt.get("value", 0))
    return out


def gdelt_headlines(query: str, days: int = 4, n: int = 6) -> list:
    """Recent headlines for a query: [{title, url, domain, date}]."""
    if not query:
        return []
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
           f"{quote(query)}&mode=artlist&maxrecords={n}&format=json"
           f"&timespan={days}d&sort=hybridrel")
    data = _gdelt(url)
    if not data:
        return []
    out, seen = [], set()
    for a in data.get("articles", []):
        title = (a.get("title") or "").strip()
        key = title.lower()[:60]
        if not title or key in seen:
            continue
        seen.add(key)
        sd = a.get("seendate", "")
        out.append({"title": title[:160], "url": a.get("url", ""),
                    "domain": a.get("domain", ""),
                    "date": f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) >= 8 else ""})
    return out


# ---------------------------------------------------------------------------
# Prices — Yahoo Finance public chart endpoint (no key)
# ---------------------------------------------------------------------------

def yahoo_prices(ticker: str, rng: str = "6mo") -> dict:
    """{'YYYY-MM-DD': {'close': float, 'volume': float}} for Yahoo-style symbols
    (AAPL, 9992.HK, WOW.AX, ADS.DE ...). Empty on failure."""
    if not ticker:
        return {}
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker)}"
           f"?range={rng}&interval=1d&includePrePost=false")
    try:
        data = _get_json(url, {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) signal-desk/0.2"}, retries=2)
    except Exception as e:
        print(f"  ! yahoo {ticker}: {type(e).__name__}")
        _mark("prices", False, f"{ticker}: {type(e).__name__}")
        return {}
    try:
        res = data["chart"]["result"][0]
        ts = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
        closes, vols = q.get("close") or [], q.get("volume") or []
    except (KeyError, IndexError, TypeError):
        _mark("prices", False, f"{ticker}: no data")
        return {}
    out = {}
    for i, t in enumerate(ts):
        c = closes[i] if i < len(closes) else None
        v = vols[i] if i < len(vols) else None
        if c is None:
            continue
        day = dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()
        out[day] = {"close": float(c), "volume": float(v or 0)}
    _mark("prices", bool(out), "" if out else f"{ticker}: empty")
    time.sleep(0.35)
    return out


def yahoo_trending(region: str = "US", count: int = 40) -> list:
    """Tickers trending on Yahoo Finance right now (the finance crowd's attention)."""
    url = f"https://query1.finance.yahoo.com/v1/finance/trending/{region}?count={count}"
    try:
        data = _get_json(url, {"User-Agent": "Mozilla/5.0 signal-desk/0.2"}, retries=2)
        syms = [q.get("symbol", "") for r in data.get("finance", {}).get("result", [])
                for q in r.get("quotes", [])]
        _mark("yahoo_trending", True)
        return [s for s in syms if s]
    except Exception as e:
        _mark("yahoo_trending", False, type(e).__name__)
        return []


# ---------------------------------------------------------------------------
# Apple App Store top charts — consumer adoption proxy for app companies
# ---------------------------------------------------------------------------

def apple_top_apps(country: str = "us", n: int = 100) -> list:
    """[{rank, name, artist, id}] for the top-free chart in a country."""
    url = f"https://rss.marketingtools.apple.com/api/v2/{country}/apps/top-free/{n}/apps.json"
    try:
        data = _get_json(url, retries=2)
        out = []
        for i, r in enumerate(data.get("feed", {}).get("results", []), start=1):
            out.append({"rank": i, "name": r.get("name", ""), "artist": r.get("artistName", ""),
                        "id": r.get("id", "")})
        _mark("appstore", True)
        return out
    except Exception as e:
        _mark("appstore", False, type(e).__name__)
        return []


# ---------------------------------------------------------------------------
# Google Trends daily trending searches (RSS)
# ---------------------------------------------------------------------------

def google_trends_rss(geo: str = "US") -> list:
    """[{title, traffic, news}] from the daily trending-searches feed."""
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    try:
        raw = _get(url, {"User-Agent": "Mozilla/5.0 signal-desk/0.2", "Accept": "application/rss+xml, */*"}, retries=2)
        root = ET.fromstring(raw)
    except Exception as e:
        _mark("google_trends", False, type(e).__name__)
        return []
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        traffic = (item.findtext("ht:approx_traffic", default="", namespaces=ns) or "").strip()
        news = []
        for ni in item.findall("ht:news_item", ns):
            t = ni.findtext("ht:news_item_title", default="", namespaces=ns)
            u = ni.findtext("ht:news_item_url", default="", namespaces=ns)
            if t:
                news.append({"title": t.strip()[:160], "url": (u or "").strip()})
        if title:
            out.append({"title": title, "traffic": traffic, "news": news[:3]})
    _mark("google_trends", True)
    return out


def traffic_to_int(s: str) -> int:
    """'200K+' -> 200000, '2M+' -> 2000000, '500+' -> 500."""
    m = re.match(r"\s*([\d.,]+)\s*([KkMm]?)", s or "")
    if not m:
        return 0
    num = float(m.group(1).replace(",", ""))
    mult = {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1)
    return int(num * mult)


def status_report() -> dict:
    """Plain-dict copy of STATUS for JSON output."""
    return {k: dict(v) for k, v in STATUS.items()}

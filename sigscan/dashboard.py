"""
Static dashboard renderer. One self-contained HTML page, no JavaScript needed,
light/dark aware. Charts are single-series sparklines (one hue each), text is
always in ink tokens, status colours only ever appear with an icon + label.
"""
from __future__ import annotations

import html, json, math
from datetime import datetime

CSS = """
:root{color-scheme:light dark;
 --page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--line:#c3c2b7;
 --ring:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--good:#006300;--warn:#b07a00;--bad:#d03b3b;
 --badge-hi:#1c5cab;--badge-mid:#6da7ec;--badge-lo:#cde2fb;--pill:#f0efec}
@media (prefers-color-scheme:dark){:root{--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--line:#383835;--ring:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--good:#0ca30c;--warn:#fab219;--bad:#e66767;
 --badge-hi:#3987e5;--badge-mid:#256abf;--badge-lo:#184f95;--pill:#383835}}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:inherit}.wrap{max-width:1080px;margin:0 auto;padding:28px 18px 60px}
header h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}header .sub{color:var(--ink2);margin:0 0 14px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0}.chip{font-size:12px;border:1px solid var(--ring);border-radius:999px;padding:2px 9px;background:var(--surface);color:var(--ink2)}
.chip.off{border-style:dashed}.chip b{color:var(--ink);font-weight:600}
.callout{margin:18px 0;padding:12px 14px;border:1px solid var(--ring);border-left:4px solid var(--s2);border-radius:8px;background:var(--surface);font-size:14px}
.callout code{background:var(--pill);padding:1px 5px;border-radius:4px;font-size:12.5px}
.sec{margin-top:34px}.sec-head{display:flex;align-items:baseline;gap:12px;margin-bottom:10px}.sec-head h2{font-size:18px;margin:0}
.sec-head .rule{flex:1;border-top:1px solid var(--grid)}.sec-head .meta{font-size:12.5px;color:var(--muted)}
.row{display:grid;grid-template-columns:minmax(180px,1.3fr) 90px minmax(140px,1fr) 130px 130px;gap:10px 14px;align-items:center;
 padding:12px 14px;border:1px solid var(--ring);border-radius:10px;background:var(--surface);margin-bottom:8px}
.row .name{font-weight:600}.row .name small{display:block;font-weight:400;color:var(--ink2);font-size:12.5px}
.row .name a{text-decoration:none;border-bottom:1px dotted var(--line)}
.score{display:inline-block;min-width:46px;text-align:center;font-weight:700;font-size:15px;border-radius:8px;padding:4px 8px;font-variant-numeric:tabular-nums}
.score.hi{background:var(--badge-hi);color:#fff}.score.mid{background:var(--badge-mid);color:#0b0b0b}.score.lo{background:var(--badge-lo);color:#0b0b0b}
@media (prefers-color-scheme:dark){.score.mid,.score.lo{color:#fff}}
.stage{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;background:var(--pill);color:var(--ink2);margin-left:6px}
.flags{display:flex;flex-wrap:wrap;gap:4px}.flag{font-size:11px;border:1px solid var(--line);border-radius:5px;padding:1px 6px;color:var(--ink2);white-space:nowrap}
.flag.warn{border-color:var(--warn);color:var(--warn)}.flag.good{border-color:var(--good);color:var(--good)}
.spark{display:flex;flex-direction:column;align-items:flex-start}.spark svg{display:block}.spark .cap{font-size:11px;color:var(--muted);margin-top:2px}
.why{grid-column:1/-1;margin:2px 0 0;padding:10px 12px;border-top:1px dashed var(--grid);font-size:13.5px;color:var(--ink2)}
.why p.sum{margin:0 0 6px;color:var(--ink)}.why ul{margin:4px 0 0;padding-left:18px}.why li{margin:2px 0}
.why .hl{margin-top:6px}.why .hl a{color:var(--ink2)}.why .q{margin-top:6px;font-style:italic}
.why .tag{font-size:11px;color:var(--muted);margin-left:6px}
details summary{cursor:pointer;color:var(--ink2);font-size:13px;list-style:none}details summary::-webkit-details-marker{display:none}
details summary:before{content:"▸ "}details[open] summary:before{content:"▾ "}
.mini{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.mini .box{border:1px solid var(--ring);border-radius:10px;background:var(--surface);padding:10px 14px;font-size:13.5px}
.mini h3{margin:0 0 6px;font-size:13px;color:var(--ink2);text-transform:uppercase;letter-spacing:.04em}.mini ol{margin:0;padding-left:18px}.mini li{margin:2px 0}
.howto{font-size:13.5px;color:var(--ink2)}.howto dt{font-weight:600;color:var(--ink);margin-top:8px}.howto dd{margin:0}
footer{margin-top:40px;font-size:12.5px;color:var(--muted)}
@media (max-width:760px){.row{grid-template-columns:1fr 1fr}.row .spark{grid-column:span 1}.row .flags{grid-column:1/-1}}
"""

FLAG_WARN = {"LATE", "FADING", "NEGATIVE_TONE", "NEGATIVE_TURN", "NARROW_SPIKE", "FINANCE_NOTICED",
             "FINANCE_ONLY", "COOLING"}
FLAG_GOOD = {"UNPRICED", "AHEAD_OF_THE_STREET", "BROAD_SPIKE", "RISING"}
FLAG_LABEL = {
    "ATTENTION_SPIKE": "attention spike", "MULTI_CHANNEL": "multi-channel", "TRENDING_SEARCH": "trending search",
    "APP_CLIMBING": "app climbing", "FINANCE_NOTICED": "finance noticed", "UNPRICED": "unpriced", "LATE": "late",
    "FADING": "fading", "RISING": "rising", "NEGATIVE_TONE": "negative tone", "BROAD_SPIKE": "broad spike",
    "NARROW_SPIKE": "narrow spike", "AHEAD_OF_THE_STREET": "ahead of the street", "FINANCE_ONLY": "finance only",
    "SCARCITY": "scarcity", "NEGATIVE_TURN": "negative turn", "COOLING": "cooling",
}
STAGE_GLYPH = {"rising": "↗ rising", "peaking": "▲ peaking", "fading": "↘ fading", "quiet": "· quiet"}


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def sparkline(values, w=120, h=28, color="var(--s1)", label="") -> str:
    vals = [float(v) for v in (values or []) if v is not None]
    if len(vals) < 2:
        return f'<svg width="{w}" height="{h}" role="img" aria-label="{_esc(label)}: no data"><line x1="0" y1="{h-1}" x2="{w}" y2="{h-1}" stroke="var(--grid)"/></svg>'
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = 2 + (w - 4) * i / (n - 1)
        y = 2 + (h - 4) * (1 - (v - lo) / rng)
        pts.append(f"{x:.1f},{y:.1f}")
    lx, ly = pts[-1].split(",")
    title = f"{label}: {vals[-1]:.2f} (range {lo:.2f}–{hi:.2f}, {n} pts)"
    return (f'<svg width="{w}" height="{h}" role="img" aria-label="{_esc(title)}"><title>{_esc(title)}</title>'
            f'<line x1="0" y1="{h-1}" x2="{w}" y2="{h-1}" stroke="var(--grid)"/>'
            f'<polyline fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="{" ".join(pts)}"/>'
            f'<circle cx="{lx}" cy="{ly}" r="3" fill="{color}"/></svg>')


def _score_badge(score: float) -> str:
    cls = "hi" if score >= 60 else "mid" if score >= 40 else "lo"
    return f'<span class="score {cls}" title="score 0–100">{score:.0f}</span>'


def _chips(flags) -> str:
    out = []
    for f in flags or []:
        cls = "warn" if f in FLAG_WARN else "good" if f in FLAG_GOOD else ""
        glyph = "⚠ " if f in FLAG_WARN else ""
        out.append(f'<span class="flag {cls}">{glyph}{_esc(FLAG_LABEL.get(f, f.lower().replace("_", " ")))}</span>')
    return f'<div class="flags">{"".join(out)}</div>'


def _ticker_link(ticker: str) -> str:
    if not ticker:
        return '<span class="tag">not listed</span>'
    return f'<a href="https://finance.yahoo.com/quote/{_esc(ticker)}" target="_blank" rel="noopener">{_esc(ticker)}</a>'


def _why(item: dict, att: dict) -> str:
    ex = item.get("explain") or {}
    why = list(att.get("why") or []) + [w for w in (item.get("why") or []) if w not in (att.get("why") or [])]
    parts = []
    if ex.get("summary"):
        src = ' <span class="tag">AI</span>' if ex.get("source") == "claude" else ""
        parts.append(f'<p class="sum">{_esc(ex["summary"])}{src}</p>')
    if why:
        parts.append("<ul>" + "".join(f"<li>{_esc(w)}</li>" for w in why[:6]) + "</ul>")
    hls = item.get("headlines") or []
    if hls:
        links = " · ".join(f'<a href="{_esc(h.get("url"))}" target="_blank" rel="noopener">{_esc(h.get("title"))}</a>'
                           + (f' <span class="tag">{_esc(h.get("domain"))}</span>' if h.get("domain") else "")
                           for h in hls[:3])
        parts.append(f'<div class="hl">News: {links}</div>')
    exs = sorted(item.get("examples") or [], key=lambda e: -(e.get("score") or 0))[:2]
    for e in exs:
        parts.append(f'<div class="q">“{_esc(e.get("text"))}” — <a href="{_esc(e.get("url"))}" target="_blank" rel="noopener">r/{_esc(e.get("sub"))}</a></div>')
    ch = att.get("channels") or {}
    if ch:
        names = {"wiki": "Wikipedia", "news": "news", "social": "Reddit", "app": "App Store"}
        parts.append('<div class="hl tag">channels: ' + ", ".join(f"{names.get(k,k)} {v:+.1f}σ" for k, v in ch.items()) +
                     (f" · 5d {att.get('price_return_5d',0)*100:+.1f}% · 20d {att.get('price_return_20d',0)*100:+.1f}%" if att.get("has_prices") else " · no price data") + "</div>")
    if not parts:
        return ""
    return f'<div class="why">{"".join(parts)}</div>'


def _row(item: dict, kind: str, open_why: bool = True) -> str:
    att = item.get("attention") or {}
    score = item.get("rank_score", item.get("attention_score", item.get("arb_score", 0))) or 0
    stage = att.get("stage", "")
    flags = list(dict.fromkeys((item.get("flags") or []) + (att.get("flags") or [])))
    sub = item.get("company") or item.get("kind") or ""
    if kind == "discovered" and item.get("private"):
        sub = (sub + " · " if sub else "") + "no listed stock"
    if kind == "discovered" and item.get("sector"):
        sub = (sub + " · " if sub else "") + str(item["sector"])
    name = f'<div class="name">{_esc(item.get("name"))} {_ticker_link(item.get("ticker"))}<small>{_esc(sub)}</small></div>'
    badge = _score_badge(float(score)) + (f'<span class="stage">{STAGE_GLYPH.get(stage, stage)}</span>' if stage else "")
    zs = att.get("z_spark") or item.get("sparkline") or []
    s1 = f'<div class="spark">{sparkline(zs, label="attention (σ vs baseline)")}<span class="cap">attention, 30d</span></div>'
    ps = att.get("price_spark") or []
    s2 = f'<div class="spark">{sparkline(ps, color="var(--s2)", label="price")}<span class="cap">{"price, 30d" if ps else "price: n/a"}</span></div>'
    why = _why(item, att)
    if why and not open_why:
        why = f'<details><summary>why</summary>{why}</details>'
    return f'<div class="row">{name}<div>{badge}</div>{_chips(flags)}{s1}{s2}{why}</div>'


def _sources(payload: dict) -> str:
    st = payload.get("sources") or {}
    names = [("reddit", "Reddit"), ("wikipedia", "Wikipedia"), ("gdelt", "News (GDELT)"), ("prices", "Prices (Yahoo)"),
             ("appstore", "App Store"), ("google_trends", "Google Trends"), ("yahoo_trending", "Yahoo trending")]
    chips = []
    for key, label in names:
        s = st.get(key)
        if key == "reddit" and not payload.get("reddit_enabled"):
            chips.append(f'<span class="chip off"><b>{label}</b> off</span>')
            continue
        if not s:
            chips.append(f'<span class="chip off"><b>{label}</b> —</span>')
            continue
        ok, fail = s.get("ok", 0), s.get("fail", 0)
        mark = "✓" if ok and fail <= ok else "⚠"
        chips.append(f'<span class="chip"><b>{label}</b> {mark} {ok} ok' + (f", {fail} failed" if fail else "") + "</span>")
    stats = payload.get("stats") or {}
    if stats.get("ai"):
        chips.append('<span class="chip"><b>AI summaries</b> on</span>')
    return f'<div class="chips">{"".join(chips)}</div>'


def build_body(payload: dict, demo: bool = False) -> str:
    date = payload.get("date", "")
    gen = payload.get("generated", "")
    try:
        gen_h = datetime.fromisoformat(gen.replace("Z", "+00:00")).strftime("%d %b %Y %H:%M UTC")
    except Exception:
        gen_h = gen
    signals = payload.get("signals") or []
    discovered = payload.get("discovered") or []
    stats = payload.get("stats") or {}

    def _score(x):
        return x.get("rank_score", x.get("attention_score", x.get("arb_score", 0))) or 0

    top = [s for s in signals if (s.get("flags") or (s.get("attention") or {}).get("flags"))] + \
          [d for d in discovered if d.get("flags")]
    top = sorted(top, key=lambda x: -_score(x))[:8]

    out = [f'<header><h1>Signal Desk</h1><p class="sub">Consumer attention vs. the stock tape · scan of {_esc(date)} · generated {_esc(gen_h)}'
           + (" · DEMO DATA" if demo else "") + "</p>" + _sources(payload) + "</header>"]

    if not payload.get("reddit_enabled"):
        out.append('<div class="callout"><b>Reddit is switched off</b> — the scanner is running on Wikipedia lookups, news volume, '
                   'App Store charts, Google Trends and prices. To add the consumer-chatter signal (the strongest early-warning channel), '
                   'add three repository secrets on GitHub — <code>REDDIT_CLIENT_ID</code>, <code>REDDIT_CLIENT_SECRET</code>, '
                   '<code>USER_AGENT</code> — under Settings → Secrets and variables → Actions. Nothing else changes.</div>')

    out.append('<section class="sec"><div class="sec-head"><h2>Top signals right now</h2><span class="rule"></span>'
               f'<span class="meta">{len(top)} flagged</span></div>')
    out.append("".join(_row(x, "discovered" if "key" in x else "watch") for x in top) if top else
               '<p class="howto">Nothing is flagged today. Baselines are still forming for new names — flags appear once a name is 2–3σ above its own 45-day normal.</p>')
    out.append("</section>")

    out.append('<section class="sec"><div class="sec-head"><h2>Discovered — trending names you are not watching</h2><span class="rule"></span>'
               f'<span class="meta">{stats.get("brands_scored", len(discovered))} brands scanned</span></div>')
    rows = [d for d in discovered if not d.get("on_watchlist")][:30]
    out.append("".join(_row(d, "discovered", open_why=(i < 6)) for i, d in enumerate(rows)) if rows else
               '<p class="howto">No discovery data yet.</p>')
    out.append("</section>")

    out.append('<section class="sec"><div class="sec-head"><h2>Your watchlist</h2><span class="rule"></span>'
               f'<span class="meta">{len(signals)} names · ranked by social score once 14 days of Reddit history exist, otherwise by attention</span></div>')
    out.append("".join(_row(s, "watch", open_why=(i < 5)) for i, s in enumerate(signals)) if signals else '<p class="howto">No watchlist data yet.</p>')
    out.append("</section>")

    tr = payload.get("trends_raw") or {}
    fin = payload.get("finance_trending") or []
    boxes = []
    for geo in ("US", "AU"):
        items = tr.get(geo) or []
        if items:
            boxes.append(f'<div class="box"><h3>Google trending searches · {geo}</h3><ol>' +
                         "".join(f'<li>{_esc(i.get("title"))} <span class="tag">{_esc(i.get("traffic"))}</span></li>' for i in items[:10]) + "</ol></div>")
    if fin:
        boxes.append('<div class="box"><h3>Yahoo Finance trending tickers</h3><p>' +
                     ", ".join(f'<a href="https://finance.yahoo.com/quote/{_esc(t)}" target="_blank" rel="noopener">{_esc(t)}</a>' for t in fin[:30]) + "</p></div>")
    if boxes:
        out.append('<section class="sec"><div class="sec-head"><h2>What the crowd is looking at</h2><span class="rule"></span></div>'
                   f'<div class="mini">{"".join(boxes)}</div></section>')

    out.append('<section class="sec"><div class="sec-head"><h2>How to read this</h2><span class="rule"></span></div><dl class="howto">'
               '<dt>Score (0–100)</dt><dd>How unusual, broad, fresh and <i>unpriced</i> the attention is. 60+ is worth a look; 40–60 is “watch”; below 40 is background.</dd>'
               '<dt>σ (sigma)</dt><dd>How far above a name’s <i>own</i> 45-day normal today is. 2σ is notable, 3σ+ is a genuine spike. Every name is judged against itself, so a giant and a micro-brand are comparable.</dd>'
               '<dt>Stage</dt><dd>↗ rising = still accelerating · ▲ peaking = near its recent high · ↘ fading = coming off a peak · quiet = nothing unusual.</dd>'
               '<dt>Unpriced / late</dt><dd>Unpriced = attention is high but the stock is flat on normal volume (the setup). Late = the stock already ran 15%+ in 5 days.</dd>'
               '<dt>Finance noticed</dt><dd>The ticker is already trending on Yahoo Finance — the investing crowd is looking, so the information edge is smaller.</dd>'
               '<dt>Past spikes</dt><dd>What the stock did in the 10 trading days after this name’s previous attention spikes. Small samples — treat as context, not a forecast.</dd>'
               '<dt>Channels</dt><dd>Wikipedia lookups (how many people are googling it), news volume (GDELT), Reddit consumer chatter (when enabled), App Store rank, Google trending searches.</dd>'
               '</dl><p class="howto">Research tool, not advice. A spike in attention is a hypothesis about demand, not a forecast of revenue or price.</p></section>')

    out.append(f'<footer>Signal Desk · {stats.get("watchlist", len(signals))} watchlist names · {stats.get("brands", "")} brands in the discovery universe · '
               f'run took {stats.get("elapsed_s", "?")}s · <a href="https://github.com/ConxApp/signal-desk">source</a> · data.json alongside this page</footer>')
    return "\n".join(out)


def render(payload: dict, path: str, demo: bool = False):
    body = build_body(payload, demo)
    doc = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
           "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
           "<title>Signal Desk</title>"
           f"<style>{CSS}</style></head><body><div class=\"wrap\">{body}</div></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


def render_fragment(payload: dict, path: str, demo: bool = False):
    frag = f"<title>Signal Desk</title><style>{CSS}</style><div class=\"wrap\">{build_body(payload, demo)}</div>"
    with open(path, "w", encoding="utf-8") as f:
        f.write(frag)

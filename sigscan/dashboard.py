"""
Renders the payload to a self-contained HTML page.

Same renderer for the live GitHub Pages dashboard and for a fragment suitable
for publishing as a Claude Artifact.
"""
from __future__ import annotations
import html, json

FONTS = ("https://fonts.googleapis.com/css2?"
         "family=IBM+Plex+Mono:wght@400;500;600&"
         "family=IBM+Plex+Sans:wght@400;500;600&"
         "family=IBM+Plex+Sans+Condensed:wght@600;700&display=swap")

# Sequential blue ramp — arb score is continuous magnitude, so one hue
# light->dark is the correct encoding. Status colours are reserved for
# caution states and never reused as a "series".
CSS = """
:root {
  color-scheme: light;
  --ground:        #f4f4f2;
  --surface:       #ffffff;
  --surface-2:     #fafaf9;
  --line:          #e3e3df;
  --line-strong:   #cfcfca;
  --ink:           #14161a;
  --ink-2:         #55585f;
  --ink-3:         #82868e;
  --accent:        #2a78d6;
  --accent-soft:   #cde2fb;
  --accent-mid:    #86b6ef;
  --good:          #0ca30c;
  --warning:       #fab219;
  --serious:       #ec835a;
  --critical:      #d03b3b;
  --chip:          #f0efec;
  --shadow:        0 1px 2px rgba(20,22,26,.06), 0 8px 24px -16px rgba(20,22,26,.25);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --ground:      #121312;
    --surface:     #1a1a19;
    --surface-2:   #201f1e;
    --line:        #2e2e2c;
    --line-strong: #43433f;
    --ink:         #f4f4f1;
    --ink-2:       #b6b5ad;
    --ink-3:       #86857d;
    --accent:      #3987e5;
    --accent-soft: #184f95;
    --accent-mid:  #256abf;
    --good:        #0ca30c;
    --warning:     #fab219;
    --serious:     #ec835a;
    --critical:    #d03b3b;
    --chip:        #2a2a27;
    --shadow:      0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --ground:      #121312;
  --surface:     #1a1a19;
  --surface-2:   #201f1e;
  --line:        #2e2e2c;
  --line-strong: #43433f;
  --ink:         #f4f4f1;
  --ink-2:       #b6b5ad;
  --ink-3:       #86857d;
  --accent:      #3987e5;
  --accent-soft: #184f95;
  --accent-mid:  #256abf;
  --good:        #0ca30c;
  --warning:     #fab219;
  --serious:     #ec835a;
  --critical:    #d03b3b;
  --chip:        #2a2a27;
  --shadow:      0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.7);
}

* { box-sizing: border-box; }
body {
  margin: 0; background: var(--ground); color: var(--ink);
  font-family: "IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, sans-serif;
  font-size: 15px; line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 28px 22px 80px; }

h1, h2, h3, .cond {
  font-family: "IBM Plex Sans Condensed", "IBM Plex Sans", ui-sans-serif, sans-serif;
  font-weight: 700; letter-spacing: -.01em; text-wrap: balance;
}
.mono, .num {
  font-family: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
  font-variant-numeric: tabular-nums;
}
.eyebrow {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--ink-3); font-weight: 500;
}

/* ---------- banner ---------- */
.demo-banner {
  display: flex; gap: 12px; align-items: flex-start;
  background: var(--surface); border: 1px solid var(--warning);
  border-left: 4px solid var(--warning);
  padding: 14px 16px; margin-bottom: 24px; border-radius: 3px;
}
.demo-banner svg { flex: none; margin-top: 2px; }
.demo-banner b { font-weight: 600; }
.demo-banner p { margin: 2px 0 0; color: var(--ink-2); font-size: 14px; }

/* ---------- header ---------- */
header.top {
  display: flex; flex-wrap: wrap; gap: 16px 28px;
  align-items: baseline; justify-content: space-between;
  padding-bottom: 18px; margin-bottom: 22px;
  border-bottom: 1px solid var(--line-strong);
}
header.top h1 { margin: 0; font-size: 27px; }
header.top .sub { color: var(--ink-2); font-size: 14px; margin: 4px 0 0; max-width: 60ch; }

/* ---------- summary strip ---------- */
.strip {
  display: grid; gap: 1px; background: var(--line);
  border: 1px solid var(--line); border-radius: 3px; overflow: hidden;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  margin-bottom: 30px;
}
.stat { background: var(--surface); padding: 14px 16px; }
.stat .v { font-size: 30px; font-weight: 500; line-height: 1.1; display: block; }
.stat .l { font-size: 11.5px; color: var(--ink-3); letter-spacing: .06em;
           text-transform: uppercase; font-family: "IBM Plex Mono", monospace; }
.stat.hot .v { color: var(--accent); }

/* ---------- section ---------- */
.sec-head { display: flex; align-items: baseline; gap: 12px; margin: 34px 0 12px; }
.sec-head h2 { margin: 0; font-size: 18px; }
.sec-head .rule { flex: 1; height: 1px; background: var(--line-strong); }

/* ---------- rows ---------- */
.rows { display: flex; flex-direction: column; gap: 1px;
        background: var(--line); border: 1px solid var(--line); border-radius: 3px;
        overflow: hidden; }
.row { background: var(--surface); display: grid;
       grid-template-columns: minmax(190px,1.5fr) 104px 1fr 120px;
       gap: 18px; align-items: center; padding: 13px 16px; }
.row:hover { background: var(--surface-2); }
.row.lead { border-left: 3px solid var(--accent); }

.ident .nm { font-weight: 600; font-size: 15px; }
.ident .tk { font-size: 12px; color: var(--ink-3); letter-spacing: .04em; }
.ident .chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }

.chip { font-family: "IBM Plex Mono", monospace; font-size: 10.5px; font-weight: 500;
        letter-spacing: .04em; padding: 2px 6px; border-radius: 2px;
        background: var(--chip); color: var(--ink-2); display: inline-flex;
        align-items: center; gap: 4px; white-space: nowrap; }
.chip.pos  { background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent); }
.chip.warn { background: color-mix(in srgb, var(--warning) 20%, transparent);
             color: color-mix(in srgb, var(--warning) 70%, var(--ink)); }
.chip.bad  { background: color-mix(in srgb, var(--critical) 15%, transparent); color: var(--critical); }
.chip .dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; flex: none; }

/* score meter */
.score { text-align: right; }
.score .n { font-size: 21px; font-weight: 500; line-height: 1; }
.meter { height: 4px; background: var(--line); border-radius: 2px; margin-top: 6px; overflow: hidden; }
.meter i { display: block; height: 100%; background: var(--accent); border-radius: 2px; }

/* metrics */
.metrics { display: flex; gap: 20px; flex-wrap: wrap; }
.metric .k { font-size: 10px; letter-spacing: .07em; text-transform: uppercase;
             color: var(--ink-3); font-family: "IBM Plex Mono", monospace; display: block; }
.metric .v { font-size: 15px; font-weight: 500; }
.metric .v.up { color: var(--accent); }
.metric .v.dn { color: var(--ink-3); }
.metric .v.warn { color: color-mix(in srgb, var(--warning) 72%, var(--ink)); }

/* why */
.why { grid-column: 1 / -1; margin: 2px 0 0; padding: 10px 12px;
       background: var(--surface-2); border-left: 2px solid var(--accent-mid);
       font-size: 13.5px; color: var(--ink-2); border-radius: 2px; }
.why ul { margin: 0; padding-left: 17px; }
.why li { margin: 2px 0; }
.why .q { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--line-strong);
          font-size: 12.5px; color: var(--ink-3); font-style: italic; }

/* sparkline */
.spark { display: flex; flex-direction: column; align-items: flex-end; gap: 3px; }
.spark svg { display: block; }
.spark .cap { font-size: 10px; color: var(--ink-3); font-family: "IBM Plex Mono", monospace;
              letter-spacing: .05em; }

.empty { background: var(--surface); border: 1px dashed var(--line-strong);
         padding: 26px; text-align: center; color: var(--ink-2); border-radius: 3px; }

table.legend { border-collapse: collapse; width: 100%; font-size: 13.5px; }
table.legend td { padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
table.legend td:first-child { white-space: nowrap; width: 1%; }
.scroll { overflow-x: auto; }

footer { margin-top: 46px; padding-top: 18px; border-top: 1px solid var(--line);
         color: var(--ink-3); font-size: 12.5px; }
footer p { margin: 4px 0; }

@media (max-width: 760px) {
  .row { grid-template-columns: 1fr 90px; }
  .metrics { grid-column: 1 / -1; }
  .spark { grid-column: 1 / -1; align-items: flex-start; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""

WARN_FLAGS = {"NARROW_SPIKE", "LATE", "COOLING", "FINANCE_ONLY"}
BAD_FLAGS = {"NEGATIVE_TURN"}
GOOD_FLAGS = {"BROAD_SPIKE", "AHEAD_OF_THE_STREET", "UNPRICED", "SCARCITY"}

FLAG_LABEL = {
    "BROAD_SPIKE": "Broad spike",
    "NARROW_SPIKE": "One viral thread",
    "AHEAD_OF_THE_STREET": "Ahead of the street",
    "FINANCE_ONLY": "Finance chatter only",
    "UNPRICED": "Not priced in",
    "LATE": "Already moved",
    "SCARCITY": "Selling out",
    "NEGATIVE_TURN": "Sentiment negative",
    "COOLING": "Cooling off",
}


def _esc(s):
    return html.escape(str(s or ""))


def sparkline(values, w=112, h=28, accent="var(--accent)"):
    """Area + 2px line + emphasised endpoint, per the mark spec."""
    vals = [float(v or 0) for v in values][-30:]
    if len(vals) < 2:
        return '<div class="cap">no history yet</div>'
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    pad = 3
    step = (w - 2) / (len(vals) - 1)
    pts = [(1 + i * step, pad + (h - 2 * pad) * (1 - (v - lo) / rng)) for i, v in enumerate(vals)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"1,{h} " + line + f" {pts[-1][0]:.1f},{h}"
    ex, ey = pts[-1]
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="mentions trend, last {len(vals)} days">'
        f'<polygon points="{area}" fill="{accent}" opacity=".13"/>'
        f'<polyline points="{line}" fill="none" stroke="{accent}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="3.2" fill="{accent}" '
        f'stroke="var(--surface)" stroke-width="2"/></svg>'
    )


def _chips(flags):
    out = []
    for f in flags:
        cls = "pos" if f in GOOD_FLAGS else "warn" if f in WARN_FLAGS else "bad" if f in BAD_FLAGS else ""
        out.append(f'<span class="chip {cls}"><span class="dot"></span>{_esc(FLAG_LABEL.get(f, f))}</span>')
    return "".join(out)


def _row(s, show_why=True):
    lead = " lead" if s.get("arb_score", 0) >= 60 else ""
    z = s.get("social_z", 0)
    ret = s.get("price_return_5d", 0) * 100
    metrics = [
        ("chatter", f"{z:+.1f}σ", "up" if z >= 2 else "dn"),
        ("breadth", f"{s.get('breadth',0):.2f}", "up" if s.get("breadth", 0) >= .45 else "dn"),
        ("lead", f"{s.get('lead_ratio',0):.1f}×", "up" if s.get("lead_ratio", 0) >= 2.5 else "dn"),
        ("5d px", f"{ret:+.1f}%", "warn" if abs(ret) >= 15 else "dn"),
    ]
    mh = "".join(
        f'<div class="metric"><span class="k">{k}</span>'
        f'<span class="v {c} num">{_esc(v)}</span></div>' for k, v, c in metrics)

    why = ""
    if show_why and s.get("why"):
        items = "".join(f"<li>{_esc(w)}</li>" for w in s["why"][:4])
        quote = ""
        ex = (s.get("examples") or [])
        if ex:
            e = ex[0]
            quote = (f'<div class="q">“{_esc(e.get("text","")[:170])}…” '
                     f'— r/{_esc(e.get("sub",""))}</div>')
        why = f'<div class="why"><ul>{items}</ul>{quote}</div>'

    dirn = s.get("direction", "mixed")
    dir_cls = {"positive": "pos", "negative": "bad", "mixed": ""}[dirn]
    dir_label = {"positive": "positive", "negative": "negative", "mixed": "mixed"}[dirn]
    dir_chip = (f'<span class="chip {dir_cls}" title="tone of the chatter">'
                f'<span class="dot"></span>{dir_label}</span>')

    days = s.get("history_days", 0)
    cap = f"{days}d history" if days < 21 else f"{s.get('mentions',0)} mentions"

    return f"""
  <div class="row{lead}">
    <div class="ident">
      <div class="nm">{_esc(s.get('name'))}</div>
      <div class="tk mono">{_esc(s.get('ticker'))} · {_esc(s.get('kind'))}</div>
      <div class="chips">{dir_chip}{_chips(s.get('flags', []))}</div>
    </div>
    <div class="score">
      <div class="n num">{s.get('arb_score', 0):.0f}</div>
      <div class="meter"><i style="width:{max(2,min(100,s.get('arb_score',0))):.0f}%"></i></div>
    </div>
    <div class="metrics">{mh}</div>
    <div class="spark">{sparkline(s.get('sparkline', []))}<span class="cap">{_esc(cap)}</span></div>
    {why}
  </div>"""


def build_body(payload: dict, demo: bool = False) -> str:
    sigs = payload.get("signals", [])
    flagged = [s for s in sigs if s.get("flags") and any(f in GOOD_FLAGS for f in s["flags"])]
    quiet = [s for s in sigs if s not in flagged]
    ahead = [s for s in sigs if "AHEAD_OF_THE_STREET" in s.get("flags", [])]
    warm = [s for s in sigs if s.get("social_z", 0) >= 2]

    banner = ""
    if demo:
        banner = """
  <div class="demo-banner" role="note">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--warning)"
         stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
      <path d="M12 9v4"/><path d="M12 17h.01"/>
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>
    </svg>
    <div>
      <b>Every number on this page is fabricated.</b>
      <p>This is a layout preview built from synthetic data so you can see the shape of the
         output before wiring up live feeds. Nothing here reflects any real company,
         any real social activity, or any real price. Do not act on it.</p>
    </div>
  </div>"""

    strip = f"""
  <div class="strip">
    <div class="stat"><span class="v num">{len(sigs)}</span><span class="l">tracked</span></div>
    <div class="stat hot"><span class="v num">{len(flagged)}</span><span class="l">flagged today</span></div>
    <div class="stat"><span class="v num">{len(ahead)}</span><span class="l">ahead of street</span></div>
    <div class="stat"><span class="v num">{len(warm)}</span><span class="l">chatter &gt; 2σ</span></div>
  </div>"""

    if flagged:
        flag_rows = f'<div class="rows">{"".join(_row(s) for s in flagged)}</div>'
    else:
        flag_rows = ('<div class="empty">Nothing crossed the threshold today. '
                     'That is the normal state — a scanner that flags something every day '
                     'is a scanner that is not filtering.</div>')

    quiet_rows = f'<div class="rows">{"".join(_row(s, show_why=False) for s in quiet)}</div>' if quiet else ""

    legend = """
  <div class="sec-head"><h2>How to read this</h2><span class="rule"></span></div>
  <div class="scroll"><table class="legend">
    <tr><td><span class="chip pos"><span class="dot"></span>Broad spike</span></td>
        <td>Chatter is 3σ+ above this entity's own 45-day baseline <em>and</em> spread across many
            communities and many accounts. Breadth is what separates a real trend from one thread
            going viral.</td></tr>
    <tr><td><span class="chip warn"><span class="dot"></span>One viral thread</span></td>
        <td>Same volume spike, but concentrated in one place from few accounts. Usually noise.</td></tr>
    <tr><td><span class="chip pos"><span class="dot"></span>Ahead of the street</span></td>
        <td>Consumer-community chatter is running well ahead of investing-forum chatter.
            This is the whole thesis — when the finance subs catch up, the edge is gone.</td></tr>
    <tr><td><span class="chip warn"><span class="dot"></span>Finance chatter only</span></td>
        <td>The investing forums are talking but consumers are not. A stock story, not a product story.</td></tr>
    <tr><td><span class="chip pos"><span class="dot"></span>Not priced in</span></td>
        <td>Attention is elevated but price and volume are still flat.</td></tr>
    <tr><td><span class="chip warn"><span class="dot"></span>Already moved</span></td>
        <td>The stock has run more than 15% in five days. You are probably late.</td></tr>
    <tr><td><span class="chip pos"><span class="dot"></span>Selling out</span></td>
        <td>Unusual density of “sold out”, “restock”, “can't find” language. In consumer products
            this leads reported revenue more reliably than sentiment does.</td></tr>
    <tr><td><span class="chip pos"><span class="dot"></span>positive</span>
            <span class="chip bad"><span class="dot"></span>negative</span></td>
        <td>The tone of the chatter, from sentiment plus the balance of buying language
            (“just copped”, “sold out”) against complaint language (“returned it”, “overrated”).
            Reported separately from the score, because a brand melting down is a loud,
            high-attention event that is <em>not</em> the same finding as a brand taking off.
            Negative names are deliberately pushed down the ranking.</td></tr>
    <tr><td><b class="mono">chatter</b></td>
        <td>Robust z-score of mentions as a share of everything sampled that day. Median/MAD, so an
            old spike doesn't poison the baseline.</td></tr>
    <tr><td><b class="mono">breadth</b></td>
        <td>0–1. Distinct communities, distinct authors, and how evenly spread the mentions are.</td></tr>
    <tr><td><b class="mono">lead</b></td>
        <td>Consumer chatter ÷ finance chatter. Above 2.5× means the street hasn't noticed.</td></tr>
  </table></div>"""

    return f"""
<div class="wrap">
{banner}
  <header class="top">
    <div>
      <div class="eyebrow">Social arbitrage scanner</div>
      <h1>Signal Desk</h1>
      <p class="sub">Consumer attention running ahead of the tape. Scores are relative to each
         name's own history — not to each other.</p>
    </div>
    <div class="eyebrow">{_esc(payload.get('date',''))} · sampled daily</div>
  </header>
{strip}
  <div class="sec-head"><h2>Flagged</h2><span class="rule"></span></div>
{flag_rows}
  <div class="sec-head"><h2>Watchlist</h2><span class="rule"></span></div>
{quiet_rows}
{legend}
  <footer>
    <p><b>This is a research tool, not advice.</b> A social spike is a hypothesis about attention,
       not a forecast of revenue or price. Plenty of things trend hard and never sell, and plenty
       of good businesses never trend at all.</p>
    <p>Scores below 21 days of history are unreliable by construction — the baseline isn't formed yet.</p>
    <p class="mono">generated {_esc(payload.get('generated',''))}</p>
  </footer>
</div>"""


def render(payload: dict, path: str, demo: bool = False):
    """Full standalone document — for GitHub Pages."""
    body = build_body(payload, demo)
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signal Desk</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<style>{CSS}</style>
</head><body>{body}</body></html>"""
    with open(path, "w") as f:
        f.write(doc)
    return doc


def render_fragment(payload: dict, path: str, demo: bool = False):
    """Head-less fragment — for publishing via the Artifact tool."""
    body = build_body(payload, demo)
    frag = f"""<title>Signal Desk</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<style>{CSS}</style>
{body}"""
    with open(path, "w") as f:
        f.write(frag)
    return frag

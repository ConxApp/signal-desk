"""
Alert dispatch. Only fires on flags that are worth interrupting you for,
and never fires twice for the same entity+flag within a cooldown window.

Alert fatigue is what kills tools like this. The cooldown is not optional.
Cooldown state is only stamped AFTER a successful delivery (or when no
webhook is configured), so a failed webhook call never swallows an alert.
"""
from __future__ import annotations
import json, os, datetime as dt, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data/alert_state.json")

# Flags that justify a notification. NARROW_SPIKE, LATE, COOLING and
# FINANCE_ONLY are shown on the dashboard but deliberately never alert —
# they are context, not opportunity.
ALERTING = {"BROAD_SPIKE", "AHEAD_OF_THE_STREET", "UNPRICED", "SCARCITY", "NEGATIVE_TURN"}
COOLDOWN_DAYS = 5
MIN_SCORE = 55
MIN_HISTORY_DAYS = 21   # below this the baseline is not trustworthy


def _load():
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)


def _post(url: str, key: str, text: str):
    req = urllib.request.Request(url, data=json.dumps({key: text}).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15)


def _chunks(lines, limit: int = 1900):
    """Discord caps a message at 2000 chars; Slack is more generous but chunking is harmless."""
    buf = []
    for ln in lines:
        if buf and sum(len(x) + 1 for x in buf) + len(ln) + 1 > limit:
            yield "\n".join(buf)
            buf = []
        buf.append(ln)
    if buf:
        yield "\n".join(buf)


def dispatch(payload: dict):
    state = _load()
    today = dt.date.fromisoformat(payload["date"])
    fresh = []

    for s in payload.get("signals", []):
        # social history, not total rows: the Reddit baseline is what alerts rely on
        if s.get("social_days", s.get("history_days", 0)) < MIN_HISTORY_DAYS:
            continue
        if s.get("arb_score", 0) < MIN_SCORE:
            continue
        for flag in s.get("flags", []):
            if flag not in ALERTING:
                continue
            k = f"{s['entity']}:{flag}"
            last = state.get(k)
            if last and (today - dt.date.fromisoformat(last)).days < COOLDOWN_DAYS:
                continue
            fresh.append((s, flag, k))

    if not fresh:
        print("No new alerts.")
        _save(state)
        return

    lines = [f"*{len(fresh)} new signal(s)* — {payload['date']}", ""]
    for s, flag, _ in fresh:
        lines.append(f"*{s.get('name')} ({s.get('ticker')})* — {flag} — score {s.get('arb_score')}")
        for w in (s.get("why") or [])[:3]:
            lines.append(f"  • {w}")
        lines.append("")
    body = "\n".join(lines)
    print(body)

    delivered = True
    url = os.environ.get("SLACK_WEBHOOK_URL") or os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        key = "content" if "discord" in url else "text"
        try:
            for chunk in _chunks(lines):
                _post(url, key, chunk)
            print("Alert delivered.")
        except Exception as e:
            delivered = False
            print(f"! webhook failed: {type(e).__name__}: {e}")

    # Also surface in the GitHub Actions run summary, so there is a record
    # even with no webhook configured.
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Signals\n\n" + body.replace("*", "**") + "\n")

    if delivered:
        for _, _, k in fresh:
            state[k] = today.isoformat()
    _save(state)

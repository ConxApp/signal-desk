"""
Alert dispatch. Only fires on flags that are worth interrupting you for,
and never fires twice for the same entity+flag within a cooldown window.

Alert fatigue is what kills tools like this. The cooldown is not optional.
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
            return json.load(open(STATE))
        except Exception:
            pass
    return {}


def _save(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(s, open(STATE, "w"), indent=1)


def dispatch(payload: dict):
    state = _load()
    today = dt.date.fromisoformat(payload["date"])
    fresh = []

    for s in payload["signals"]:
        if s.get("history_days", 0) < MIN_HISTORY_DAYS:
            continue
        if s["arb_score"] < MIN_SCORE:
            continue
        for flag in s["flags"]:
            if flag not in ALERTING:
                continue
            k = f"{s['entity']}:{flag}"
            last = state.get(k)
            if last and (today - dt.date.fromisoformat(last)).days < COOLDOWN_DAYS:
                continue
            state[k] = today.isoformat()
            fresh.append((s, flag))

    if not fresh:
        print("No new alerts.")
        _save(state)
        return

    lines = [f"*{len(fresh)} new signal(s)* — {payload['date']}", ""]
    for s, flag in fresh:
        lines.append(f"*{s['name']} ({s['ticker']})* — {flag} — score {s['arb_score']}")
        for w in s["why"][:3]:
            lines.append(f"  • {w}")
        lines.append("")
    body = "\n".join(lines)
    print(body)

    url = os.environ.get("SLACK_WEBHOOK_URL") or os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        key = "content" if "discord" in url else "text"
        try:
            req = urllib.request.Request(
                url, data=json.dumps({key: body}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
            print("Alert delivered.")
        except Exception as e:
            print(f"! webhook failed: {type(e).__name__}: {e}")

    # Also surface in the GitHub Actions run summary, so there is a record
    # even with no webhook configured.
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## Signals\n\n" + body.replace("*", "**") + "\n")

    _save(state)

"""Append-only daily history, stored as JSONL so git diffs stay readable."""
from __future__ import annotations
import json, os
from collections import defaultdict

class History:
    def __init__(self, path="data/history.jsonl"):
        self.path = path
        self.rows = []
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self.rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

    def upsert(self, row: dict):
        """One row per (entity, date). Re-running the same day overwrites."""
        for i, r in enumerate(self.rows):
            if r["entity"] == row["entity"] and r["date"] == row["date"]:
                merged = dict(r); merged.update({k: v for k, v in row.items() if v not in (None, [], 0) or k in r})
                self.rows[i] = merged
                return
        self.rows.append(row)

    def patch(self, entity: str, date: str, **fields):
        for r in self.rows:
            if r["entity"] == entity and r["date"] == date:
                r.update(fields); return
        row = {"entity": entity, "date": date}; row.update(fields)
        self.rows.append(row)

    def by_entity(self) -> dict:
        out = defaultdict(list)
        for r in self.rows:
            out[r["entity"]].append(r)
        for k in out:
            out[k].sort(key=lambda r: r["date"])
        return out

    def dates_for(self, entity: str) -> set:
        return {r["date"] for r in self.rows if r["entity"] == entity}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.rows.sort(key=lambda r: (r["date"], r["entity"]))
        with open(self.path, "w") as f:
            for r in self.rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")

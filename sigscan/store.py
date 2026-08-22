"""Append-only daily history, stored as JSONL so git diffs stay readable.

Rows are keyed by (entity, date). An in-memory index keeps upsert/patch O(1);
the Wikipedia backfill alone issues ~40k patches per run against a
~40-65k-row discovery file, which a linear scan turns into minutes of CPU.
"""
from __future__ import annotations
import json, os
from collections import defaultdict


class History:
    def __init__(self, path="data/history.jsonl"):
        self.path = path
        self.rows = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self.rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        self._idx = {(r["entity"], r["date"]): r for r in self.rows if "entity" in r and "date" in r}

    def upsert(self, row: dict):
        """One row per (entity, date). Re-running the same day overwrites.
        Empty/zero values never erase an existing non-empty value unless the
        key already existed on the row (so a re-run can legitimately set 0)."""
        k = (row["entity"], row["date"])
        r = self._idx.get(k)
        if r is None:
            self.rows.append(row)
            self._idx[k] = row
            return
        r.update({kk: v for kk, v in row.items() if v not in (None, [], 0) or kk in r})

    def patch(self, entity: str, date: str, **fields):
        k = (entity, date)
        r = self._idx.get(k)
        if r is None:
            r = {"entity": entity, "date": date}
            self.rows.append(r)
            self._idx[k] = r
        r.update(fields)

    def get(self, entity: str, date: str) -> dict | None:
        return self._idx.get((entity, date))

    def by_entity(self) -> dict:
        out = defaultdict(list)
        for r in self.rows:
            out[r["entity"]].append(r)
        for k in out:
            out[k].sort(key=lambda r: r["date"])
        return out

    def dates_for(self, entity: str) -> set:
        return {r["date"] for r in self.rows if r["entity"] == entity}

    def prune(self, cutoff_date: str):
        """Drop rows older than cutoff_date (ISO string) and rebuild the index."""
        self.rows = [r for r in self.rows if r.get("date", "") >= cutoff_date]
        self._idx = {(r["entity"], r["date"]): r for r in self.rows}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.rows.sort(key=lambda r: (r["date"], r["entity"]))
        with open(self.path, "w", encoding="utf-8") as f:
            for r in self.rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")

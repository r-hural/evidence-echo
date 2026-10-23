"""One-off import of the v0.1 prototype posts_cache.json."""
import json
from pathlib import Path

from evidence_echo.core import store

TIER_MAP = {"ab_test": "A", "correlation_study": "B",
            "published_research": "B", "case_study": "C"}


def import_cache(conn, cache_path: Path) -> dict:
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    stats = {"verified": 0, "rejected": 0, "fetched": 0}
    for post in data.get("posts", []):
        item = {
            "url": post.get("url") or post.get("id"),
            "source_name": post.get("source_name", "legacy"),
            "source_type": post.get("source_type", "rss"),
            "title": post.get("title", ""),
            "excerpt": post.get("summary", ""),
            "published": post.get("published"),
            "fetched_at": post.get("fetched_at", ""),
        }
        if store.insert_items(conn, [item]) == 0:
            continue  # already imported
        rowid = conn.execute("SELECT id FROM items WHERE url=?",
                             (item["url"],)).fetchone()["id"]
        passes = post.get("passes")
        if passes is True:
            tier = TIER_MAP.get(post.get("evidence_type", ""), "D")
            store.set_triage(conn, rowid, True, "legacy import")
            store.set_full_text(conn, rowid, item["excerpt"])
            facts = {"what_was_tested": item["title"], "findings": post.get("reason", "")}
            store.set_verdict(conn, rowid, True, tier, facts, None)
            store.assign_study(conn, rowid, store.create_study(conn, tier, facts, None),
                               "legacy import")
            store.set_skeptic(conn, rowid, True, None, "legacy import (unaudited)")
            stats["verified"] += 1
        elif passes is False:
            store.set_triage(conn, rowid, False, post.get("reason", "legacy reject"))
            conn.execute("UPDATE items SET state='rejected' WHERE id=?", (rowid,))
            conn.execute("UPDATE items SET rejected_at=NULL WHERE id=?", (rowid,))  # an import is no rejection event
            conn.commit()
            stats["rejected"] += 1
        else:
            stats["fetched"] += 1
    return stats

"""Embed stage: advance verified items to embedded. Vectors come from the embed pass (core/similarity)."""
from evidence_echo.core import store


def run(conn) -> dict:
    items = store.items_in_state(conn, "verified")
    for it in items:
        store.set_state(conn, it["id"], "embedded")
    return {"embedded": 0, "skipped": len(items)}

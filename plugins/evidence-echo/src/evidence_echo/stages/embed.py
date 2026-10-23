"""Embed verified items' representative text. No embedder -> advance state, skip vectors."""
from evidence_echo.core import store


def _representative_text(item: dict) -> str:
    return (item["full_text"] or item["excerpt"] or item["title"] or "")[:8000]


def run(conn, embedder) -> dict:
    items = store.items_in_state(conn, "verified")
    if embedder is None:
        for it in items:
            store.set_state(conn, it["id"], "embedded")
        return {"embedded": 0, "skipped": len(items)}
    texts = [_representative_text(it) for it in items]
    if texts:
        vectors = embedder.embed(texts)
        for it, vec in zip(items, vectors):
            store.save_embedding(conn, it["id"], embedder.model_name, vec.tobytes())
    return {"embedded": len(items), "skipped": 0}

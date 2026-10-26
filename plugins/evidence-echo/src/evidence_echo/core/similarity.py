"""Embedding side of study matching: embed study cards, load item and study vectors, find near ones."""
from collections import Counter

import numpy as np

from evidence_echo.core import cards, store
from evidence_echo.core.embedders import EmbeddingError

# Both cut-offs confirmed by the owner-side check, 2026-10-25 (plan ledger, Task 6).
CANDIDATE_SIM = 0.85   # cosine at which a study becomes a match candidate
DUPLICATE_SIM = 0.93   # cosine at which a study is a likely duplicate
MAX_SIMILAR = 3        # candidates per item

# Items that get a vector: judged ones, and ones that belong to a study and are not rejected.
_VECTOR_ITEMS = "(state = 'judged' OR (study_id IS NOT NULL AND state NOT IN ('rejected', 'rejected_triage')))"
STUDY_ITEMS_SQL = "(i.state = 'judged' OR i.state NOT IN ('rejected', 'rejected_triage'))"


def embed_pending(conn, embedder) -> str | None:
    """Embed the cards of items that have no vector for the embedder's tag yet.
    Returns None on success (also when nothing was pending), else the short failure reason; never raises."""
    try:
        rows = conn.execute(
            f"""SELECT * FROM items WHERE {_VECTOR_ITEMS}
                AND id NOT IN (SELECT item_id FROM embeddings WHERE model = ?) ORDER BY id""",
            (embedder.tag,)).fetchall()
        pending = [(it["id"], card) for it in map(dict, rows) if (card := cards.study_card(it))]
        if not pending:
            return None
        texts = [card for _, card in pending]
        batches = getattr(embedder, "batches", None)
        groups = batches(texts) if batches else [(texts, embedder.embed(texts))]
        done = 0
        for chunk, vectors in groups:  # saved per request: a later failure keeps earlier work
            if len(vectors) != len(chunk):
                return "embedder returned the wrong number of vectors"
            for (item_id, _), vec in zip(pending[done:done + len(chunk)], vectors):
                store.save_vector(conn, item_id, embedder.tag,
                                  np.asarray(vec, dtype=np.float32).tobytes())
            done += len(chunk)
        if done != len(pending):
            return "embedder returned the wrong number of vectors"
    except EmbeddingError as e:
        return str(e) or "embedding failed"
    except Exception as e:  # embedding never stops a run
        return f"embedding failed: {type(e).__name__}"
    return None


def _unit(rows) -> dict[int, np.ndarray]:
    vecs = {}
    for row in rows:
        if len(row["vector"]) % 4:   # corrupt blob: not whole float32 values
            continue
        vec = np.frombuffer(row["vector"], dtype=np.float32)
        norm = np.linalg.norm(vec) if vec.size else 0
        if norm > 0:
            vecs[row["item_id"]] = (vec, norm)
    if not vecs:
        return {}
    length = Counter(v.size for v, _ in vecs.values()).most_common(1)[0][0]
    return {item_id: vec / norm for item_id, (vec, norm) in vecs.items() if vec.size == length}


def item_vectors(conn, tag: str) -> dict[int, np.ndarray]:
    """Unit vectors of all items for one embedder tag; vectors of an odd length are dropped."""
    return _unit(conn.execute("SELECT item_id, vector FROM embeddings WHERE model = ?", (tag,)))


def unit_of(total):
    """The unit vector along a sum of unit vectors (the normalised mean), or None."""
    if total is None:
        return None
    norm = np.linalg.norm(total)
    return total / norm if norm > 0 else None


def study_vectors(conn, tag: str) -> dict[int, np.ndarray]:
    """Active study id -> unit mean of its items' vectors."""
    sums = study_sums(conn, tag)
    return {sid: vec for sid, total in sums.items() if (vec := unit_of(total)) is not None}


def study_sums(conn, tag: str, vecs: dict | None = None) -> dict[int, np.ndarray]:
    """Active study id -> sum of its items' unit vectors (its normalised mean is the study vector)."""
    vecs = item_vectors(conn, tag) if vecs is None else vecs
    members: dict[int, list[np.ndarray]] = {}
    for row in conn.execute(
            f"""SELECT i.id, i.study_id FROM items i JOIN studies s ON s.id = i.study_id
                WHERE s.merged_into IS NULL AND {STUDY_ITEMS_SQL}"""):
        if row["id"] in vecs:
            members.setdefault(row["study_id"], []).append(vecs[row["id"]])
    return {study_id: np.sum(group, axis=0) for study_id, group in members.items()}


def similar(vec, pool: list[dict], cutoff: float, limit: int, exclude: set[str]) -> list[dict]:
    """Pool entries ("vec", "cid", "order") with cosine >= cutoff to vec, best first, newer first on ties."""
    scored = []
    for entry in pool:
        if entry["cid"] in exclude:
            continue
        score = float(np.dot(vec, entry["vec"]))
        if score >= cutoff:
            scored.append((score, entry.get("order", 0), entry))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [entry for _, _, entry in scored[:limit]]

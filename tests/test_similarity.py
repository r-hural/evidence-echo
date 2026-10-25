import json
import sqlite3

import numpy as np

from evidence_echo.core import similarity, store
from evidence_echo.core.embedders import EmbeddingError
from tests.conftest import make_item


class FakeEmbedder:
    model_name = "fake"
    tag = "fake/card-v1"

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.error:
            raise EmbeddingError(self.error)
        return [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32) for _ in texts]


def _item(db, n, state="judged", title=None, facts=None, study=None):
    url = f"https://example.com/p{n}"
    store.insert_items(db, [make_item(url=url, title=f"Title {n}" if title is None else title)])
    item_id = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()["id"]
    db.execute("UPDATE items SET state=?, facts=?, study_id=? WHERE id=?",
               (state, json.dumps(facts or {}), study, item_id))
    db.commit()
    return item_id


def _study(db, name="s"):
    cur = db.execute("INSERT INTO studies (canonical_name, created_at) VALUES (?, 'x')", (name,))
    db.commit()
    return cur.lastrowid


def _put(db, item_id, vec, tag="fake/card-v1"):
    store.save_vector(db, item_id, tag, np.array(vec, dtype=np.float32).tobytes())


def _state(db, item_id):
    return db.execute("SELECT state FROM items WHERE id=?", (item_id,)).fetchone()["state"]


def test_embed_pending_embeds_judged_and_backfill_once(db):
    sid = _study(db)
    a = _item(db, 1, "judged")
    b = _item(db, 2, "matched", study=sid)
    c = _item(db, 3, "rejected", study=sid)
    emb = FakeEmbedder()
    assert similarity.embed_pending(db, emb) is None
    assert len(emb.calls) == 1 and len(emb.calls[0]) == 2
    assert {r["item_id"] for r in db.execute("SELECT item_id FROM embeddings")} == {a, b}
    assert (_state(db, a), _state(db, b), _state(db, c)) == ("judged", "matched", "rejected")
    assert similarity.embed_pending(db, emb) is None
    assert len(emb.calls) == 1


def test_embed_pending_reembeds_on_new_tag(db):
    _item(db, 1, "judged")
    emb = FakeEmbedder()
    similarity.embed_pending(db, emb)
    emb.tag = "fake/card-v2"
    similarity.embed_pending(db, emb)
    assert len(emb.calls) == 2
    assert db.execute("SELECT COUNT(*) c FROM embeddings").fetchone()["c"] == 2


def test_embed_pending_skips_items_without_card(db):
    _item(db, 1, "judged", title="")
    emb = FakeEmbedder()
    assert similarity.embed_pending(db, emb) is None
    assert emb.calls == []


def test_embed_pending_failure_returns_reason(db):
    _item(db, 1, "judged")
    assert similarity.embed_pending(db, FakeEmbedder(error="rate limited")) == "rate limited"
    assert db.execute("SELECT COUNT(*) c FROM embeddings").fetchone()["c"] == 0


def test_item_vectors_ignore_other_tags_and_bad_lengths(db):
    a, b, c, d = (_item(db, i) for i in range(4))
    _put(db, a, [3, 0, 0, 4])
    _put(db, b, [0, 2, 0, 0])
    _put(db, c, [1, 2, 3])
    _put(db, d, [1, 0, 0, 0], tag="other/card-v1")
    vecs = similarity.item_vectors(db, "fake/card-v1")
    assert set(vecs) == {a, b}
    assert all(abs(np.linalg.norm(v) - 1) < 1e-6 for v in vecs.values())
    assert similarity.item_vectors(db, "none") == {}


def test_study_vectors_mean_of_items(db):
    sid = _study(db)
    a = _item(db, 1, "matched", study=sid)
    b = _item(db, 2, "matched", study=sid)
    r = _item(db, 3, "rejected", study=sid)
    _put(db, a, [1, 0, 0, 0])
    _put(db, b, [0, 1, 0, 0])
    _put(db, r, [0, 0, 1, 0])
    vec = similarity.study_vectors(db, "fake/card-v1")[sid]
    assert np.allclose(vec, [2 ** -0.5, 2 ** -0.5, 0, 0])


def test_study_vectors_follow_merge(db):
    s1, s2 = _study(db), _study(db)
    a = _item(db, 1, "matched", study=s1)
    b = _item(db, 2, "matched", study=s2)
    db.execute("UPDATE items SET first_study_id=study_id")
    db.commit()
    _put(db, a, [1, 0, 0, 0])
    _put(db, b, [0, 1, 0, 0])
    store.merge_studies(db, s1, s2)
    merged = similarity.study_vectors(db, "fake/card-v1")
    assert set(merged) == {s1}
    assert np.allclose(merged[s1], [2 ** -0.5, 2 ** -0.5, 0, 0])
    store.unmerge_study(db, s2)
    back = similarity.study_vectors(db, "fake/card-v1")
    assert set(back) == {s1, s2}
    assert np.allclose(back[s2], [0, 1, 0, 0])


def test_similar_cutoff_limit_exclude_order():
    def e(cid, vec, order):
        v = np.array(vec, dtype=np.float32)
        return {"cid": cid, "vec": v / np.linalg.norm(v), "order": order}

    pool = [e("a", [1, 0], 1), e("b", [1, 0], 2), e("c", [1, 1], 3), e("d", [0, 1], 4), e("e", [1, 0.1], 5)]
    q = np.array([1, 0], dtype=np.float32)
    top = [p["cid"] for p in similarity.similar(q, pool, 0.9, 10, set())]
    assert top[:2] == ["b", "a"] and top[2] == "e" and len(top) == 3
    assert [p["cid"] for p in similarity.similar(q, pool, 0.9, 2, set())] == ["b", "a"]
    assert [p["cid"] for p in similarity.similar(q, pool, 0.9, 10, {"b"})] == ["a", "e"]
    assert [p["cid"] for p in similarity.similar(q, pool, 0.5, 10, {"a", "b"})] == ["e", "c"]


def test_step14_drops_full_text_vectors(tmp_path):
    path = tmp_path / "echo.db"
    conn = sqlite3.connect(path)
    conn.executescript(store.SCHEMA)
    conn.execute("INSERT INTO items (id, url, source_name, source_type, fetched_at) "
                 "VALUES (1, 'http://x/1', 'src', 'rss', 'x')")
    for model in ("voyage-4-lite", "voyage-4-lite/card-v1"):
        conn.execute("INSERT INTO embeddings (item_id, model, vector) VALUES (1, ?, x'00')", (model,))
    conn.execute("PRAGMA user_version = 13")
    conn.commit()
    conn.close()
    conn = store.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    assert [r["model"] for r in conn.execute("SELECT model FROM embeddings")] == ["voyage-4-lite/card-v1"]
    conn.close()


def test_vectors_skip_blob_not_a_whole_number_of_floats(db):
    a, b = _item(db, 1), _item(db, 2)
    _put(db, a, [1, 0, 0, 0])
    store.save_vector(db, b, "fake/card-v1", b"\x00\x01\x02\x03\x04")
    assert set(similarity.item_vectors(db, "fake/card-v1")) == {a}
    assert similarity.study_vectors(db, "fake/card-v1") == {}


def test_zero_vectors_do_not_win_the_length_vote(db):
    a, b, c = _item(db, 1), _item(db, 2), _item(db, 3)
    _put(db, a, [1, 0, 0, 0])
    _put(db, b, [0, 0, 0])
    _put(db, c, [0, 0, 0])
    assert set(similarity.item_vectors(db, "fake/card-v1")) == {a}


def _unit_pair(cos):
    return np.array([1.0, 0.0], dtype=np.float32), np.array([cos, (1 - cos * cos) ** 0.5], dtype=np.float32)


def test_duplicate_similar_text_rule(db):
    a = store.create_study(db, "A", {}, None)
    b = store.create_study(db, "B", {}, None)
    va, vb = _unit_pair(0.96)
    pairs = store.duplicate_pairs(db, {a: va, b: vb})
    assert [(p["keep"], p["other"], p["rule"]) for p in pairs] == [(a, b, "similar text: 0.96")]
    va, vb = _unit_pair(0.90)
    assert store.duplicate_pairs(db, {a: va, b: vb}) == []
    assert store.duplicate_pairs(db) == []


def test_duplicate_earlier_rule_wins(db):
    a = store.create_study(db, "A", {}, "https://survey.example/x")
    b = store.create_study(db, "B", {}, "https://survey.example/x")
    va, vb = _unit_pair(0.99)
    pairs = store.duplicate_pairs(db, {a: va, b: vb})
    assert [p["rule"] for p in pairs] == ["same primary url"]


def test_duplicate_similar_ranked_last(db):
    a, b, c, d = (store.create_study(db, n, {}, u) for n, u in
                  (("A", None), ("B", None), ("C", "https://s.example/p"), ("D", "https://s.example/p")))
    va, vb = _unit_pair(0.95)
    vectors = {a: va, b: vb, c: np.array([0, 0, 1.0], dtype=np.float32), d: np.array([0, 0, -1.0], dtype=np.float32)}
    vectors = {k: np.pad(v, (0, 3 - len(v))) for k, v in vectors.items()}
    rules = [p["rule"] for p in store.duplicate_pairs(db, vectors)]
    assert rules == ["same primary url", "similar text: 0.95"]


def test_duplicate_similar_ties_higher_similarity_first(db):
    a, b, c = (store.create_study(db, n, {}, None) for n in "ABC")
    _, v2 = _unit_pair(0.94)
    v3 = np.array([0.97, (1 - 0.97 ** 2) ** 0.5], dtype=np.float32)
    pairs = store.duplicate_pairs(db, {a: np.array([1.0, 0.0], dtype=np.float32), b: v2, c: v3})
    assert [(p["keep"], p["other"]) for p in pairs] == [(b, c), (a, c), (a, b)]


def _two_studies_with_embedder(cfg, monkeypatch, emb):
    from evidence_echo.core import embedders
    monkeypatch.setattr(embedders, "build", lambda c: emb)
    conn = store.connect(cfg.db_path)
    s1, s2 = _study(conn, "one"), _study(conn, "two")
    a = _item(conn, 1, "matched", study=s1)
    b = _item(conn, 2, "matched", study=s1)
    c = _item(conn, 3, "matched", study=s2)
    conn.close()
    return a, b, c


def test_studies_similarity_skipped_without_embedder(workspace, monkeypatch):
    from evidence_echo import api
    from evidence_echo.core import config
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert api.studies_similarity(config.load()) == {"skipped": "no embedder"}


def test_studies_similarity_shape(workspace, monkeypatch):
    from evidence_echo import api
    from evidence_echo.core import config
    cfg = config.load()
    _two_studies_with_embedder(cfg, monkeypatch, FakeEmbedder())
    out = api.studies_similarity(cfg)
    assert set(out) == {"known_pairs", "cutoffs", "top_pairs"}
    assert len(out["cutoffs"]) == 6 and out["cutoffs"][0]["cutoff"] == 0.70
    assert all(r["known_total"] == 2 and r["items_total"] == 3 for r in out["cutoffs"])
    assert len(out["known_pairs"]) == 2 and out["known_pairs"][0]["sim"] == 1.0
    assert out["top_pairs"][0]["a_name"] == "one" and out["top_pairs"][0]["sim"] == 1.0


def test_studies_similarity_reports_embedding_failure(workspace, monkeypatch):
    from evidence_echo import api
    from evidence_echo.core import config
    cfg = config.load()
    _two_studies_with_embedder(cfg, monkeypatch, FakeEmbedder(error="rate limited"))
    out = api.studies_similarity(cfg)
    assert out["embeddings"] == "unavailable: rate limited"
    assert out["cutoffs"][0]["known_total"] == 0


def test_studies_duplicates_uses_stored_vectors(workspace, monkeypatch):
    from evidence_echo import api
    from evidence_echo.core import config
    cfg = config.load()
    emb = FakeEmbedder()
    _two_studies_with_embedder(cfg, monkeypatch, emb)
    assert api.studies_duplicates(cfg, count_only=True) == {"count": 0}   # nothing stored yet, no embed pass
    assert emb.calls == []
    api.studies_similarity(cfg)                                            # stores the vectors
    assert api.studies_duplicates(cfg, count_only=True) == {"count": 1}


def test_studies_similarity_known_found_uses_unrounded_cosine(workspace, monkeypatch):
    from evidence_echo import api
    from evidence_echo.core import config
    cfg = config.load()
    a, b, _ = _two_studies_with_embedder(cfg, monkeypatch, FakeEmbedder())
    conn = store.connect(cfg.db_path)
    va, vb = _unit_pair(0.7496)
    _put(conn, a, va)
    _put(conn, b, vb)
    conn.close()
    out = api.studies_similarity(cfg)
    assert out["known_pairs"][0]["sim"] == 0.75
    row = next(r for r in out["cutoffs"] if r["cutoff"] == 0.75)
    assert row["known_found"] == 0


# --- final review fixes ---

def _vec_rows(db, item_id):
    return db.execute("SELECT 1 FROM embeddings WHERE item_id=?", (item_id,)).fetchall()


def test_rejudged_item_loses_its_vector_and_is_embedded_again(db):
    a = _item(db, 1, "judged")
    emb = FakeEmbedder()
    similarity.embed_pending(db, emb)
    assert _vec_rows(db, a)
    store.set_verdict(db, a, True, "B", {"what_was_tested": "new"}, None)
    assert not _vec_rows(db, a)
    similarity.embed_pending(db, emb)
    assert _vec_rows(db, a) and len(emb.calls) == 2
    store.set_verdict(db, a, False, None, {}, None)
    assert not _vec_rows(db, a)


def test_duplicate_similar_text_skips_edition_clash(db):
    a = store.create_study(db, "A", {"editions": ["2024"]}, None)
    b = store.create_study(db, "B", {"editions": ["2025"]}, None)
    va, vb = _unit_pair(0.97)
    assert store.duplicate_pairs(db, {a: va, b: vb}) == []
    c = store.create_study(db, "C", {}, None)
    assert [p["other"] for p in store.duplicate_pairs(db, {a: va, c: vb})] == [c]


def test_embedding_error_never_contains_the_key(monkeypatch):
    import httpx
    import pytest
    from evidence_echo.core import embedders
    from tests.test_embed import _Resp, _patch_post
    monkeypatch.setattr(embedders.time, "sleep", lambda s: None)

    def boom(n, body):
        raise httpx.ConnectError("down sekret")

    for responder in (lambda n, b: _Resp(401), lambda n, b: _Resp(503), boom):
        _patch_post(monkeypatch, responder)
        with pytest.raises(EmbeddingError) as e:
            embedders.VoyageEmbedder("sekret").embed(["a"])
        assert "sekret" not in str(e.value)

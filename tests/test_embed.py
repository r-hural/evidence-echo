import numpy as np

from evidence_echo.core import store, embedders
from evidence_echo.stages import embed
from tests.conftest import make_item


class FakeEmbedder:
    model_name = "fake-embed-v1"

    def embed(self, texts):
        return [np.full(4, float(len(t)), dtype=np.float32) for t in texts]


def _seed_verified(db):
    store.insert_items(db, [make_item()])
    item = store.items_in_state(db, "fetched")[0]
    store.set_triage(db, item["id"], True, "ok")
    store.set_full_text(db, item["id"], "text")
    store.set_verdict(db, item["id"], True, "B", {}, None)
    store.set_skeptic(db, item["id"], True, None, "ok")
    return item["id"]


def test_embed_stores_vector_tagged_with_model(db):
    item_id = _seed_verified(db)
    embed.run(db, FakeEmbedder())
    row = db.execute("SELECT * FROM embeddings WHERE item_id=?", (item_id,)).fetchone()
    assert row["model"] == "fake-embed-v1"
    vec = np.frombuffer(row["vector"], dtype=np.float32)
    assert vec.shape == (4,)
    assert store.items_in_state(db, "embedded")


def test_embed_none_embedder_skips_but_advances(db):
    _seed_verified(db)
    embed.run(db, None)
    assert store.items_in_state(db, "embedded")
    assert db.execute("SELECT COUNT(*) c FROM embeddings").fetchone()["c"] == 0


def test_voyage_embedder_parses_response(monkeypatch):
    import httpx

    def fake_post(url, headers=None, json=None, timeout=None):
        class R:
            def raise_for_status(self): pass
            def json(self):
                return {"data": [{"embedding": [0.1, 0.2]} for _ in json["input"]]}
        return R()

    monkeypatch.setattr(httpx, "post", fake_post)
    e = embedders.VoyageEmbedder(api_key="k")
    vecs = e.embed(["a", "b"])
    assert len(vecs) == 2 and vecs[0].dtype == np.float32

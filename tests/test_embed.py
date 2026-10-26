import numpy as np

from evidence_echo.core import store, embedders
from evidence_echo.stages import embed
from tests.conftest import make_item


def _seed_verified(db):
    store.insert_items(db, [make_item()])
    item = store.items_in_state(db, "fetched")[0]
    store.set_triage(db, item["id"], True, "ok")
    store.set_full_text(db, item["id"], "text")
    store.set_verdict(db, item["id"], True, "B", {}, None)
    store.set_skeptic(db, item["id"], True, None, "ok")
    return item["id"]


def test_embed_stage_only_advances_state(db):
    item_id = _seed_verified(db)
    assert embed.run(db) == {"embedded": 0, "skipped": 1}
    assert store.items_in_state(db, "embedded")[0]["id"] == item_id
    assert db.execute("SELECT COUNT(*) c FROM embeddings").fetchone()["c"] == 0


def test_embed_stage_with_nothing_verified(db):
    assert embed.run(db) == {"embedded": 0, "skipped": 0}


class _Resp:
    def __init__(self, status=200, n=0, payload=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload if payload is not None else {
            "data": [{"embedding": [0.1, 0.2]} for _ in range(n)]}

    def json(self):
        return self._payload


def _patch_post(monkeypatch, responder):
    import httpx
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return responder(len(calls), json)

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_voyage_embedder_parses_response(monkeypatch):
    _patch_post(monkeypatch, lambda n, body: _Resp(200, len(body["input"])))
    e = embedders.VoyageEmbedder(api_key="k")
    vecs = e.embed(["a", "b"])
    assert len(vecs) == 2 and vecs[0].dtype == np.float32


def test_voyage_sends_document_type_in_chunks(monkeypatch):
    def respond(n, body):
        return _Resp(200, payload={"data": [{"embedding": [float(t)]} for t in body["input"]]})

    calls = _patch_post(monkeypatch, respond)
    texts = [str(i) for i in range(70)]
    vecs = embedders.VoyageEmbedder("k").embed(texts)
    assert [len(c["input"]) for c in calls] == [64, 6]
    assert all(c["input_type"] == "document" and c["model"] == "voyage-4-lite" for c in calls)
    assert [float(v[0]) for v in vecs] == [float(i) for i in range(70)]
    assert all(v.dtype == np.float32 for v in vecs)


def test_voyage_retries_once_on_429(monkeypatch):
    sleeps = []
    monkeypatch.setattr(embedders.time, "sleep", lambda s: sleeps.append(s))
    _patch_post(monkeypatch, lambda n, body: _Resp(429) if n == 1 else _Resp(200, len(body["input"])))
    assert len(embedders.VoyageEmbedder("k").embed(["a"])) == 1
    assert sleeps == [60]


def test_voyage_chunks_respect_the_token_budget(monkeypatch):
    def respond(n, body):
        return _Resp(200, len(body["input"]))

    calls = _patch_post(monkeypatch, respond)
    texts = ["x" * 8000 for _ in range(10)]  # ~2000 tokens each, budget 6000
    assert len(embedders.VoyageEmbedder("k").embed(texts)) == 10
    assert [len(c["input"]) for c in calls] == [2, 2, 2, 2, 2]
    calls.clear()
    embedders.VoyageEmbedder("k").embed(["a", "x" * 60000, "b"])
    assert [len(c["input"]) for c in calls] == [1, 1, 1]


def test_voyage_429_waits_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr(embedders.time, "sleep", lambda s: sleeps.append(s))
    _patch_post(monkeypatch, lambda n, body: _Resp(429, headers={"Retry-After": "7"})
                if n == 1 else _Resp(200, len(body["input"])))
    assert len(embedders.VoyageEmbedder("k").embed(["a"])) == 1
    assert sleeps == [7.0]


def test_voyage_429_wait_is_capped(monkeypatch):
    sleeps = []
    monkeypatch.setattr(embedders.time, "sleep", lambda s: sleeps.append(s))
    _patch_post(monkeypatch, lambda n, body: _Resp(429, headers={"Retry-After": "3600"})
                if n == 1 else _Resp(200, len(body["input"])))
    embedders.VoyageEmbedder("k").embed(["a"])
    assert sleeps == [embedders.MAX_WAIT]


def test_voyage_batches_yield_each_request(monkeypatch):
    _patch_post(monkeypatch, lambda n, body: _Resp(200, len(body["input"])))
    texts = ["x" * 8000 for _ in range(3)]
    got = [(chunk, len(vecs)) for chunk, vecs in embedders.VoyageEmbedder("k").batches(texts)]
    assert got == [(texts[:2], 2), (texts[2:], 1)]


def test_voyage_gives_up_after_repeated_429(monkeypatch):
    import pytest
    sleeps = []
    monkeypatch.setattr(embedders.time, "sleep", lambda s: sleeps.append(s))
    calls = _patch_post(monkeypatch, lambda n, body: _Resp(429))
    with pytest.raises(embedders.EmbeddingError) as err:
        embedders.VoyageEmbedder("secret-key").embed(["a"])
    assert "secret-key" not in str(err.value)
    assert len(calls) == 4 and sleeps == [60, 60, 60]


def test_voyage_500_retries_once_after_two_seconds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(embedders.time, "sleep", lambda s: sleeps.append(s))
    _patch_post(monkeypatch, lambda n, body: _Resp(500) if n == 1 else _Resp(200, len(body["input"])))
    assert len(embedders.VoyageEmbedder("k").embed(["a"])) == 1
    assert sleeps == [2]


def test_voyage_gives_up_after_second_failure(monkeypatch):
    import httpx
    import pytest
    monkeypatch.setattr(embedders.time, "sleep", lambda s: None)
    calls = _patch_post(monkeypatch, lambda n, body: _Resp(503))
    with pytest.raises(embedders.EmbeddingError):
        embedders.VoyageEmbedder("k").embed(["a"])
    assert len(calls) == 2

    calls = _patch_post(monkeypatch, lambda n, body: _Resp(401))
    with pytest.raises(embedders.EmbeddingError):
        embedders.VoyageEmbedder("k").embed(["a"])
    assert len(calls) == 1

    def boom(n, body):
        raise httpx.ConnectError("down")

    _patch_post(monkeypatch, boom)
    with pytest.raises(embedders.EmbeddingError):
        embedders.VoyageEmbedder("k").embed(["a"])


def test_voyage_short_response_raises(monkeypatch):
    import pytest
    _patch_post(monkeypatch, lambda n, body: _Resp(200, 2))
    with pytest.raises(embedders.EmbeddingError):
        embedders.VoyageEmbedder("k").embed(["a", "b", "c"])


def test_voyage_tag():
    assert embedders.VoyageEmbedder("k").tag == "voyage-4-lite/card-v1"

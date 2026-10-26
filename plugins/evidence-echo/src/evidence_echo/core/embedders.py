"""Embedder implementations. Protocol: .model_name, .tag, .embed(list[str]) -> list[np.ndarray];
optional .batches(list[str]) yielding (texts, vectors) per request, so callers can keep each
request's vectors before a later request fails."""
import time

import numpy as np
import httpx

from . import cards

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
CHUNK = 64  # texts per request
TOKEN_BUDGET = 6000  # estimated tokens per request; free Voyage accounts allow 10K per minute,
#                     and text with fewer characters per token (German) runs ~30% over the estimate
MAX_RETRIES = 3  # waits after a 429 before giving up
RATE_LIMIT_WAIT = 60  # seconds, when a 429 has no usable Retry-After
MAX_WAIT = 120  # seconds; a longer Retry-After is cut to this


def _tokens(text: str) -> int:
    return len(text) // 4 + 1  # rough estimate, no tokenizer dependency


def _chunks(texts: list[str]):
    """Requests of at most CHUNK texts and TOKEN_BUDGET estimated tokens; a larger text goes alone."""
    chunk: list[str] = []
    used = 0
    for text in texts:
        n = _tokens(text)
        if chunk and (len(chunk) >= CHUNK or used + n > TOKEN_BUDGET):
            yield chunk
            chunk, used = [], 0
        chunk.append(text)
        used += n
    if chunk:
        yield chunk


def _retry_after(resp) -> float:
    try:
        wait = float(getattr(resp, "headers", {}).get("Retry-After"))
    except (TypeError, ValueError):
        return RATE_LIMIT_WAIT
    return min(wait, MAX_WAIT) if wait >= 0 else RATE_LIMIT_WAIT


class EmbeddingError(Exception):
    """Any embedding failure; the message never contains the API key."""


class VoyageEmbedder:
    def __init__(self, api_key: str, model: str = "voyage-4-lite"):
        if not api_key:
            raise ValueError("VOYAGE_API_KEY required for voyage embedder")
        self._key = api_key
        self.model_name = model
        self.tag = f"{model}/{cards.CARD_VERSION}"  # stored with each vector

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for _, vecs in self.batches(texts):
            out.extend(vecs)
        return out

    def batches(self, texts: list[str]):
        for chunk in _chunks(texts):
            yield chunk, self._embed_chunk(chunk)

    def _post(self, chunk: list[str]):
        try:
            return httpx.post(
                VOYAGE_URL,
                headers={"Authorization": f"Bearer {self._key}"},
                json={"input": chunk, "model": self.model_name, "input_type": "document"},
                timeout=60)
        except httpx.HTTPError as e:
            raise EmbeddingError(f"voyage request failed: {type(e).__name__}") from None

    def _embed_chunk(self, chunk: list[str]) -> list[np.ndarray]:
        resp = self._post(chunk)
        waits = 0
        while resp.status_code == 429 and waits < MAX_RETRIES:
            time.sleep(_retry_after(resp))
            waits += 1
            resp = self._post(chunk)
        if resp.status_code >= 500:
            time.sleep(2)  # one retry for server trouble
            resp = self._post(chunk)
        if not 200 <= resp.status_code < 300:
            raise EmbeddingError(f"voyage returned HTTP {resp.status_code}")
        try:
            vecs = [np.asarray(d["embedding"], dtype=np.float32) for d in resp.json()["data"]]
        except (ValueError, KeyError, TypeError):
            raise EmbeddingError("voyage response had an unexpected shape") from None
        if len(vecs) != len(chunk):
            raise EmbeddingError(f"voyage returned {len(vecs)} embeddings for {len(chunk)} texts")
        return vecs


def build(cfg):
    """Factory from config; returns None when embedding is disabled/unavailable."""
    if cfg.embedder == "voyage" and cfg.voyage_api_key:
        return VoyageEmbedder(cfg.voyage_api_key)
    return None  # 'none', or voyage without key: memory features off

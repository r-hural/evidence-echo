"""Embedder implementations. Protocol: .model_name, .tag, .embed(list[str]) -> list[np.ndarray]."""
import time

import numpy as np
import httpx

from . import cards

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
CHUNK = 64  # texts per request


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
        for i in range(0, len(texts), CHUNK):
            out.extend(self._embed_chunk(texts[i:i + CHUNK]))
        return out

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
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2)  # one retry for rate limit / server trouble
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

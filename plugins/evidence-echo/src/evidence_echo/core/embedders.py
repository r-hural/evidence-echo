"""Embedder implementations. Protocol: .model_name, .embed(list[str]) -> list[np.ndarray]."""
import numpy as np
import httpx

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


class VoyageEmbedder:
    def __init__(self, api_key: str, model: str = "voyage-4-lite"):
        if not api_key:
            raise ValueError("VOYAGE_API_KEY required for voyage embedder")
        self._key = api_key
        self.model_name = model

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        resp = httpx.post(
            VOYAGE_URL,
            headers={"Authorization": f"Bearer {self._key}"},
            json={"input": texts, "model": self.model_name},
            timeout=60)
        resp.raise_for_status()
        return [np.asarray(d["embedding"], dtype=np.float32)
                for d in resp.json()["data"]]


def build(cfg):
    """Factory from config; returns None when embedding is disabled/unavailable."""
    if cfg.embedder == "voyage" and cfg.voyage_api_key:
        return VoyageEmbedder(cfg.voyage_api_key)
    return None  # 'none', or voyage without key: memory features off

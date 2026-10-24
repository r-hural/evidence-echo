"""Shared on-disk fetch cache: one file per key, fresh for `minutes`, shared by every topic.

Only successful answers are stored (the caller decides). Freshness comes from `saved_at` in
the file, not from the file's mtime. Any read or write problem is a miss / ignored: the cache
must never make a fetch fail.
"""
import base64
import hashlib
import json
import os
import time
from pathlib import Path

from . import base


class FetchCache:
    def __init__(self, root: Path, minutes: int, read: bool = True, clock=time.time):
        self.root, self.minutes, self.read, self.clock = Path(root), minutes, read, clock

    def _path(self, key: str) -> Path:
        return self.root / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    def _fresh(self, saved_at) -> bool:
        return (isinstance(saved_at, (int, float)) and not isinstance(saved_at, bool)
                and 0 <= self.clock() - saved_at <= self.minutes * 60)

    def _load(self, key: str) -> dict | None:
        try:
            data = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("key") != key or not self._fresh(data.get("saved_at")):
            return None
        return data

    def _store(self, key: str, payload: dict) -> None:
        path = self._path(key)
        tmp = path.with_name(path.name + ".tmp")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"key": key, "saved_at": self.clock(), **payload}),
                           encoding="utf-8")
            os.replace(tmp, path)
        except (OSError, ValueError):
            try:
                tmp.unlink()
            except OSError:
                pass

    def get(self, url: str) -> "base.Fetched | None":
        if not self.read:
            return None
        data = self._load("url:" + url)
        if data is None:
            return None
        try:
            return base.Fetched(data["final_url"], int(data["status"]), data["text"],
                                base64.b64decode(data["raw"]))
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, url: str, fetched: "base.Fetched") -> None:
        self._store("url:" + url, {"url": url, "final_url": fetched.url, "status": fetched.status,
                                   "text": fetched.text,
                                   "raw": base64.b64encode(fetched.content).decode("ascii")})

    def get_json(self, key: str):
        if not self.read:
            return None
        data = self._load("json:" + key)
        return None if data is None else data.get("value")

    def put_json(self, key: str, value) -> None:
        self._store("json:" + key, {"value": value})

    def prune(self) -> int:
        """Delete expired (and unreadable) files; returns how many went."""
        removed = 0
        try:
            files = list(self.root.glob("*.json"))
        except OSError:
            return 0
        for path in files:
            try:
                saved_at = json.loads(path.read_text(encoding="utf-8")).get("saved_at")
            except (OSError, ValueError, AttributeError):
                saved_at = None
            if not self._fresh(saved_at):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed


    def clear(self) -> int:
        """Delete every saved copy; returns how many went."""
        removed = 0
        try:
            files = list(self.root.glob("*.json"))
        except OSError:
            return 0
        for path in files:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed


def for_config(cfg, fresh: bool = False) -> "FetchCache | None":
    """The cache for a run, or None when fetch_cache_minutes is 0 (copies saved while it was on
    are deleted then). `fresh` skips reading (every URL is downloaded again) but still refreshes
    what is stored."""
    if cfg.fetch_cache_minutes == 0:
        FetchCache(cfg.data_dir / "cache" / "fetch", 0).clear()
        return None
    cache = FetchCache(cfg.data_dir / "cache" / "fetch", cfg.fetch_cache_minutes, read=not fresh)
    cache.prune()
    return cache

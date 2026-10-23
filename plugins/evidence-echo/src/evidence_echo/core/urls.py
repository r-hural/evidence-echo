"""Canonical URLs: the dedup key for items (tracking parameters, scheme and fragment ignored)."""
from urllib.parse import urlsplit, urlunsplit

import courlan


def canonical_url(url: str) -> str | None:
    """https://host/path?query without tracking parameters and fragment; None for non-http(s)."""
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    try:
        normalized = urlsplit(courlan.normalize_url(raw, strict=False))
    except (ValueError, TypeError):
        return None
    return urlunsplit(("https", normalized.netloc.lower(), normalized.path or "/",
                       normalized.query, ""))

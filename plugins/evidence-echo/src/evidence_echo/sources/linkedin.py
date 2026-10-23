"""linkedin kind: posts of LinkedIn profiles via an Apify actor (needs a token; costs money)."""
import time

import httpx
from dateutil import parser as dateutil_parser
from datetime import timezone

from evidence_echo.sources import base

TYPE = "linkedin"
FIELDS = {"name": base.required_text("name"), "slug": base.required_text("slug")}
HAS_HEADLINES = False
APIFY_POLL_INTERVAL_S = 5
APIFY_TIMEOUT_S = 300
NO_TOKEN = "Apify token missing (plugin settings)"   # fits the report's 40-char warning
NO_POSTS = "no posts returned; check the slug"   # Apify finishes fine on "Profile not found"
MAX_POSTS = 30   # posts asked of a normal run (a full result may hide older ones)


def _parse_iso(s: str):
    if not s:
        return None
    try:
        dt = dateutil_parser.parse(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _redact(message: str, secret: str) -> str:
    """Mask a secret wherever it appears in an error message before storing/printing."""
    return message.replace(secret, "***") if secret else message


def check(source, ctx=None) -> dict:
    return {"name": source["name"], "type": TYPE,
            "url": f"https://www.linkedin.com/in/{source.get('slug', '')}/", "status": "skipped",
            "http_status": None, "entries": 0, "latest_entry_date": None,
            "detail": "LinkedIn is fetched via Apify; not probed"}


def normalize_record(record: dict, slug_to_name: dict):
    """(source name, Entry) for one Apify post record, or None. The horizon is the caller's."""
    pub = _parse_iso((record.get("postedAt") or {}).get("date", ""))
    text = record.get("content", "")
    url = record.get("linkedinUrl", "")
    if not pub or not url:
        return None
    author = record.get("author") or {}
    name = slug_to_name.get(author.get("publicIdentifier", ""),
                            author.get("name", "LinkedIn Author"))
    return name, base.Entry(url=url, title=text[:120].strip(), excerpt=text[:800].strip(),
                            published=pub, text=text)


def _answered_slugs(raw: list) -> set:
    """Slugs (lowercase) of the profiles the run returned any record for, reposts included."""
    out = set()
    for record in raw:
        target = (record.get("query") or {}).get("targetUrl", "")
        if "/in/" in target:
            out.add(target.split("/in/", 1)[1].strip("/").lower())
        out.add(((record.get("author") or {}).get("publicIdentifier") or "").lower())
    return out


def list_new(sources: list[dict], ctx: base.FetchContext) -> dict:
    if not ctx.apify_token:
        return {s["name"]: base.SourceResult(error=NO_TOKEN) for s in sources}
    out = {s["name"]: base.SourceResult() for s in sources}
    if not sources:
        return out
    slug_to_name = {s["slug"]: s["name"] for s in sources}
    profile_urls = [f"https://www.linkedin.com/in/{s['slug']}/" for s in sources]
    headers = {"Authorization": f"Bearer {ctx.apify_token}"}
    try:
        if ctx.backfill:
            body = {"targetUrls": profile_urls,
                    "maxPosts": max(ctx.backfill.per_source.values() or [MAX_POSTS])}
        else:
            body = {"targetUrls": profile_urls, "maxPosts": MAX_POSTS, "postedLimit": "week"}
        run_resp = httpx.post(
            f"https://api.apify.com/v2/acts/{ctx.apify_actor}/runs",
            headers=headers, json=body, timeout=30)
        run_resp.raise_for_status()
        run_id = run_resp.json()["data"]["id"]
        status = "RUNNING"
        for _ in range(APIFY_TIMEOUT_S // APIFY_POLL_INTERVAL_S):
            try:
                status = httpx.get(
                    f"https://api.apify.com/v2/actor-runs/{run_id}",
                    headers=headers, timeout=10,
                ).json()["data"]["status"]
            except Exception:
                pass
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
            time.sleep(APIFY_POLL_INTERVAL_S)
        if status != "SUCCEEDED":
            raise RuntimeError(f"Apify run status {status}")
        raw = httpx.get(
            f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items",
            headers=headers, timeout=30).json()
    except Exception as exc:
        error = _redact(str(exc), ctx.apify_token)
        return {s["name"]: base.SourceResult(error=error) for s in sources}
    for record in raw:
        got = normalize_record(record, slug_to_name)
        if got and got[0] in out:
            out[got[0]].entries.append(got[1])
    answered = _answered_slugs(raw)
    for s in sources:
        if s["slug"].lower() not in answered and not out[s["name"]].entries:
            out[s["name"]].error = NO_POSTS
    return out


def full_text(url, ctx) -> base.FullText:
    return base.FullText("", "error")  # never called: LinkedIn entries carry their text

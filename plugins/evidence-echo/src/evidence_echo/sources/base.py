"""Shared pieces of every source kind: data shapes, polite HTTP, junk check, dates.

A kind module provides: TYPE, FIELDS (key -> checker returning an error suffix or None),
HAS_HEADLINES, check(source, ctx) -> dict, list_new(sources, ctx) -> {name: SourceResult},
full_text(url, ctx) -> FullText.
Optional: CAN_PAGE (default False): the kind reads further index pages to fill gaps itself.
"""
import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Callable

import htmldate
import httpcore
import httpx
import trafilatura

if TYPE_CHECKING:
    from .cache import FetchCache

try:
    _VERSION = version("evidence-echo")
except PackageNotFoundError:
    _VERSION = "dev"
USER_AGENT = f"EvidenceEcho/{_VERSION} (+https://github.com/r-hural/evidence-echo)"
MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 30000
JUNK_MIN_CHARS = 500
JUNK_MARKER_MAX_CHARS = 3000
TIMEOUT_S = 20
CHALLENGE_MARKERS = ("just a moment", "enable javascript and cookies", "cf-chl",
                     "checking your browser", "access denied", "subscribe to continue",
                     "attention required")


class Blocked(Exception):
    """The site refused us (403/401/451, challenge page, or a rate limit wait over the cap)."""


class FetchError(Exception):
    """Any other failure (network, HTTP error, oversized response)."""


@dataclass
class Entry:
    url: str
    title: str = ""
    excerpt: str = ""
    published: datetime | None = None
    date_unknown: bool = False
    text: str | None = None  # set when the listing already carries the full text


@dataclass
class SourceResult:
    entries: list = field(default_factory=list)
    error: str | None = None
    blocked: bool = False
    cursor: dict | None = None
    possible_gap: bool = False


@dataclass
class FullText:
    text: str
    status: str  # ok | blocked | short | error


@dataclass
class Fetched:
    url: str
    status: int
    text: str
    content: bytes = b""  # the raw body, for parsers that read the encoding themselves (feeds)


class RateLimiter:
    """At most one request per `interval` seconds per host; penalize() honours Retry-After."""

    def __init__(self, interval=1.0, max_wait=60.0, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.max_wait, self.clock, self.sleep = interval, max_wait, clock, sleep
        self._next: dict[str, float] = {}

    def wait(self, host: str) -> bool:
        now = self.clock()
        ready = self._next.get(host, now)
        delay = max(0.0, ready - now)
        if delay > self.max_wait:
            return False
        if delay:
            self.sleep(delay)
        self._next[host] = max(now, ready) + self.interval
        return True

    def penalize(self, host: str, seconds: float) -> None:
        self._next[host] = max(self._next.get(host, 0.0), self.clock() + seconds)


@dataclass
class Backfill:
    days: int
    max_items: int
    per_source: dict = field(default_factory=dict)  # name -> share of max_items


@dataclass
class FetchContext:
    cutoff: datetime
    client: httpx.Client
    limiter: RateLimiter
    apify_token: str = ""
    apify_actor: str = ""
    known: Callable[[str], bool] = lambda canonical: False
    cursors: dict = field(default_factory=dict)  # source name -> cursor dict
    backfill: Backfill | None = None
    first_run: set = field(default_factory=set)  # source names on their first run
    cache: "FetchCache | None" = None  # sources/cache.py; shared by every topic


def make_client() -> httpx.Client:
    """The client for every fetch of a URL from feeds, pages or articles: connections go only
    to checked public addresses (PublicOnlyBackend). An explicit transport also means no
    proxy from the environment: a proxy would be the address checked, not the site."""
    transport = httpx.HTTPTransport()
    # httpx has no network_backend option; the pool's attribute is pinned by a test
    transport._pool._network_backend = PublicOnlyBackend()
    return httpx.Client(transport=transport, headers={"User-Agent": USER_AGENT},
                        timeout=TIMEOUT_S, follow_redirects=True)


def _retry_after(value: str | None) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 60.0


CHALLENGE_PAGE_MARKERS = ("just a moment", "enable javascript and cookies", "cf-chl",
                          "checking your browser", "attention required")


def is_challenge(html: str) -> bool:
    """A bot-check page (Cloudflare and similar), even when served with HTTP 200."""
    head = html[:20000].lower()
    return len(html) < 100_000 and any(m in head for m in CHALLENGE_PAGE_MARKERS)


MAX_REDIRECTS = 5
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")


def real_resolve_host(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise FetchError(f"cannot resolve {host}: {exc}") from None
    return [info[4][0] for info in infos]


resolve_host = real_resolve_host  # tests swap this for an offline table


def _not_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True  # an address we cannot read is not one we connect to
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def public_addresses(host: str) -> list[str]:
    """The host's addresses, refused (FetchError) unless every one is public.

    Literal addresses and local names are refused outright; numeric IPv4 shorthand (127.1,
    2130706433, 0x7f000001, octal) is refused because resolvers read it as an address. A name
    is resolved and refused if any of its addresses is not public."""
    host = (host or "").lower().strip("[]").rstrip(".")
    if not host:
        raise FetchError("refused: no host")
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise FetchError(f"refused: {host} is not a public address")
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        if all(_NUMERIC_LABEL.match(label) for label in host.split(".")):
            raise FetchError(f"refused: {host} is a numeric address in a non-standard form") from None
        addresses = resolve_host(host)
    if not addresses or any(_not_public(a) for a in addresses):
        raise FetchError(f"refused: {host} is not a public address")
    return list(dict.fromkeys(addresses))


def check_public_host(url: str) -> str:
    """Refuse a URL whose host is not public; return the host as httpx connects to it (the
    ASCII, IDNA2008 form: the Unicode form can resolve to a different name). The connection
    itself is pinned to checked addresses by PublicOnlyBackend (no DNS rebinding)."""
    try:
        host = httpx.URL(url).raw_host.decode("ascii").lower().strip("[]").rstrip(".")
    except (httpx.InvalidURL, ValueError):  # IDNAError is a ValueError (UnicodeError)
        raise FetchError("refused: invalid URL") from None
    public_addresses(host)
    return host


class PublicOnlyBackend(httpcore.SyncBackend):
    """Resolves the host once, refuses it unless every address is public, and connects to one
    of exactly those addresses. TLS still verifies and sends SNI for the host name (httpcore
    does that after connect_tcp), so only the DNS lookup moves here."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            addresses = public_addresses(host)
        except FetchError as exc:
            raise httpcore.ConnectError(str(exc)) from None
        error = None
        for address in addresses:
            try:
                return super().connect_tcp(address, port, timeout=timeout,
                                           local_address=local_address,
                                           socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                error = exc
        raise error


def http_get(url: str, ctx: FetchContext) -> Fetched:
    if ctx.cache is not None:
        hit = ctx.cache.get(url)  # a hit makes no request: no host check, no rate limit wait
        if hit is not None:
            return hit
    fetched = _download(url, ctx)
    if ctx.cache is not None:
        ctx.cache.put(url, fetched)
    return fetched


def _download(url: str, ctx: FetchContext) -> Fetched:
    cur_host = ""
    for _ in range(2):
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            cur_host = check_public_host(current)
            if not ctx.limiter.wait(cur_host):
                raise Blocked(f"{cur_host}: rate limited")
            try:
                with ctx.client.stream("GET", current, headers={"User-Agent": USER_AGENT},
                                       follow_redirects=False) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                        current = str(httpx.URL(current).join(resp.headers["location"]))
                        continue
                    if resp.status_code == 429:
                        ctx.limiter.penalize(cur_host, _retry_after(resp.headers.get("Retry-After")))
                        break
                    if resp.status_code in (401, 403, 451):
                        raise Blocked(f"HTTP {resp.status_code}")
                    if resp.status_code >= 400:
                        raise FetchError(f"HTTP {resp.status_code}")
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise FetchError("response larger than 5 MB")
                    text = bytes(body).decode(resp.encoding or "utf-8", errors="replace")
                    final = str(resp.url)
                    status = resp.status_code
            except httpx.HTTPError as exc:
                raise FetchError(str(exc) or type(exc).__name__) from None
            except (httpx.InvalidURL, ValueError) as exc:  # httpx parses Location itself (IDNAError)
                raise FetchError(f"invalid URL: {exc}") from None
            if is_challenge(text):
                raise Blocked("challenge page")
            return Fetched(final, status, text, bytes(body))
        else:
            raise FetchError("too many redirects")
    raise Blocked(f"{cur_host}: rate limited (HTTP 429)")


def looks_like_junk(text: str) -> bool:
    stripped = (text or "").strip()
    if len(stripped) < JUNK_MIN_CHARS:
        return True
    if len(stripped) < JUNK_MARKER_MAX_CHARS:
        low = stripped.lower()
        return any(m in low for m in CHALLENGE_MARKERS)
    return False


def article_text(url: str, ctx: FetchContext) -> FullText:
    try:
        page = http_get(url, ctx)
    except Blocked as exc:
        return FullText(str(exc), "blocked")
    except FetchError as exc:
        return FullText(str(exc), "error")
    text = (trafilatura.extract(page.text, url=page.url, include_links=True) or "")[:MAX_TEXT_CHARS]
    if looks_like_junk(text):
        return FullText(text, "short")
    return FullText(text, "ok")


def find_date(html: str, url: str) -> datetime | None:
    try:
        found = htmldate.find_date(html, url=url, original_date=True, extensive_search=False)
    except Exception:  # htmldate raises on odd markup; a missing date is not an error
        return None
    if not found:
        return None
    return datetime.fromisoformat(found).replace(tzinfo=timezone.utc)


def required_text(key: str):
    return lambda value: None if isinstance(value, str) and value.strip() else f"needs '{key}'"


def http_url(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "needs 'url'"
    if not value.strip().lower().startswith(("http://", "https://")):
        return "needs an http(s) url"
    return None


def optional_scan_depth(value) -> str | None:
    return None if value in (None, "headlines", "full") else "scan_depth must be headlines or full"


MAX_FETCH_EVERY = 60


def optional_fetch_every(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_FETCH_EVERY:
        return f"fetch_every must be a whole number of days between 1 and {MAX_FETCH_EVERY}"
    return None


def optional_site_url(value) -> str | None:
    if value is None:
        return None
    if http_url(value) is not None:
        return "site_url must be an http(s) URL"
    return None


MAX_SEARCH_QUESTION = 300


def optional_found_by(value) -> str | None:
    return None if value in (None, "search") else 'found_by must be "search"'


def optional_search_run(value) -> str | None:
    from evidence_echo.core import config   # config reads topics, which load source kinds on use
    if value is None:
        return None
    return None if isinstance(value, str) and config.RUN_ID_RE.match(value) else "search_run must be a search run id"


def optional_search_question(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= MAX_SEARCH_QUESTION:
        return f"search_question must be 1-{MAX_SEARCH_QUESTION} characters"
    return None


COMMON_FIELDS = {"scan_depth": optional_scan_depth, "fetch_every": optional_fetch_every,
                 "site_url": optional_site_url, "found_by": optional_found_by,
                 "search_run": optional_search_run, "search_question": optional_search_question}

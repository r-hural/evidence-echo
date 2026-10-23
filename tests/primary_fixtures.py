# tests/primary_fixtures.py
"""Seeds and a fake network for the SP3b-P tests. The network is always faked: requests go to an
httpx.MockTransport, and conftest's fake_dns answers every host with one public address."""
import httpx

from evidence_echo.sources import base
from tests.test_judge import SENTENCE
from tests.test_report_grading import _verified

ORIGINAL_URL = "https://orig.example.com/study"   # test_judge.VERDICT["primary_url"]
ARTICLE_QUOTE = SENTENCE                          # a sentence of the item's text (test_judge.TEXT)
ORIGINAL_QUOTE = "The variant group saw organic clicks rise by 4.2% against the control group."
SENTENCES = [
    "We tested rewritten title tags on 12,000 product pages over six weeks in spring.",
    "Pages were split 50/50 into a control group and a variant group by URL hash.",
    ORIGINAL_QUOTE,
    "Impressions did not change in a measurable way during the test window.",
    "We excluded pages that received internal link changes during the experiment.",
    "Search Console exports were pulled daily and compared with a two-sided t-test.",
    "The full dataset and the bucketing script are published with this write-up.",
]


def original_html(sentences=SENTENCES) -> str:
    """An article page trafilatura extracts to more than 500 chars (base.JUNK_MIN_CHARS)."""
    body = "".join(f"<p>{s}</p>" for s in sentences)
    return ("<html><head><title>Title tag study</title></head><body><article>"
            f"<h1>Title tag split test</h1>{body}</article></body></html>")


def fake_ctx(pages: dict, calls: list | None = None) -> base.FetchContext:
    """pages: url -> HTML text, or an HTTP status to answer with; unknown URLs get 404."""
    def handler(request):
        if calls is not None:
            calls.append(str(request.url))
        body = pages.get(str(request.url), 404)
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})
    return base.FetchContext(cutoff=None, client=httpx.Client(transport=httpx.MockTransport(handler)),
                             limiter=base.RateLimiter(interval=0))


def report_item(db, url="https://example.com/p1", **over) -> int:
    """A verified item (judge A, skeptic A by default; key numbers 4.2% and 12000) whose study
    cites ORIGINAL_URL unless `over` says otherwise (over: test_report_grading._verified's)."""
    return _verified(db, url=url, **over)

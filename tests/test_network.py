import pytest

from evidence_echo.sources import once, page, rss

pytestmark = pytest.mark.network


def test_rss_live():
    assert rss.check({"name": "Search Engine Roundtable", "url": "https://www.seroundtable.com/index.rdf"})["status"] == "ok"


def test_page_live():
    out = page.check({"name": "Anthropic news", "url": "https://www.anthropic.com/news"})
    assert out["status"] in ("ok", "blocked") and (out["status"] == "blocked" or out["article_links"] >= 3)


def test_once_live():
    assert once.check({"name": "Python 3.13 notes", "url": "https://docs.python.org/3/whatsnew/3.13.html"})["status"] == "ok"

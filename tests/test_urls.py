import pytest

from evidence_echo.core.urls import canonical_url


@pytest.mark.parametrize("raw,expected", [
    ("HTTP://Example.com:80/a/?utm_source=x&b=1#frag", "https://example.com/a/?b=1"),
    ("https://e.com/a?fbclid=1&gclid=2&id=3", "https://e.com/a?id=3"),
    ("https://e.com", "https://e.com/"),
])
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


@pytest.mark.parametrize("raw", ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x",
                                 "mailto:a@b.c", "", "not a url", "https://"])
def test_canonical_url_rejects_non_http(raw):
    assert canonical_url(raw) is None

# tests/test_primary.py
"""SP3b-P: selection (report items only), fetch and cache, number check. Network faked."""
import pytest

from evidence_echo.core import store
from evidence_echo.sources import base
from evidence_echo.stages import primary, report
from tests.primary_fixtures import ORIGINAL_URL, SENTENCES, fake_ctx, original_html, report_item


# --- selection ---

def test_selects_a_report_item_whose_study_cites_an_original(db):
    iid = report_item(db)
    [cand] = primary.select(db)
    assert (cand.item["id"], cand.url, cand.canonical) == (iid, ORIGINAL_URL, ORIGINAL_URL)


def test_a_study_without_an_original_is_not_selected(db):
    report_item(db, primary_url=None)
    assert primary.select(db) == []


def test_an_original_that_is_the_item_itself_is_not_selected(db):
    report_item(db, primary_url="https://example.com/p1")
    assert primary.select(db) == []


def test_a_non_web_original_is_not_selected(db):
    report_item(db, primary_url="javascript:alert(1)")
    assert primary.select(db) == []


def test_a_checked_item_is_not_selected(db):
    iid = report_item(db)
    store.save_primary_check(db, iid, primary_url=ORIGINAL_URL, status="undecided",
                             prompt_version="t")
    assert primary.select(db) == []


def test_only_items_of_the_next_report_are_selected(db, tmp_path, topic):
    report_item(db)
    report.render(db, '{"results": []}', tmp_path / "reports", "English", topic)  # now 'reported'
    assert primary.select(db) == []
    second = report_item(db, url="https://example.com/p2")   # new coverage of the same study
    assert [c.item["id"] for c in primary.select(db)] == [second]


# --- fetch and cache ---

def test_prepare_fetches_and_caches_the_original(db):
    iid = report_item(db)
    calls = []
    ctx = fake_ctx({ORIGINAL_URL: original_html()}, calls)
    assert primary.prepare(db, ctx) == {"selected": 1, "fetched": 1, "failed": 0,
                                        "unreachable": 0, "ready": 1}
    src = store.primary_source(db, ORIGINAL_URL)
    assert src["status"] == "ok" and "4.2%" in src["text"]
    assert primary.prepare(db, ctx)["fetched"] == 0          # cached: no second request
    assert calls == [ORIGINAL_URL]
    [p] = primary.pending(db)
    assert (p.item["id"], p.canonical) == (iid, ORIGINAL_URL) and "4.2%" in p.original


def test_items_sharing_an_original_fetch_it_once(db):
    report_item(db)
    report_item(db, url="https://example.com/p2")             # joins the study (same primary url)
    calls = []
    out = primary.prepare(db, fake_ctx({ORIGINAL_URL: original_html()}, calls))
    assert calls == [ORIGINAL_URL] and out["selected"] == 2 and out["ready"] == 2


def test_three_failed_runs_make_the_original_unreachable(db):
    iid = report_item(db)
    calls = []
    ctx = fake_ctx({ORIGINAL_URL: 404}, calls)
    for attempt in (1, 2):
        out = primary.prepare(db, ctx)
        assert (out["failed"], out["unreachable"], out["ready"]) == (1, 0, 0)
        assert store.primary_source(db, ORIGINAL_URL)["attempts"] == attempt
        assert store.primary_check(db, iid) is None           # no row yet: tried again next run
        assert primary.pending(db) == []
    assert primary.prepare(db, ctx)["unreachable"] == 1
    assert store.primary_source(db, ORIGINAL_URL)["status"] == "unreachable"
    assert store.primary_check(db, iid)["status"] == "unreachable"
    primary.prepare(db, ctx)
    assert len(calls) == 3                                     # never fetched again


def test_an_unreachable_original_marks_new_items_without_fetching(db):
    for _ in range(3):
        store.record_primary_fetch(db, ORIGINAL_URL, error="HTTP 404")
    iid = report_item(db)
    calls = []
    primary.prepare(db, fake_ctx({ORIGINAL_URL: original_html()}, calls))
    assert calls == [] and store.primary_check(db, iid)["status"] == "unreachable"


def test_a_page_too_short_to_be_a_study_counts_as_a_failure(db):
    report_item(db)
    primary.prepare(db, fake_ctx({ORIGINAL_URL: "<html><body><p>Download the PDF.</p></body></html>"}))
    src = store.primary_source(db, ORIGINAL_URL)
    assert (src["status"], src["text"], src["attempts"]) == ("failed", None, 1)
    assert src["last_error"] == "short"


def _ctx_with_body(body: str):
    import httpx

    def handler(request):
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})
    return base.FetchContext(cutoff=None, client=httpx.Client(transport=httpx.MockTransport(handler)),
                             limiter=base.RateLimiter(interval=0))


def _failed_not_cached(db):
    src = store.primary_source(db, ORIGINAL_URL)
    assert (src["status"], src["text"], src["attempts"]) == ("failed", None, 1)
    return src


@pytest.mark.parametrize("prefix", ["", "\n  ", "\ufeff"])
def test_a_pdf_body_is_a_failure_not_cached_text(db, prefix):
    report_item(db)
    pdf = prefix + "%PDF-1.7\n" + original_html()  # extractable text behind a PDF header
    primary.prepare(db, _ctx_with_body(pdf))
    assert _failed_not_cached(db)["last_error"]


def test_text_with_many_replacement_characters_is_a_failure(db):
    report_item(db)
    junk = "".join("<p>" + "\ufffd" * 20 + f" word{i} data here ok</p>" for i in range(60))
    primary.prepare(db, _ctx_with_body(f"<html><body><article>{junk}</article></body></html>"))
    _failed_not_cached(db)


def test_a_few_odd_characters_stay_ok(db):
    report_item(db)
    odd = SENTENCES[:3] + ["Caf\u00e9 na\u00efve \ufffd results \u2014 fine."] + SENTENCES[3:]
    primary.prepare(db, fake_ctx({ORIGINAL_URL: original_html(odd)}))
    src = store.primary_source(db, ORIGINAL_URL)
    assert src["status"] == "ok" and "na\u00efve" in src["text"]


def test_the_private_address_guard_applies(db, monkeypatch):
    report_item(db)
    monkeypatch.setattr(base, "resolve_host", lambda host: ["10.0.0.7"])
    calls = []
    out = primary.prepare(db, fake_ctx({ORIGINAL_URL: original_html()}, calls))
    assert calls == [] and out["failed"] == 1
    assert "refused" in store.primary_source(db, ORIGINAL_URL)["last_error"]


def test_a_crashing_fetch_counts_as_a_failure(db, monkeypatch):
    report_item(db)

    def boom(url, ctx):
        raise RuntimeError("odd URL")
    monkeypatch.setattr(base, "http_get", boom)
    assert primary.prepare(db, fake_ctx({}))["failed"] == 1
    assert "odd URL" in store.primary_source(db, ORIGINAL_URL)["last_error"]


def test_prepare_logs_a_run(db):
    report_item(db)
    primary.prepare(db, fake_ctx({ORIGINAL_URL: original_html()}))
    row = db.execute("SELECT detail FROM runs WHERE stage='primary'").fetchone()
    assert "fetched=1" in row["detail"]


# --- number check ---

FACTS = {"match_keys": {"key_numbers": ["4.2%", "12k"]}}


def test_number_check_all_found():
    assert primary.number_check(FACTS, "Clicks rose 4.2% on 12,000 pages.") == ("ok", [])


def test_number_check_partial():
    assert primary.number_check(FACTS, "Clicks rose 3.9% on 12,000 pages.") == ("partial", ["4.2%"])


def test_number_check_none():
    assert primary.number_check(FACTS, "No figures here.") == ("none", ["4.2%", "12k"])


def test_number_check_reads_percent_words():
    assert primary.number_check(FACTS, "Clicks rose 4.2 per cent on 12,000 pages.") == ("ok", [])
    assert primary.number_check(FACTS, "Clicks rose 4.2 percent on 12,000 pages.") == ("ok", [])


def test_number_check_ignores_the_sign():
    assert primary.number_check({"match_keys": {"key_numbers": ["-4.2%"]}},
                                "Clicks fell 4.2% in the variant group.") == ("ok", [])


def test_number_check_without_key_numbers():
    assert primary.number_check({}, "anything") == (None, [])
    assert primary.number_check({"match_keys": {"key_numbers": []}}, "4.2%") == (None, [])
    assert primary.number_check(None, "4.2%") == (None, [])


def _facts(*numbers):
    return {"match_keys": {"key_numbers": list(numbers)}}


def test_number_check_normalises_the_items_numbers_too():
    assert primary.number_check(_facts("4.2 percent"), "Clicks rose 4.2% overall.") == ("ok", [])
    assert primary.number_check(_facts("4.2%"), "Clicks rose 4.2 percent overall.") == ("ok", [])


def test_number_check_reads_percentage_points():
    assert primary.number_check(_facts("4.2 percentage points"), "Up 4.2% overall.") == ("ok", [])
    assert primary.number_check(_facts("4.2 pp"), "Up 4.2 percentage points.") == ("ok", [])
    assert primary.number_check(_facts("4.2%"), "Up 4.2pp overall.") == ("ok", [])


def test_number_check_matches_million_words_and_suffix_both_ways():
    assert primary.number_check(_facts("1.2 million"), "Reached 1.2M users.") == ("ok", [])
    assert primary.number_check(_facts("1.2M"), "Reached 1.2 million users.") == ("ok", [])
    assert primary.number_check(_facts("5 thousand"), "About 5,000 pages.") == ("ok", [])

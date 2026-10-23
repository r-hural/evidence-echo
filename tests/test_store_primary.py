"""SP3b-P: the primary_* store API over SP3b-0's schema step 5 tables."""
import json

import pytest

from evidence_echo.core import store
from tests.conftest import make_item

URL = "https://orig.example.com/study"


def _item(db, url="https://example.com/p1") -> int:
    store.insert_items(db, [make_item(url=url)])
    return store.find_by_canonical(db, url)["id"]


def test_primary_source_absent(db):
    assert store.primary_source(db, URL) is None


def test_record_fetch_ok(db):
    row = store.record_primary_fetch(db, URL, text="the original text")
    assert (row["status"], row["text"], row["last_error"]) == ("ok", "the original text", None)
    assert (row["attempts"] or 0) == 0 and row["fetched_at"]
    assert store.primary_source(db, URL) == row


def test_three_failures_make_the_url_unreachable(db):
    statuses = [store.record_primary_fetch(db, URL, error="HTTP 404")["status"] for _ in range(3)]
    assert statuses == ["failed", "failed", "unreachable"]
    row = store.primary_source(db, URL)
    assert row["attempts"] == 3 and row["last_error"] == "HTTP 404" and row["text"] is None


def test_success_after_a_failure_clears_the_error(db):
    store.record_primary_fetch(db, URL, error="HTTP 503")
    row = store.record_primary_fetch(db, URL, text="text")
    assert (row["status"], row["last_error"], row["attempts"]) == ("ok", None, 1)


def test_record_fetch_needs_exactly_one_of_text_or_error(db):
    with pytest.raises(ValueError):
        store.record_primary_fetch(db, URL)
    with pytest.raises(ValueError):
        store.record_primary_fetch(db, URL, text="t", error="e")


def test_save_and_read_checks(db):
    a, b = _item(db), _item(db, "https://example.com/p2")
    store.save_primary_check(db, a, primary_url=URL, numbers="partial", missing=["4.2%"],
                             verdict="overstated", quote_article="x y z", quote_primary="a b c",
                             note="claims more", status="agreed", prompt_version="primary-1")
    row = store.primary_check(db, a)
    assert (row["numbers"], row["verdict"], row["status"]) == ("partial", "overstated", "agreed")
    assert json.loads(row["missing"]) == ["4.2%"] and row["checked_at"]
    assert store.primary_check(db, b) is None
    assert set(store.primary_checks_for(db, [a, b])) == {a}
    assert store.primary_checks_for(db, []) == {}


def test_an_unreachable_row_needs_no_verdict(db):
    a = _item(db)
    store.save_primary_check(db, a, primary_url=URL, status="unreachable", prompt_version="p")
    row = store.primary_check(db, a)
    assert row["verdict"] is None and row["numbers"] is None and json.loads(row["missing"]) == []


def test_save_check_rejects_an_unknown_status(db):
    with pytest.raises(ValueError):
        store.save_primary_check(db, _item(db), primary_url=URL, status="pending", prompt_version="p")


def test_add_red_flag_once(db):
    iid = _item(db)
    db.execute("UPDATE items SET red_flags=? WHERE id=?", (json.dumps(["no_baseline"]), iid))
    assert store.add_item_red_flag(db, iid, "misrepresented_primary") is True
    assert store.add_item_red_flag(db, iid, "misrepresented_primary") is False
    assert json.loads(store.get_item(db, iid)["red_flags"]) == ["no_baseline", "misrepresented_primary"]


@pytest.mark.parametrize("raw", [None, "", "not json", '{"a": 1}'])
def test_add_red_flag_to_an_item_without_a_flag_list(db, raw):
    iid = _item(db)
    db.execute("UPDATE items SET red_flags=? WHERE id=?", (raw, iid))
    assert store.add_item_red_flag(db, iid, "misrepresented_primary") is True
    assert json.loads(store.get_item(db, iid)["red_flags"]) == ["misrepresented_primary"]


def test_add_red_flag_to_a_missing_item(db):
    assert store.add_item_red_flag(db, 999, "misrepresented_primary") is False

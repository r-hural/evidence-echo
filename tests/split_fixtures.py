"""Counted studies for the split tests: a study with a verified item, placed in a subtopic / question /
metric by slug."""
import json
from itertools import count

from evidence_echo.core import store
from tests.conftest import make_item

_n = count(1)


def add_study(db, subtopic=None, metric=None, question=None, name=None, measured="clicks rose", **cols):
    """One counted study (active, an item on the page). `subtopic` / `metric` are slugs, `question` an id."""
    n = next(_n)
    sid = store.create_study(db, "B", {"what_was_tested": name or f"Study {n}", "findings": "f",
                                       "research_timeframe": "2024", "metric": measured}, None)
    url = f"https://e.com/s{sid}-{n}"
    store.insert_items(db, [make_item(url=url)])
    iid = db.execute("SELECT id FROM items WHERE url=?", (url,)).fetchone()[0]
    db.execute("UPDATE items SET study_id=?, state='verified', full_text='t', facts=? WHERE id=?",
               (sid, json.dumps({"what_was_tested": "x"}), iid))
    if subtopic is not None:
        db.execute("UPDATE studies SET subtopic_id=? WHERE id=?", (store.get_subtopic(db, subtopic)["id"], sid))
    if metric is not None:
        db.execute("UPDATE studies SET metric_id=? WHERE id=?", (store.get_metric(db, metric)["id"], sid))
    if question is not None:
        db.execute("UPDATE studies SET question_id=?, question_direction='up' WHERE id=?", (question, sid))
    for key, value in cols.items():
        db.execute(f"UPDATE studies SET {key}=? WHERE id=?", (value, sid))
    db.commit()
    return sid


def add_many(db, how_many, **kw):
    return [add_study(db, **kw) for _ in range(how_many)]


def add_question(db, subtopic, text, measure="the measure", kind="level"):
    return store.create_question(db, store.get_subtopic(db, subtopic)["id"], text, measure, kind)


def with_decided_proposal(db, target, source_slug, studies_at):
    """A decided split proposal on a source (rows only; what the owner did is not needed here)."""
    source = (store.get_subtopic(db, source_slug) if target == "subtopic" else store.get_metric(db, source_slug))
    pid = db.execute("""INSERT INTO split_proposals (target, source_id, studies_at, status, created_at, decided_at)
                        VALUES (?,?,?,'decided','x','y')""", (target, source["id"], studies_at)).lastrowid
    db.commit()
    return pid

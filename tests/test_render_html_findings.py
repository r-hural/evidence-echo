"""Quotes-mode pages: findings instead of questions; questions-mode markup is untouched."""
import json
import re

from evidence_echo.render import html as html_render
from evidence_echo.render import html_assets
from tests.html_fixtures import (entry, finding, finding_row, findings_subtopic, report, topic_model, topic_study,
                                 view)

EVIL = "<script>alert(1)</script> & x"


def quotes_topic(rows, studies=None, **over):
    studies = studies if studies is not None else [topic_study(entry(r.study_id, findings=[], findings_state="done"))
                                                   for r in rows if r.study_id]
    count = sum(1 for r in rows if r.finding is not None)
    return topic_model(studies, mode="quotes", findings=[findings_subtopic(rows)], finding_count=count, **over)


def page_of(t) -> str:
    return html_render.topic_page(t, "overview.html", "report.html")


def test_findings_section_replaces_evidence():
    t = quotes_topic([finding_row(3), finding_row(4)])
    out = page_of(t)
    assert 'id="ee-findings"' in out and "<h2>Findings</h2>" in out and 'id="ee-evidence"' not in out
    assert 'id="sub-local"' in out and 'data-subtopic="local"' in out and '<details class="ee-sub">' in out
    assert "2 findings · 2 studies" in out


def test_findings_section_index_with_two_subtopics():
    t = topic_model([topic_study(entry(1))], mode="quotes", finding_count=2, findings=[
        findings_subtopic([finding_row(1)], "a", "Alpha"), findings_subtopic([finding_row(2)], "b", "Beta")])
    out = page_of(t)
    assert 'href="#sub-a"' in out and 'href="#sub-b"' in out and 'aria-label="Subtopics"' in out


def test_finding_row_parts():
    row = finding_row(3, labels=["observational"], date="2026-08")
    out = html_render.finding_row(row, {3: "Study three"})
    assert '<li class="ee-finding" data-study="3">' in out
    assert 'class="tier tier-b"' in out and '<span class="ee-gl">observational</span>' in out
    assert "Clicks rose 1%" in out and '<a href="#s3">Study three</a>' in out
    assert "2026-08" in out and "Clicks" in out
    assert '<details class="ee-quote"><summary>Quote</summary>' in out and " open" not in out.split("<details")[1][:40]
    assert "<blockquote>“Clicks rose 1% on forty pages.”</blockquote>" in out
    assert "ee-warn" not in out


def test_finding_row_without_card_is_plain_text():
    out = html_render.finding_row(finding_row(5, name="Not reported yet"), {})
    assert "Not reported yet" in out and 'href="#s5"' not in out


def test_unchecked_quote_row_has_mark():
    row = finding_row(3, finding=finding(quote_checked=False))
    out = html_render.finding_row(row, {3: "T"})
    assert (f'<span class="ee-warn"><span class="ee-mark" aria-hidden="true">#</span>'
            f"{html_render.UNCHECKED_QUOTE}</span>") in out
    assert html_render.UNCHECKED_QUOTE == "Quote not found in the article"
    assert "<summary>Quote</summary>" not in out and "∅" not in page_of(quotes_topic([row]))


def test_waiting_and_failed_rows():
    waiting = html_render.finding_row(finding_row(3, finding=None, state="waiting"), {3: "Title"})
    assert '<span class="meta">findings waiting</span>' in waiting and "ee-quote" not in waiting
    assert '<a href="#s3">Title</a>' in waiting
    failed = html_render.finding_row(finding_row(4, finding=None, state="failed"), {4: "Other"})
    assert "waiting" not in failed and "ee-quote" not in failed and '<a href="#s4">Other</a>' in failed


def test_finding_text_is_escaped():
    row = finding_row(3, name=EVIL, finding=finding(text=EVIL, quote=EVIL, measure=EVIL, metric_name=EVIL))
    out = page_of(quotes_topic([row]))
    assert "<script>alert(1)" not in out.split("<style>")[1].split("</head>")[1]
    assert out.count("&lt;script&gt;alert(1)&lt;/script&gt; &amp; x") >= 3
    card = html_render.card(entry(1, findings=[finding(text=EVIL, quote=EVIL)], findings_state="done"))
    assert "<script>" not in card and "&lt;script&gt;" in card


def test_quotes_header_counts():
    rows = [finding_row(1), finding_row(1, finding=finding(2)), finding_row(2), finding_row(3, finding=None, state="waiting")]
    studies = [topic_study(entry(n, findings=[], findings_state="done")) for n in (1, 2, 3)]
    t = quotes_topic(rows, studies)
    assert "3 studies · 3 findings · updated " in page_of(t)
    questions = page_of(topic_model(studies))
    assert "3 studies · updated " in questions and "findings" not in questions.split("</header>")[0]


def test_filter_rows_carry_metric_union_in_quotes_mode():
    e = entry(1, view=view(metric_slug="ctr", metric_name="CTR"),
              findings=[finding(1, metric_slug="clicks", metric_name="Clicks"),
                        finding(2, metric_slug="ctr", metric_name="CTR"),
                        finding(3, metric_slug=None, metric_name=None)], findings_state="done")
    t = topic_model([topic_study(e)], mode="quotes")
    row = html_render.filter_data(t)["studies"][0]
    assert row["metrics"] == ["ctr", "clicks"] and row["metric"] == "ctr"
    other = entry(2, view=view(), findings=[finding(1, metric_slug="clicks", metric_name="Clicks")], findings_state="done")
    t2 = topic_model([topic_study(e), topic_study(other)], mode="quotes", metrics=[("clicks", "Clicks"), ("ctr", "CTR")])
    options = html_render._metric_options(t2, [e, other])
    assert options == [("clicks", "Clicks (2)"), ("ctr", "CTR (1)")]


def test_questions_filter_rows_unchanged():
    e = entry(1, view=view(metric_slug="ctr", metric_name="CTR"))
    row = html_render.filter_data(topic_model([topic_study(e)]))["studies"][0]
    assert "metrics" not in row and row["metric"] == "ctr"


def test_js_findings_variant():
    assert html_assets.JS_FINDINGS != html_assets.JS
    assert "s.metrics" in html_assets.JS_FINDINGS and "#ee-findings [data-study" in html_assets.JS_FINDINGS
    assert "s.metrics" not in html_assets.JS and "#ee-findings" not in html_assets.JS
    assert html_assets.JS_FINDINGS.count("el.hidden = !ok;") == 1


def test_quotes_page_uses_findings_script_and_css():
    t = quotes_topic([finding_row(3)])
    out = page_of(t)
    assert html_assets.FINDINGS_CSS in out and "s.metrics" in out
    plain = page_of(topic_model([topic_study(entry(1))]))
    assert html_assets.FINDINGS_CSS not in plain and "s.metrics" not in plain


def test_page_css_extra_is_appended_inside_style():
    assert html_render.page("T", "b") == html_render.page("T", "b", css_extra="")
    out = html_render.page("T", "b", css_extra=".x{}")
    assert f"{html_assets.CSS}.x{{}}</style>" in out


def test_quotes_card_lists_findings():
    e = entry(1, subtopic={"key": "local", "name": "Local", "question": None, "label": None, "independent": 0, "echo": None},
              findings=[finding(1), finding(2, quote_checked=False)], findings_state="done")
    card = html_render.card(e)
    assert "<b>Findings:</b>" in card and "Clicks rose 1%" in card and "Question" not in card
    assert card.count('<span class="ee-mark" aria-hidden="true">#</span>') == 1
    assert "<b>Subtopic:</b> Local" in card
    rep = html_render.report_page(report(new=[e], mode="quotes"), "t.html", "o.html")
    assert "<b>Findings:</b>" in rep
    waiting = html_render.card(entry(2, findings=[], findings_state="waiting"))
    assert "<b>Findings:</b> waiting" in waiting
    failed = html_render.card(entry(3, findings=[], findings_state="failed"))
    assert "Findings" not in failed


def test_questions_card_has_no_findings_field():
    assert "Findings" not in html_render.card(entry(1))


def test_ratings_key_widened_only_in_quotes_mode():
    questions = html_render.report_page(report(), "t.html", "o.html")
    quotes = html_render.report_page(report(mode="quotes"), "t.html", "o.html")
    assert "numbers not found in the original" in questions and "a number or quote not found" not in questions
    assert "a number or quote not found in the article" in quotes and "numbers not found in the original" not in quotes
    assert html_render.RATINGS_KEY != html_render.RATINGS_KEY_FINDINGS


def test_filters_form_lists_findings_subtopics():
    studies = [topic_study(entry(1, subtopic={"key": "a", "name": "Alpha", "question": None, "label": None,
                                              "independent": 0, "echo": None}, findings=[], findings_state="done")),
               topic_study(entry(2, subtopic={"key": "b", "name": "Beta", "question": None, "label": None,
                                              "independent": 0, "echo": None}, findings=[], findings_state="done"))]
    t = topic_model(studies, mode="quotes", finding_count=2, findings=[
        findings_subtopic([finding_row(1)], "a", "Alpha"), findings_subtopic([finding_row(2)], "b", "Beta")])
    out = page_of(t)
    assert '<option value="a">Alpha</option>' in out and '<option value="b">Beta</option>' in out
    data = json.loads(re.search(r'id="ee-data">(.*?)</script>', out, re.S).group(1))
    assert {r["subtopic"] for r in data["studies"]} == {"a", "b"}

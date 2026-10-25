# Evidence Echo — Glossary

One meaning per term. Docs, skills, reports and code use these words.

## Hierarchy

```
Workspace
├── Topic (SEO, AI Coding)                    configured: own sources, database, reports
│   ├── Subtopic (AI Overviews & AI search)   one per study; counted
│   │   └── Question (does freshness raise AI citations?)   one per study, inside its subtopic
│   │       └── Study ← covered by articles
│   └── Entity (ChatGPT, Shopify)             per topic; what a study is about; 0..5 per study
```

Subtopics, questions, metrics and entities are independent angles inside one topic, not levels of one
tree; nothing links topics. Questions sit inside a subtopic. A metric is the outcome a study counted,
one per study, from the topic's own list. An entity is a named thing the study is about, zero to five
per study, from the topic's own list.

| Term | Answers | Per study | List |
|---|---|---|---|
| **Topic** | which area I monitor | one (the study lives in its topic's database) | user-defined |
| **Subtopic** | which aspect of the topic | exactly one (or none: Unsorted) | short, curated, counted in the evidence summary |
| **Question** | what exactly the study measured (and, for a test, changed) | at most one, one per topic whatever its studies' subtopics (none until grouped, none in Unsorted) | automatic, grows from the studies; the owner renames, merges and moves |
| **Metric** | what outcome the study counted (clicks, rankings, AI citations) | exactly one, from the topic's list (`Other` when none fits) | per topic: seeded from `topic.json` or the template; the owner renames and merges; the engine proposes splits of a bloated `Other` |
| **Entity** | which named thing the study is about | zero to five | per topic: grows from the studies; the owner renames, merges, aliases and rejects |
| **Conducted by** | who ran the study | one | provenance, not grouping |

An entity is what a study is **about**, never who made it. A study belongs to Shopify when it was
run on Shopify stores or compares Shopify with other platforms. A study Shopify published belongs
to Shopify only when its subject is Shopify's platform or stores; otherwise Shopify is just its
`conducted_by`.

## Terms A–Z

**Article** — one fetched publication (blog post, news item, page, document, LinkedIn post).
Code: `item` (`items` table). Deprecated in prose: post, publication. *See also* entry.

**Backfill** — the research spike: a one-time load of older articles for a new topic or source
(`ev backfill`).

**Category** — deprecated; means subtopic.

**Cell** — deprecated; the old subtopic × metric row of "What the evidence says". Replaced by
question.

**Conducted by** — the organisation that ran a study. Provenance, shown next to the tier; never a
grouping. Studies with the same `conducted_by` count as one independent study.

**Corroboration** — independent studies (different `conducted_by`) agreeing. Opposite of echo.

**Data date** — the month or year a study's data was collected. Code: `data_date`, filled from
the judge's `research_timeframe` (raw text of the period). Fallback when unknown: "reported" +
the earliest article's publication date.

**Digest** — only in the product description ("an evidence-graded digest"). The thing a run
produces is a report.

**Direction** — the result of a study on its question's measure: `up`, `down`, `no_change`,
`mixed` (the study's own result is mixed). Code: `question_direction`. The judge's older
`direction` field (`positive`, `negative`, `no_effect`, `mixed`) is retired: it is not asked, not stored
and not shown (the columns `items.direction` and `studies.direction` remain, unused). *Not* a row
label; see varied.

**Domain / scope** — free-text fields of a topic, read by the judge and skeptic prompts.

**Echo** — one study repeated by several articles ("1 study · 4 articles"). Opposite of
corroboration. (Also part of the product name.)

**Edition** — one study in a series ("edition N of series"). Deprecated meaning: the 0.4 name for
a topic (`editions/<id>.json`, now `templates/` and `topics/`).

**Entity** — a named thing a study is about (product, platform, company, AI model, search feature,
algorithm update), per topic, zero to five per study, shown as "About: <names>" and as the topic
page's Entity filter. Named by the judge (new studies) or classify (older ones); the owner renames,
merges, aliases, rejects and restores them and adds or removes one on a study. Code: `entities`,
`study_entities`, a checked flag on studies and `entity_names` on items. `match_keys.subjects` is
something else.

**Entry** — a study's block in one report (`report_entries`, `Entry`).

**Event horizon** (planned) — the maximum age of the data a study reports, measured on its data
date.

**Explanation** — one per question: the relate step's short synthesis plus one checked line per study
outside the question's main result, shown only while it matches the question and only for lines where both
blind comparers agree and the quote checks. A level question gets one when two independent studies' values
are at least 3x apart; its main result is then the best-graded study's value.
Code: `question_explanations` (one row per question). Do not call it a relation.

**Fetch horizon** — the maximum age of an article when it is fetched; older ones are skipped.
Code: `fetch_horizon_days` (formerly `research_horizon_days`, `lookback_days`).

**Finding** — deprecated; use study (the overview's "top findings" are studies).

**Incomplete** — retired with questions: the topic page no longer notes studies with a missing
metric or direction. It says "N studies wait to be grouped" instead.

**Issue** — deprecated; use report.

**Comparison** — a difference question's statement of what A is compared with ("pages with an
updated date vs pages without"); at most 120 characters, shown as "Compared: ..." under the
question. Code: `questions.comparison`. Level and capability questions have none.

**Difference question** — a question of kind `difference`: is the measure different under A than
under B? Experiments, before/after cases, A/B comparisons, correlations, effects of an event,
measurement gaps. Only difference questions carry per-study directions and a label.

**Label** — a secondary signal on a difference question: its verdict on its studies' directions, only with two or more independent
studies and every direction read: `consistent` (all the same direction), `contested` (up and down),
`varied` (any other mix). A question with one independent study has no label (`single` is retired).
A single study's own direction `mixed` stays.

**Measure** — a question's quantity that directions are read against ("AI citations of the page";
"up" = it went up / is higher). Code: `questions.measure`. Not the metric and not "Measured".

**Measured** — the line on a card, "Measured: <metric name> · <free text>": the study's metric (left
out when it is Other) and the judge's free-text description of what was measured (code:
`studies.metric`). With only one of the two, only that one; with neither, no line. Not the metric
itself.

**Metric** — the main outcome a study counted, one per study, from its topic's own list (the SEO
template lists Clicks, Rankings, AI citations, ...). `Other` always exists, is always last and cannot
be rejected, retired or merged. Statuses as for subtopics (seeded, confirmed, proposed, rejected,
retired); the slug never changes. Code: the `metrics` table, `studies.metric_id`, `items.metric_code`
(the judge's slug), `topic.json` `metrics`, `ev metrics`. The topic page filters by metric. A
third angle next to subtopic and question; no label or summary per metric.

**Original** — the study's own publication, which a secondary article reports. Checked by the
primary check. Do not call it a source.

**Overview** — the page across all topics (`overview.md`, `overview.html`).

**Primary check** — comparing articles with their original: number check plus two blind
verdicts (faithful, overstated, direction flipped, wrong metric, cherry-picked). Stage `primary`.

**Red flag** — a coded methodology problem tagged by the skeptic (or `misrepresented_primary` by
the primary check). Never changes the tier.

**Relation** — provenance only: how the study's runner relates to its subject (`vendor`,
`independent`, `agency`, `platform`, `unknown`). Not a link between studies (that's an
explanation, or later an entity).

**Level question** — a question of kind `level`: how much, how often, what share is the measure?
Prevalence, shares, benchmarks, rankings, trends over time without a tested cause. No direction,
no label; an explanation when its values spread (see Explanation).

**Question** — what a study measured and, for a test, what it changed ("Does updating the
publication date raise AI citations?"). One question exists once per topic; it sits in one subtopic
and its studies follow it there. Studies that answer the same question are compared; a label and
explanations exist only on a difference question. Assigned automatically by the group step; the
owner renames, merges and moves studies (`ev questions`), also across subtopics. Code: `questions`,
`question_id`, `question_direction`. A study not grouped yet is listed as "Not grouped yet" (Obsidian: under "Other studies").

**Question kind** — `difference`, `level` or `capability`: what a question asks. Decided by the
group step (or the owner with `--kind`); a question created before kinds existed has none until an
answer fills it. Code: `questions.kind`.

**Report** — the dated output of one report run for one topic (`<date>-report.md` / `.html`).
Deprecated: issue.

**Research horizon** — deprecated name for fetch horizon.

**Run** — one execution of the pipeline for a topic, ending in a report.

**Series** — a study repeated over time (Stack Overflow developer survey 2023–2025);
its members are editions.

**Source** — a configured feed or page of a topic: kind `rss`, `page`, `once` or `linkedin`.
Not the original of a study.

**Study** — one data collection (experiment, test, survey, analysis). Articles cover it; it has
one tier, one subtopic, one question, a direction on that question's measure, a data date and
later entities.

**Split proposal** — the engine's proposal to split an overloaded subtopic (more than 30% of the
studies, at least 15), a bloated Other subtopic or a bloated `other` metric (more than 20%, at least
10) into parts, made by the `split` stage. Its parts are split names: new subtopics or metrics in status
`proposed` that the judge and classify never reuse, or `into` groups that move into an existing one.
The owner accepts, renames or rejects each part (`ev split`); nothing moves before an accept. After an
accepted subtopic split the owner chooses to keep the source for leftovers or retire it. Code: tables
`split_proposals`, `split_groups`, `split_moves`.

**Subtopic** — a curated aspect of a topic ("AI Overviews & AI search"); seeded from the topic's
list, the judge picks one or proposes a new one, the owner confirms. Statuses: seeded, confirmed,
proposed, rejected, retired. Slug never changes. Code and CLI: `subtopic` (`ev subtopics`, `subtopics`
table, `topic.json` `subtopics`). Deprecated in prose: theme, category.

**Template** — a ready-made topic definition shipped with the plugin (`templates/seo.json`),
including its subtopic list and renames (`subtopic_renames`).

**Tier** — evidence grade: A controlled, B large-scale, C case study, D practitioner anecdote.

**Topic** — one monitored area the user configures: own `topic.json`, sources, database
(`echo.db`) and reports. The top level of grouping inside a workspace.

**Topic page** — the per-topic page across all runs (`<id>.html`), opening with "What the
evidence says".

**Unsorted** — studies without a subtopic (none fits yet, proposal pending, or rejected).

**Varied** — the question label for directions that differ without being plainly up vs down
(e.g. up + no change).

**Workspace** — the data folder (`~/.evidence-echo`): `config.json`, `topics/`, `reports/`.

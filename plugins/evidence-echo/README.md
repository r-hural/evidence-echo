# Evidence Echo — Claude Code Plugin

Evidence-graded content digests for any number of topics. For each topic it
fetches the configured sources (RSS feeds, web pages, single documents, LinkedIn),
gates on "measured outcome present", grades evidence into tiers (A controlled /
B large-scale / C case study / D practitioner anecdote) with code-checked quotes,
verifies grading with a blind skeptic pass, groups articles into studies, files
each study under a subtopic and a question, and checks articles against
the original study they report. It renders a result-first Markdown report, an
HTML digest and topic page ("What the evidence says": per subtopic and question, the
studies newest first and how results changed), an overview across topics, and
optionally Obsidian notes.

You define the topics (SEO, Claude Code, weight loss, ...). Ready-made templates
ship in `templates/` (`ev templates` lists them): SEO (`seo`), AI coding tools (`ai-coding`),
conversion optimization and A/B tests (`cro`) and exercise science (`exercise`); any other topic starts blank, described in your own words. Each topic has its own sources, database and reports.

## Install

    claude plugin marketplace add r-hural/evidence-echo
    claude plugin install evidence-echo@evidence-echo

Requirements: Python 3.11+ as `python`, `py -3` (Windows launcher) or `python3`. Nothing else to install — the
first command sets up a private environment in the plugin's data folder (about a
minute) via `scripts/ev.py`, using [uv](https://docs.astral.sh/uv/) when
available and `venv` + `pip` otherwise. It is rebuilt automatically when a
plugin update changes the dependencies.

## Getting started

1. Install the plugin and start a Claude Code session. The first session shows a
   one-line hint that Evidence Echo is not set up (once).
2. Say "set up Evidence Echo" or run `/evidence-echo:setup`. The setup skill asks
   one question at a time: where reports go, which demo topics to import (none are
   added unless you pick them), any topics of your own (described in your own
   words), their sources (every feed is checked), and the report defaults. It works without any API key.
3. Ask for an "Evidence Echo report" (skill `evidence-echo:report`). With several
   topics it asks which one to run, or all of them. Run `/evidence-echo:setup`
   again at any time to add a topic, change sources or change settings.

Until it is set up, the engine refuses pipeline commands (exit code 4) and the
report skill hands over to the setup.

## Plugin settings

Set in the plugin configuration (Claude Code asks on install; change later via
`/plugin`). Everything else is stored by the engine in `config.json` and
`topic.json` (below) — use the setup skill, not the plugin settings.

| Option | Default | Meaning |
|---|---|---|
| `data_dir` — Data folder (advanced) | empty = `~/.evidence-echo` | Where topics and databases live. Do not use a cloud-synced folder |
| `anthropic_api_key` | — | Optional; enables faster unattended runs (backends `auto` then choose `api`) |
| `voyage_api_key` | — | Optional; enables embeddings (embedder `auto` then chooses `voyage`); only study cards (the judge's short summary of each item and its title, never article text) are sent, and similar studies then join the match step as candidates |
| `apify_api_token` | — | Optional; enables LinkedIn sources |

A SessionStart hook passes the settings to the engine; keys never appear in
prompts or command lines. The hook writes them to `<plugin data>/keys.json`
(owner-only permissions on macOS/Linux; on Windows it sits in your user
profile), which only the engine reads, and never exports them to the session's
commands. Precedence: `EV_ECHO_ANTHROPIC_API_KEY` (etc.) if set > the plugin
settings > a plain `ANTHROPIC_API_KEY` in your shell. Clearing the keys in the
settings removes the file at the next session start. Never paste a key into the
chat.

## Topics and folders

Data folder resolution: `EV_ECHO_DATA_DIR` > the `data_dir` plugin setting >
`~/.evidence-echo`. The plugin's own data folder (`CLAUDE_PLUGIN_DATA`) is never
used for studies, because Claude Code deletes it on uninstall; it only holds
`keys.json` and the launcher's virtual environments (override with
`EV_ECHO_PLUGIN_DATA`; fallback `<data>/cache`).

    <data>/                                 default ~/.evidence-echo
      config.json                           global settings
      state.json                            engine bookkeeping (never edit)
      topics/<id>/topic.json                topic definition, sources, overrides
      topics/<id>/echo.db                   the topic's database
      reports/<id>/                         reports when no reports_dir is set

    <reports_dir>/                          chosen in setup
      overview.md                           one page across all topics
      <id>/<YYYY-MM-DD>-report.md           reports, unless the topic sets its own folder

Configured paths expand `~`, `%VAR%` and `$VAR`; a path that is still relative is
refused. Missing folders are created on first write.

### `config.json`

Global settings, written by `ev config set` (all optional; unknown fields are an
error): `reports_dir`, `triage_backend` and `judgment_backend` (`auto|api|session`),
`embedder` (`auto|voyage|none`), `output_language` (default `en`),
`fetch_horizon_days` (whole days 1–3650, default 7; how old an article may be when fetched; replaces `lookback_days` and `research_horizon_days` — existing
files are renamed once), `scan_depth` (`headlines|full`, default `headlines`: triage reads
titles and excerpts, or the start of the full text), `ask_on_disputes` (`off|on`, default `off`: disputed
items appear in the report's Disputed section with both grades and the skeptic's
reason; `on`: the report skill asks you to rule on each one first), `adaptive_fetch` (`off|on`,
default `off`, also per topic or `EV_ECHO_ADAPTIVE_FETCH`: each source is fetched about as often
as it publishes and quiet sources are skipped until due; no source waits longer than
`fetch_horizon_days` minus one day, or 6 days for LinkedIn, so nothing ages out meanwhile),
`html_reports` (`on|off`, default `on`, also per topic or `EV_ECHO_HTML_REPORTS`: each report
also gets HTML pages; during a report run `overview.html` follows the topic's `html_reports`, while `ev overview` follows the workspace setting), `md_reports` (`on|off`, default `on`, also `EV_ECHO_MD_REPORTS`, global only: `off` keeps the Markdown report, its rejections report and `overview.md` out of the reports folder, in `<data>/markdown/`, where Claude still reads them; it applies only while HTML is on, so a topic with `html_reports` off keeps its Markdown reports), `rejections_report` (`on|off`, default `off`, also per topic or `EV_ECHO_REJECTIONS_REPORT`: every report also writes the rejections report, see "Rejections report"), `obsidian_dir` (your Obsidian vault's folder, or empty; default empty = no
export; also `EV_ECHO_OBSIDIAN_DIR`; `ev config set` refuses a folder that does not exist and
never creates one), `relate` (`on|off`, default `on`, also per topic or `EV_ECHO_RELATE`: two blind comparers explain contested evidence, see "What the evidence says"; `off` skips the relate step), `classify_batch` (default `40`, `0` = off, also per topic or `EV_ECHO_CLASSIFY_BATCH`: how many older studies the classify step fills in per run), `overview_studies` (default `10`, `0` = latest report only, also `EV_ECHO_OVERVIEW_STUDIES`, global only: how many recently reported studies each topic lists in the overview), `fetch_cache_minutes` (default `60`, `0` = off, also `EV_ECHO_FETCH_CACHE_MINUTES`, global only: feed and page downloads are kept this long and shared by every topic; `ev fetch --fresh` downloads again). `auto` picks `api` when an Anthropic key is set,
else `session` (judged by Claude in the session); the embedder picks `voyage`
when a Voyage key is set, else `none`. With a Voyage key, study matching also proposes studies with similar text as candidates; without one, matching uses the rules alone.

### `topic.json`

Required: `id` (lowercase letters, digits and `-`, up to 40 characters; equals
the folder name and never changes), `name`, `domain`, `scope`, `sources`.
Optional: `subtopics`, `template` (informational), `linkedin_apify_actor`. A topic
can override `reports_dir`, `output_language`, `fetch_horizon_days`, `scan_depth`, `adaptive_fetch`,
`html_reports`, `relate` and `classify_batch`; `null` or
absent means "use `config.json`". Sources are `{"type": "rss", "name", "url"}`,
`{"type": "page", "name", "url", "scan_depth"?}` (an index page; new article links become
items), `{"type": "once", "name", "url"}` (one document, analysed once; versions by content
hash) or `{"type": "linkedin", "name", "slug"}` with names unique within the topic.
Any source may carry `fetch_every` (whole days 1–60): a pinned interval used when `adaptive_fetch` is on.
Any source may carry `site_url` (an http(s) URL): the source's home page, shown on the Sources page next to the feed URL. `ev sources add` fills it when it discovers the feed from a page; `ev sources set <name> --site-url <url|default>` changes it.
Only `http(s)` source URLs are accepted. A typo in a hand-edited file is
reported with file and field and affects only that topic.

Precedence for a setting: `EV_ECHO_<NAME>` environment variable > the topic's
override > `config.json` > built-in default. For `scan_depth`, a source's own
`scan_depth` comes first (then that order); `once` and `linkedin` sources are always `full`.

### Sources

`ev sources add <url>` detects the kind (rss, page or once; `--kind` forces one, `--name`
names it, `--scan-depth` sets it). Without `--name` a source is named after the page title
or host; if that name is taken, host + first path segment, then a number is added.
`ev sources add <slug-or-profile-url> --kind linkedin` adds a LinkedIn profile (a bare slug
or a `linkedin.com/in/<slug>` URL; only the slug is stored; needs the Apify token, see
"Content, copyright and personal data" first). `ev sources set <name> --scan-depth headlines|full|default`
changes a source's depth, `ev sources reset <name>` re-arms a `once` source, forgets a
page's learned link pattern or clears an error count, and `ev sources doctor` probes every
source. `ev backfill [--source N] [--days 365] [--max 200]` is the research spike: it loads
older entries once, with a per-source cap. Pages the engine cannot fetch (blocked, junk) are
handed to the session: `ev fetch-fallback emit` lists them, `ev apply --stage fetch <file>`
takes the results. `ev fetch --all` fetches every source even when adaptive fetch would skip it; `ev fetch --fresh` ignores the fetch cache;
`ev sources set <name> --fetch-every <days|default>` pins or unpins one source's interval. Page
sources read up to 5 index pages while every link is new; feeds where every entry is new are
flagged as a possible gap. Requests are limited to one per second per host; `robots.txt` is not read.

### Overview

`overview.md` lists, per topic, the last run, the number of new findings (studies the newest report showed
for the first time) and the studies most recently reported: with
`overview_studies` (default `10`, `0` = latest report only, also
`EV_ECHO_OVERVIEW_STUDIES`, global only) at most that many, the newest report's
best-graded first, then a line "Earlier" and the studies of older reports, each
linked to the report that first showed it. A newest report with more studies
than that adds "+K more in the report". It is rebuilt after every `report --render` and by `ev overview`; it needs no LLM.
A topic with an invalid `topic.json` shows `setup error: <message>` there.

### Rejections report

Every judge rejection carries a short `reason` (stored with the item), so you can see why an item
did not make the report. With `--rejections` (`ev report --render entries.json --rejections`) or the
setting `rejections_report` on, `report --render` also writes `<YYYY-MM-DD>-rejected.md` beside the
report (same `-2` suffix as its report; an `.html` page too when `html_reports` is on). It lists the
items rejected at triage or by the judge since the previous report (everything for the first one),
with source, title, date, stage and reason. The render output gains `rejections`
(`{"path", "html", "count"}`); a failure is reported as `rejections_error` and never changes the
exit code. The existing `rejected` key (report entries rejected as a whole) is unchanged.

### HTML pages

With `html_reports` on (the default), every `report --render` also writes
self-contained HTML pages: no server, no internet connection, nothing loaded
from elsewhere. Open them in any browser.

- `<YYYY-MM-DD>-report.html` beside each Markdown report (same name, `-2`
  included): the same sections and texts as the `.md`. It opens with a ratings
  chart (grade, study, skeptic's grade, red flags, source check, and a key), then
  one write-up per study: title and summary on the left, a small grade block and
  the named facts (red flags, measured, research, coverage ...) on the right.
  Every page has a black masthead with the breadcrumb trail and ends with a
  footer (back to top, generation time).
- `<id>.html` in the topic's reports folder: every study the topic has
  reported, rewritten on each run. "What the evidence says" groups studies by
  subtopic and question (click a question to open its studies). A closed
  "Filter and sort" box filters by tier, red flag, relation, series, subtopic,
  metric (shown once two or more metrics are in use), entity (shown once a study has one), source (shown once two or more sources have studies) and disputed, searches titles and summaries, and sorts (newest, tier, first
  reported); active fields are highlighted and a reset button appears. A closed
  "Reports (N)" list links every report with its study count.
  Without JavaScript it is a plain list.
- `<id>-sources.html` beside the topic page: one row per source with the studies it produced and
  the entries it fetched (all reports, and the newest report), when it was last fetched, a red
  marker for a source that fails, and links to its site and feed. A source name opens the topic
  page filtered to that source. Studies count once, in the report that first showed them.
- `overview.html` beside `overview.md`: one card per topic with its newest
  studies (same rule as `overview.md`) and links to the latest report and the topic page. `ev overview`
  rebuilds it too.

Times are shown in your local time zone (stored in UTC); report file names keep
the UTC date. Dates that come from articles are shown as written.

Links point only to the studies' sources and to the other pages. The pages
follow the system's light or dark setting. If the HTML cannot be written, the
Markdown report still counts, and the command result names the error.

### How grades are checked

The judge quotes the sentence behind each key number, and the engine checks every quote against the
article text; numbers it cannot find are listed in the report, and an item with no verified number
waits for your review. Tier rules that code can check are enforced: A needs a stated control group,
B a stated sample scale (the report says "capped from A" when a grade was lowered). The skeptic
grades each item again without seeing the judge's grade: one tier apart, the lower grade stands;
two or more apart, the item is disputed. The skeptic also tags methodology red flags (for example
"no baseline" or "confounded window"), and the judge records who ran the study and any conflict of
interest; both are shown next to the grade and never change it.

### What the evidence says

The judge also files each study under one of the topic's subtopics (the topic's `subtopics` list, seeded
into the topic's database on first use; names added to `topic.json` later are added too). When no
subtopic fits it proposes one; the report run asks you to accept, map or reject each proposal, and
until then the study is listed under "Unsorted".

The judge also picks the study's **metric**, the main outcome it counted, from the topic's own metric
list (the topic's `metrics` list, else its template's; `Other` always exists and comes last). Cards read
"Measured: <metric> · <the article's own words>"; for Other only the article's words are shown. A topic
whose only metric is Other (a blank topic) starts every study at Other, and the engine proposes a list
once enough studies have come in (see "Splits" below). `ev metrics list | accept | reject | rename |
merge` mirror `ev subtopics`, and `ev studies metric s<id> <slug>` sets one study's metric yourself.

**Splits.** When one subtopic holds too much of the topic (at least 15 studies and more than 30 %), or
the Other subtopic or the Other metric grows too big (at least 10 studies and more than 20 %), the split
step of a report run (`ev split`, after the group step) proposes parts: new subtopics (or metrics) that
sit side by side with the rest, or moves into existing ones. Nothing moves until you decide: the report
run shows each part with its study count and examples and asks "Accept", "Rename and accept" or
"Reject" (`ev split list`, `ev split accept <group>`, `ev split rename <group> "<name>"`, `ev split reject
<group>`). After an accepted subtopic split it asks whether the source keeps the studies that fit none
of the parts or is retired (`ev split source <proposal> keep|retire`; a retired source's studies are
sorted again by the classify step). A decided source is proposed again only after it has grown by a
quarter; `ev reset --stage split` drops proposals that are still open.

**Entities.** Each study can also be about up to five named things: a product, platform, company, AI model or search feature (ChatGPT, Shopify, Google AI Overviews). The rule is *about, not who ran it*: a study run by a vendor is about that vendor only when it measured the vendor's own product. The judge names them for new studies and the classify step for older ones; new names join the topic's own entity list without asking you, and cards read "About: <names>". The topic page's "Filter and sort" box has an entity filter. Fix the list with `ev entities list | rename | merge | alias | reject | restore`, and a single study with `ev studies entity s<id> add "<name>"` or `remove <slug>`; the engine never undoes those decisions.

The group step of a report run (`ev group`, at most 40 studies per run, repeated up to three times
per run) files each study under a **question**: studies that measure the same thing in the same way
share one, for example "Does updating the publication date raise AI citations?". One question list
covers the whole topic; a study that joins a question of another subtopic moves to that subtopic.
Each question has a measure ("AI citations of the page") and a **kind**:
a *difference* question asks whether the measure is different under A than under B (experiments,
before/after cases, correlations, effects of an event; it states what is compared), a *level*
question asks how much, how often or what share (prevalence, benchmarks, rankings, trends), a
*capability* question asks what a system can do (pass rates, audits). Only a difference question
carries a direction (up, down, no change, mixed) per study and a label, so directions are only
compared between studies that were compared the same way. Studies in Unsorted are never grouped.

The topic page opens with "What the evidence says": per subtopic, a title that opens and closes its
questions (closed at first; a link to the subtopic opens it). All questions sit in one list: those with
studies from
two or more independent organisations first (studies run by the same organisation count once),
each with how many independent studies and articles it has; a difference question also shows what it
compares ("Compared: pages with an updated date vs pages without") and, after the counts, a small
label as a secondary signal: consistent (all in one direction), contested (up and down results) or
varied (any other mix). Level and capability questions have no label. Then the questions
with several studies from one organisation. These questions with several studies are highlighted and
open their study list on click. Then every other study under its question, on the same level (alone on
its question, or "not grouped yet"; the Obsidian note lists these under "Other studies"). A question with one independent study has no label.
A study's card says when it is one study repeated by several articles ("1 study · 4 articles") or
"corroborated by" other independent studies. For contested questions the engine asks two blind
comparers why the results differ (for contested and varied questions: any studies whose results
differ from the question's main result, not only up against down); a question gets one explanation: a short
summary and one line per study outside the main result. A line is shown only when both give the same reason
and its quote is found in that study's text; the summary only when every line passed (the `relate` setting,
on by default; `ev reset --stage relate` asks every question again). The Markdown report adds one line per
study: a `Subtopic: <name>` line and a `Question: <question> · <label> (<n> independent studies)`
line; the question line is left out for a study that is not grouped yet, and its label part for a
study with no label. Labels carry a symbol: ✓ consistent, ⇅ contested, ≈ varied; a legend under the
heading says what they mean. Each question's explanation is one closed "Why the results differ" block: the summary,
the main result, and per study its link, direction, reason, note and checked quote.

Each question has a closed list "Studies, newest first". A line shows the data date (the month or year
the data was collected), or "reported" plus the date of the first report when the data date is
unknown; the study's name, linked to its card; the headline number with a short label, but only
when the code found the number in a quote from the article, and then written as the article
writes it ("no checked number" otherwise); the tier; and "edition N of series" for a repeated study series. A change line
(`Seer Interactive CTR study: −15 % (2024) → −34 % (2025)`) appears only for a repeated series
whose numbers share a label; there is never a change line across different studies. A note says
"N studies wait to be grouped" while shown studies have no question yet; they stay in "Other
studies" until the group step files them.

You can correct the grouping: `ev questions list [--subtopic <slug>]` shows every question with its
studies, kind, comparison and label; `ev questions rename <qid> "<text>" [--measure "<measure>"]
[--kind difference|level|capability] [--comparison "<comparison>"]` renames one (a new measure,
kind or comparison clears its studies' directions, which the next run reads again); `ev questions
merge <qid> <into>` merges two questions of the same kind, also across subtopics; `ev questions move
s<id> <qid>` (or `--new "<text>" --measure "<measure>" --kind <kind> [--comparison "<comparison>"]`)
places a study yourself, also in a question of another subtopic. The group step never undoes such a decision, and
`ev reset --stage group` groups only the studies you did not place.

Studies judged before these fields existed are filled in by the classify step of a report run
(`ev classify`, at most `classify_batch` studies per run; it only fills what is empty and never
changes a tier, facts or a subtopic you decided). `ev subtopics template --adopt | --decline` switches
a topic to its template's current subtopic list: subtopics with the same name (or a rename the template
declares) are carried over, other seeded subtopics are retired, their studies are sorted again by
the classify step, and subtopics you accepted stay. The report run asks you once.

Before a report is written, `ev primary` checks secondary coverage against the original study it
cites (setting `primary_check`: `on` by default, `off` to skip; also per topic or
`EV_ECHO_PRIMARY_CHECK`). Only items of the report being written are checked, each once. The
original is downloaded the same way articles are (no private or local addresses, at most one
request per second per host) and kept per URL; a page too short to be a study counts as a failed
attempt, and after three failed attempts the report says "Original not reachable". The engine
looks for the item's key numbers in the original ("Original: numbers not found — 44%"). Two blind
comparisons then decide whether the article is faithful, overstated, flips the direction, uses the
wrong metric or cherry-picks; a verdict is shown only when both give the same one and their quotes
are found in the two texts ("Original: overstated — …"), and any verdict other than faithful adds
the red flag "misrepresented primary". An original that could not be fetched yet leaves its items
unchecked and the report says "Original not checked (could not be fetched)". The check never
changes a grade. `ev reset --stage primary` (or `--url <original>` for one) forgets failed and
unreachable originals, so the next run downloads them again for the items of the coming report;
items already reported are not checked again (the command counts them as `already_reported`).

### Obsidian export

Set `obsidian_dir` to the root folder of an Obsidian vault (`ev config set obsidian_dir
"~/Documents/My Vault"`; the folder must exist, no vault is created). Every `report --render`
then exports the topic, and `ev export obsidian [--topic ID]` exports on demand (every topic
without `--topic` or `EV_ECHO_TOPIC`; a topic that never ran is skipped). Export is one way:
nothing is read back from the vault.

    <vault>/Evidence Echo/
      .evidence-echo-export.json            which files each topic's export wrote (never edit)
      <Topic name>/_index.md                studies by tier, series, sources, a Dataview table
      <Topic name>/studies/<slug>-s<id>.md  one note per study, with frontmatter
      <Topic name>/series/<series>.md       editions, oldest first
      <Topic name>/sources/<source>.md      the source's studies and its dispute and red-flag counts
      <Topic name>/subtopics/<subtopic>.md  per question: label, counts, studies newest first, one folded explanation callout per question; then other studies

A study note's frontmatter holds `title`, `tier`, `topic`, `relation`, `conducted_by`, `red_flags`,
`disputed`, `series`, `editions`, `first_reported`, `last_reported`, `research_timeframe`, `sources`
and `evidence_echo_id`; a study filed under a subtopic also has `subtopic` and links to its
subtopic note; the `_index.md` table needs the Dataview community plugin. Links use full
vault paths (`[[Evidence Echo/<Topic>/studies/…]]`), so they work only when `obsidian_dir` is the
vault root; the Dataview `FROM` path in `_index.md` assumes the same. A study's file name comes
from its canonical name, so it stays when a report title changes. A topic's folder becomes
`<Topic name> (<id>)` when another topic already owns the cleaned name. Each export rewrites its notes (edits in them are lost) and deletes the notes its previous export wrote that
are gone now (a merged study, a removed source); your own files in these folders are never touched.
`ev export obsidian` exits 2 when `obsidian_dir` is not set.

## Content, copyright and personal data

Evidence Echo is a reading tool for your own use. What it produces is built from other
people's work, so read this before you share it.

- **Others' content.** Reports, HTML pages and Obsidian notes hold titles, excerpts (up to
  800 characters per item) and quotes of the articles they grade, with the source names. The
  articles belong to their authors and publishers. Keep the output private, or check before
  you publish it that your use is allowed: quotation or fair-use rules where you live and the
  terms of each site. Link to the original.
- **Personal data.** Author names, posts and profile content are personal data under the
  GDPR (EU and UK) and similar laws. Private, personal use is generally outside those rules;
  publishing or sharing the output with others is not, and you are then responsible for it.
- **Sources are your choice.** The plugin fetches only the sources you configure and what
  they link to, at most one request per second per host. It does not read `robots.txt`;
  respect each site's terms yourself.
- **LinkedIn sources** (optional) read a public profile's recent posts through a third-party
  scraper on Apify, with your own Apify token and billed to your Apify account. The default
  actor is `harvestapi~linkedin-profile-posts`; a topic can name another in
  `linkedin_apify_actor`. LinkedIn's User Agreement forbids scraping and other automated
  access, so adding such a source is your decision and your responsibility. The actor can
  change or disappear without notice. Evidence Echo is not affiliated with LinkedIn or Apify.
- **Grades are automated.** Tiers, quotes and verdicts come from a language model, with the
  quotes checked against the text by code. They can be wrong: read the original before you
  rely on a result. Topics about health (nutrition, exercise) track research; they are not
  medical, dietary or training advice.
- **No warranty.** The software is provided "as is" under the MIT license.

## CLI

`ev` below stands for `python scripts/ev.py`. The global option `--topic` comes
before the command; without it the engine uses `EV_ECHO_TOPIC`, then the only
topic, and with several topics it refuses (`several topics — pass --topic <id>`).

    ev [--topic ID] fetch | triage | extract | judge | match | verify | embed | report
    ev [--topic ID] apply --stage <triage|judge|match|verify|report> results.json
    ev [--topic ID] apply --stage verify --ruling rulings.json
    ev [--topic ID] classify [--emit]       fill subtopic, metric, date and headline of older studies
    ev [--topic ID] apply --stage classify answers.json
    ev [--topic ID] group [--emit]          file each study under a question of the topic
    ev [--topic ID] apply --stage group answers.json
    ev [--topic ID] split [--emit]          propose parts of an overloaded subtopic or of Other
    ev [--topic ID] apply --stage split answers.json
    ev [--topic ID] split list | accept <group> | reject <group> | rename <group> <name>
    ev [--topic ID] split source <proposal> keep|retire
    ev [--topic ID] relate
    ev [--topic ID] apply --stage relate answers_a.json answers_b.json
    ev [--topic ID] primary [--emit]        check report items against their original studies
    ev [--topic ID] apply --stage primary answers_a.json answers_b.json
    ev [--topic ID] reset --stage primary [--url URL]  fetch failed or unreachable originals again
    ev [--topic ID] report --render entries.json [--rejections]
    ev [--topic ID] studies list [--all] | show <id> | duplicates [--count]
    ev [--topic ID] studies merge <keep> <other> | unmerge <other>
    ev [--topic ID] studies similarity          read-only: what the similarity layer would find; needs a Voyage key
    ev [--topic ID] studies metric <id> <slug>
    ev [--topic ID] metrics list | accept <slug> | reject <slug>
    ev [--topic ID] metrics rename <slug> <name> | merge <slug> <into>
    ev [--topic ID] subtopics list | accept <slug> | reject <slug>
    ev [--topic ID] subtopics map <slug> <target> | rename <slug> <name> | merge <slug> <into>
    ev [--topic ID] subtopics template --adopt | --decline
    ev [--topic ID] questions list [--subtopic <slug>] | rename <qid> <text> [--measure <m>] [--kind <k>] [--comparison <c>]
    ev [--topic ID] questions merge <qid> <into> | move <sid> <qid> | move <sid> --new <text> --measure <m> --kind <k> [--comparison <c>]
    ev [--topic ID] status | reset --stage <name>
    (`reset --stage classify` asks every study again; `reset --stage group` groups every study you did not place yourself again.)

    ev setup status                         configured?, folders, topics, templates
    ev config show                          effective global settings and their origin
    ev config set <key> <value>             e.g. ev config set reports_dir "~/Documents/Evidence Echo"
    ev templates                            available topic templates
    ev topic list | show <id>
    ev topic add <id> --from <template>
    ev topic add <id> --blank --name N --domain D --scope S
    ev topic set <id> <key> <value>         name, domain, scope, subtopics, overrides (not id)
    ev topic remove <id> [--delete-data]
    ev [--topic ID] sources list
    ev [--topic ID] sources add rss <name> <url>
    ev [--topic ID] sources add linkedin <name> <slug>
    ev [--topic ID] sources remove <name>
    ev [--topic ID] sources doctor
    ev overview                             rebuild overview.md, print its path
    ev export obsidian [--topic ID]         export notes to the Obsidian vault (obsidian_dir)

LLM stages print an emit JSON payload in session mode (it carries the topic's
`id`, `name`, `domain`, `scope` and `subtopics`); `apply` validates each result and
prints `{"applied": n, "rejected": [...]}` — rejected items keep their state.
`--ruling` applies your adjudication answers to verified items (the only
way a study's tier changes after grading). `report --render` writes the report
and prints `{"path": ..., "applied": n, "rejected": [...]}`. A run without new
studies writes no report: it prints `{"path": null, "skipped": "no_new_studies",
"warning": ...}`, and new coverage of earlier studies waits for the next report.

`match` gives each graded item its study; Claude is asked only when a study might
already be known. `ev studies list|show|duplicates|merge|unmerge` lists studies,
shows one, finds possible duplicates, and merges or un-merges them (only when you run them).

`ev subtopics list` shows the topic's subtopics (seeded from `topic.json`, accepted proposals, proposals
waiting for review) with their study counts; `accept`, `map` and `reject` settle a proposal, `rename`
changes a subtopic's name (never its slug) and `merge` folds one subtopic into another.
`sources list` also shows per source how many of its items passed the judge (`items`) and how many of
those were `disputed`, `red_flagged`, or flagged as misrepresenting their original
(`misrepresented`); reports do not show these counts.

`ev sources add rss <name> <url>` fetches the URL: a feed is used as is; for an HTML page the
first `<link rel="alternate">` RSS/Atom feed is used (the result says
`discovered_from`); otherwise the URL is stored with the failing status. The
result includes the check status; a failing check warns but still adds.
`sources doctor` probes each RSS source of the topic (fetches nothing into the
DB) and prints per source `status` (`ok`, `http_error`, `not_feed`, `empty`, `blocked`,
`error`; LinkedIn is `skipped`), `http_status`, `entries` and
`latest_entry_date` — run it when `fetch` reports errors or zero counts.

`topic remove` keeps the data: the folder is renamed to
`topics/.removed-<id>-<timestamp>`; `--delete-data` deletes it instead.

Every command prints JSON on stdout; errors go to stderr as `evidence-echo: <message>`.

Exit codes: 0 ok · 1 rejected results file or unwritable output · 2 usage, topic
selection or invalid configuration (also Python < 3.11) · 3 launcher setup
failed · 4 needs setup (`Evidence Echo is not set up — run the setup
(/evidence-echo:setup)` or `no topics yet — run the setup (/evidence-echo:setup)`).
Without `config.json` only `setup status`, `config`, `templates`, `topic`,
`sources`, `status` and `overview` work.

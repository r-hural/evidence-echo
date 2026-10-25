# Changelog

## 0.16.4

- **API keys only from the plugin settings:** the Anthropic, Voyage AI and Apify keys are read
  only from the plugin settings. Keys in your shell's environment (`EV_ECHO_…` or the plain
  variable names) are no longer used, so a key exported for other tools can no longer switch
  grading to paid API runs. If you set a key that way, enter it in the plugin settings instead.
- **Clearer privacy notes:** the plugin README opens with what the plugin does and lists every
  service it contacts, now including Apify for LinkedIn sources and PyPI for the one-time
  setup of its Python packages; SECURITY.md likewise.
- **README:** a "Sample reports" section with the screenshots, each opening its live sample
  page.
- **Icon:** re-encoded at 512 px (smaller file, same picture).

## 0.16.3

- **Pages:** every HTML page's footer has a GitHub icon that links to the project; it stays
  visible in print.
- **README:** a link to the public demo (real reports from the four templates) and two
  screenshots, a topic page and a report.
- **Plugin listing:** the description now says what each study gets, a level of evidence, and
  the plugin keywords match it.

## 0.16.2

- **Similar studies found by meaning:** with a Voyage AI key, study matching also proposes
  studies whose study card reads alike as candidates, so the same study reported under
  different words is caught. Only study cards (the judge's short summary of each item and its title, never
  article text) are sent. `ev studies similarity` shows, read-only, what this would find;
  possible duplicates in `studies duplicates` can carry their similarity. Without a key,
  matching uses the rules alone, as before.
- **Level questions explain large spreads:** when the studies of a "how much / how often"
  question report values far apart (one at least three times the other), the explanation step
  now says why, as it already did for questions where studies disagree on the direction.
  Explanations show each study's value.
- **Primary-source check:** a study's numbers are compared only with the numbers its own
  findings state, so an article that also cites other research no longer looks like it left
  numbers out of the original.
- **Article titles from index pages:** a link card's heading is used as the title, and a
  byline date stuck to the end of the link text is dropped.
- **Markdown reports:** `&` and quotes appear as written instead of HTML entities.
- **Pages:** study cards group their facts (trust, what was tested, where it fits, sources and
  dates); the ratings chart's key folds under "How to read this table" and each chart row
  opens its write-up; the Sources page is one full-width table with Site and Feed links, and a
  source with studies opens the topic page filtered to it; overview rows open the study's card
  in the report that first showed it; topic and overview headers show totals.

## 0.16.1

- **Setup shows settings as a table:** each setting with its current value and what it does,
  instead of raw configuration output.
- **First report window:** every topic added during setup, demo topics included, asks how far
  back the first report should look: 7 days (the default, fastest first report), 21, 90 or 365.
- **Report formats:** setup asks which formats to write, HTML (the default), Markdown and
  Obsidian, in one multiple-choice question. The new `md_reports` setting (`off`) keeps Markdown
  files out of the reports folder while HTML is on.

## 0.16.0

- **Sources page:** every topic gets a Sources page next to its topic page. Each source shows
  how many studies it produced against how many entries were fetched, for all reports and for
  the last one, when it was last fetched, and its site and feed links. Failing or blocked
  sources are marked; a source that fetches fine but delivers nothing gets a quiet note.
  Removed sources stay listed at the bottom while they still have entries.
- **Source filter:** the topic page can be filtered by source; a source's name on the Sources
  page opens the topic page filtered to it.
- **Site links:** a source can carry a `site_url` (the page a person opens) next to the address
  that is fetched. `sources add` fills it when a feed is found from a blog page;
  `sources set --site-url` changes it. The templates include it.
- **Fewer downloads:** fetched pages and feeds are kept for `fetch_cache_minutes` (default 60,
  `0` = off), so a source used by several topics, or a page needed twice, is downloaded once.
  `ev fetch --fresh` skips the saved copies. LinkedIn profiles are cached one by one, so only
  uncached profiles are requested.
- **Overview:** each topic lists its `overview_studies` most recently reported studies (default
  10): the last report's new studies first, then earlier ones under "Earlier". `0` shows only the
  last report. A study counts for the report that first included it.
- **Plugin icon.**

## 0.15.1

- **Setup:** the first setup suggests the demo topics (SEO, AI coding tools, conversion
  optimization, exercise science) in one multi-select question and adds only the ones you pick;
  a second question adds topics of your own. Nothing is added by default.
- **Topic page:** in "What the evidence says" every question sits on one level. A subtopic's
  title opens and closes its questions (closed at first; a link to the subtopic opens it).
  Questions with several studies are highlighted and open their study list on click; a question
  with one study shows it right under the question. The "Other studies" list is gone.

## 0.15.0 (first public release)

Evidence Echo turns the feeds and sites you follow into evidence-graded digests, one topic at a
time. What this first public version does:

- **Topics:** any number, each with its own sources, database and reports. Templates ship
  for SEO, AI coding tools, conversion optimization (A/B tests) and exercise science; any other topic starts blank, described in your own words. A setup skill walks you
  through the first topic, its sources and the report defaults; no API key needed.
- **Sources:** RSS and Atom feeds (a site's advertised feed is found for you), index pages whose
  new article links become items, single documents analysed once, and optionally public LinkedIn profiles (through Apify, with
  your own token; read the README's "Content, copyright and personal data" first). Each source can be read
  as headlines or in full, fetched as often as it publishes, and checked with `sources doctor`.
- **Grading:** articles without a measured outcome are filtered out. The rest get an evidence
  tier (A controlled, B large-scale, C case study, D anecdote) with quotes checked against the
  text, then a blind second grader challenges every grade; disagreements are shown with both
  grades and the reasons.
- **Studies, not articles:** several articles about one study are grouped together, checked
  against the original study they cite, and filed under a subtopic, a question, the metrics it
  measured and the named subjects it is about.
- **Reports:** a result-first Markdown report and HTML pages per run, a topic page ("What the
  evidence says": per question, the studies newest first and how results changed, with an
  explanation for every disagreement), an overview across topics, an optional report of what
  was rejected and why, and an optional Obsidian export.
- **Backends:** grading inside your Claude Code session by default, or through the Anthropic API
  with a key; optional Voyage AI embeddings.

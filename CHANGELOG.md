# Changelog

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

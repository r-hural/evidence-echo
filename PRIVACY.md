# Privacy

Evidence Echo is a Claude Code plugin that runs on your own computer. Its author runs no server
and receives nothing: no account, no analytics, no telemetry.

## What is stored on your computer

- **The data folder** (default `~/.evidence-echo`): your topics and sources, one SQLite database
  per topic with the fetched article text, grades, quotes, author names and, for LinkedIn
  sources, the profile URLs; a short-lived cache of downloaded pages and LinkedIn posts.
- **The reports folder** you choose in setup: reports, topic pages and the overview, which hold
  excerpts, quotes and author names. Also the Obsidian notes, if you turn that export on.
- **The plugin's data folder** (managed by Claude Code): your API keys, one file per Claude Code
  session, written from the plugin settings and removed after 30 days unused; and the engine's
  Python environment.

## What is sent where

Each service is contacted only when you have set it up.

- **The web:** your configured sources and the articles and studies they link to.
- **Anthropic:** article and feed text for grading. With the default setting this happens inside
  your own Claude Code session; with an Anthropic API key in the plugin settings it goes through
  the Anthropic API under that key.
- **Voyage AI** (only with a Voyage key): short study summaries, never article text, to find
  similar studies.
- **Apify** (only with an Apify token and a LinkedIn source): the LinkedIn profile URLs of those
  sources.
- **PyPI:** on first use, the engine's Python packages.

Each service's own privacy policy applies to what it receives.

## Deleting your data

Delete the data folder, the reports folder and, if you use it, the Obsidian export folder.
Uninstalling the plugin removes the plugin's data folder, keys included.

## Personal data in reports

Reports quote other people's articles and name their authors. That is personal data under the
GDPR and similar laws: keep reports private, or check that publishing them is allowed.

Technical details: [SECURITY.md](SECURITY.md).

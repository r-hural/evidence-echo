# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
[private vulnerability reporting](https://github.com/r-hural/evidence-echo/security/advisories/new),
not in a public issue. You will get an answer within a week.

## Supported versions

Only the latest release gets fixes.

## What the plugin sends and stores

- **Anthropic** (only with the `api` backend and an Anthropic API key): feed and article text
  for triage, grading, verification and grouping. With the default `session` backend the same
  text is processed inside your own Claude Code session.
- **Voyage AI** (only with a Voyage key): study cards, for embeddings: the judge's short summary of each item (who ran the study, what was tested, findings, timeframe, sample, title), never article text.
- **Apify** (only with an Apify token and a LinkedIn source): the profile URLs of those
  sources, sent to the topic's Apify actor (default `harvestapi~linkedin-profile-posts`).
- **The web:** your configured sources and the article and study URLs they link to. Only
  public addresses are contacted (every redirect is checked, and the connection goes to the
  checked address), at most 5 MB per response, rate-limited per site. System proxy settings
  are not used.
- **PyPI** (first use, and after an update that changes the dependencies): the engine's
  Python packages, at the exact versions pinned in `constraints.txt`, installed into a private
  environment in the plugin data folder.
- **Local files:** the data folder (default `~/.evidence-echo`: topics, SQLite databases with
  fetched text and grades, and `cache/fetch/` with downloaded pages and LinkedIn posts kept for
  `fetch_cache_minutes`, deleted when they expire or when the cache is turned off), the reports folder, the optional Obsidian export, and the plugin
  data folder (`keys.json`, the engine's Python environment).
- **Keys** are kept only in `keys.json`, written from the plugin settings; keys in your
  environment are not read. They are never printed, put on a command line or passed into the
  session.

Fetched web text is untrusted: the reports escape it, and the plugin's grading agents can only
read files.

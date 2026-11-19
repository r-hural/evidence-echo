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
- **Telegram and your mail server** (only with scheduled report messages set up): a short
  summary of a report (topic, study titles and links, counts, source errors, the report's file
  name; written during the run, like the report itself) and a copy of the report's HTML file, without links to files on your computer, sent to
  your own chat or address only. Only a saved report from the topic's reports folder is
  attached. The bot token goes only into the Telegram API address and the SMTP password only to
  your mail server (TLS with certificate checks); both are removed from error messages.
- **PyPI** (first use, and after an update that changes the dependencies): the engine's
  Python packages, at the exact versions pinned in `constraints.txt`, installed into a private
  environment in the plugin data folder.
- **Local files:** the data folder (default `~/.evidence-echo`: topics, SQLite databases with
  fetched text and grades, and `cache/fetch/` with downloaded pages and LinkedIn posts kept for
  `fetch_cache_minutes`, deleted when they expire or when the cache is turned off), the reports folder, the optional Obsidian export, and the plugin
  data folder (`keys/`, the engine's Python environment). With scheduled runs, also
  `scheduling/` in the data folder (schedules, run log, status, the launcher the OS task starts).
- **Keys** are kept only in one file per session under `keys/`, written from that session's
  plugin settings and removed after 30 days unused (older sessions' files keep their keys until
  then; delete `keys/` to remove them all at once); keys in your
  environment are not read. They are never printed, put on a command line or passed into the
  session.
- **Delivery secrets for scheduled runs:** with a Telegram bot token or an SMTP password in the
  plugin settings, those two (and nothing else) are also kept in `scheduler-keys.json` in the
  plugin data folder, because the OS starts scheduled runs without a session. Every session
  rewrites it from the settings; it is removed once the settings hold neither. Mode 0600 where
  the system supports it.
- **Scheduled report runs** start `claude -p` with nobody watching, in a run folder of its own
  inside the data folder, with only your user settings and no MCP servers. Because the run reads
  untrusted web text, a tool policy limits what it can do: Claude Code's `dontAsk` mode with a
  short list of allowed tools, and a hook that checks every tool call and blocks the call when it
  cannot run. Bash may run only this plugin's engine (`ev.py`), one command at a time, with no
  pipes, redirects, chaining or shell expansion, and only the commands a report of that topic
  needs (it cannot change settings, message channels, sources or topics). Files may be written
  only as `.json` files in the run's `work` folder. Files may be read only from the run's folder,
  the plugin's own files and Claude Code's saved tool output, never key files. The web fetch tool
  reaches only the pages the engine listed, and the engine fetches a study's original only when
  the article itself links it. Only the plugin's own subagents may run, and file search tools are
  off. This narrows what instructions hidden in an article can do; it does not make the model's
  output trustworthy: the report text and the message summary are written by the model. A run
  that looks stuck in a loop is stopped, and every run has a spend or usage limit (README,
  "Scheduled reports").
- **The OS task trusts the data folder:** it runs the launcher and the plugin paths recorded in
  `scheduling/`. Keep the data folder where only you can write (not a shared or synced folder
  others can change).

- **The MCP server** (`find_evidence`, `list_topics`, `get_study`) is local: it opens the topic
  databases read-only, creates and changes nothing, reads no keys and makes no network calls. It
  returns at most a 600-character excerpt of an article, and its instructions tell Claude to treat
  study content as untrusted data.

Fetched web text is untrusted: the reports escape it, the plugin's grading subagents can only
read files, and scheduled runs work under the tool policy above. Error texts of scheduled runs
are shown to you at a session start but are not passed into Claude's context.

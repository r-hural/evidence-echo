---
name: setup
description: Set up or reconfigure Evidence Echo — first-run setup, add or edit a topic, add or remove a topic's sources, change Evidence Echo settings (reports folder, language, fetch window). Use when the user says "set up Evidence Echo", when another Evidence Echo command reported that setup is needed (exit code 4), or when the user wants to monitor a new topic.
---

# Evidence Echo setup

Run every engine command with the Bash tool as:

    python "${CLAUDE_PLUGIN_ROOT}/scripts/ev.py" <cmd>

Below, `ev <cmd>` is short for that line. If `python` is not found, use `py -3` (Windows), then `python3`. The first command may set up the engine (about a minute; run it with a 600000 ms timeout).

Rules:
- Ask one question at a time with AskUserQuestion; offer a recommended option first.
- Change settings only through `ev` commands — never write the JSON files yourself.
- Never ask for a key in chat or on a command line. Keys live in the plugin's settings.
- Source commands are `ev sources add` and `ev sources remove`; `--topic <id>` is a top-level option and goes before the command.
- Exit code 2: show the message (it names the problem) and ask how to fix it.

## 0. Where are we?

`ev setup status` → `configured`, `reports_dir`, `topics`, `templates`, `config_error`.
- `config_error` set: show it; offer to set the reported setting again with `ev config set`.
- Configured: ask what to do — add a topic (steps 3, 4, then 7) / edit a topic's sources (step 4) / change settings (step 5) / done.
- Not configured: steps 1–7. If some steps are already done (e.g. a reports folder or a topic exists), start at the first missing one.

## 1. Welcome

Two lines: Evidence Echo watches sources for a topic, keeps only content with measured results, and grades the evidence. It works without API keys (Claude judges in this session).

## 2. Reports folder

Options: the user's Documents folder + `Evidence Echo` (recommended; resolve the real Documents path, e.g. via `echo ~/Documents` or `$USERPROFILE/Documents`) / inside the data folder (`ev setup status` → `data_dir` + `/reports`) / another folder (ask for the full path).
Run `ev config set reports_dir "<absolute path>"`.

## 3. Topic

`ev templates` → offer each template plus "Blank topic".
- Template: confirm the topic id (default: the template id) → `ev topic add <id> --from <template>`.
- Blank: a blank topic starts with one metric, "Other"; once it has enough studies the engine proposes a metric list (and splits of an overloaded subtopic) during a report, and the user decides then. Ask the user to describe the topic in a sentence or two. Draft: id (lowercase, digits, hyphens), name, domain (a short field name) and scope (what is in and out). Show the draft and let the user correct it → `ev topic add <id> --blank --name "<name>" --domain "<domain>" --scope "<scope>"`.
- With several topics, every later command for this topic carries `--topic <id>` (before the command).

## 4. Sources

`ev --topic <id> sources list` → show names and URLs.
- No sources yet (blank topic): propose 5–10 well-known sources for the topic as a table (name, site URL, why it fits). Ask which to add (AskUserQuestion, multiSelect; more than four candidates → several questions, or let the user name the ones to drop). Add only the chosen ones. Never add sources the user has not chosen.
- Has sources: ask keep all / remove some / add sources.
- Remove: `ev --topic <id> sources remove "<name>"`.
- Add: the user may give a feed URL, a site or blog URL, a single document URL, or a site name (find the URL yourself). Run `ev --topic <id> sources add "<url>"` (optionally `--name "<name>"`). The engine detects the kind: `rss` (a feed, or a page that advertises one), `page` (an index page whose new article links become items) or `once` (one document, analysed once). Pass `--kind rss|page|once` only when the user asks for a kind. LinkedIn: only when the user asks for it, never as a suggestion. Before adding, tell the user in one or two sentences: the posts are read by a third-party scraper on Apify with their own Apify token (billed to them), LinkedIn's User Agreement forbids scraping, so it is their decision, and reports with LinkedIn posts hold personal data and are for private use. Add only after they confirm: `ev --topic <id> sources add linkedin "<name>" <profile-slug>` (needs an Apify token in the plugin's settings to be fetched). A single source's fetch interval can be pinned with `ev --topic <id> sources set "<name>" --fetch-every <days>` (`default` removes the pin).
- If the user asks for suggestions, propose well-known sources for the topic of any kind (feeds, blogs, research pages, yearly reports) the same way as for a blank topic.
- After adding: show a table of every source just added — name, kind (and whether it was detected), URL, `check.status`, and for `page` the `sample_links`. If a page's sample links are not articles, offer: switch to `once` (`ev --topic <id> sources remove "<name>"`, then add again with `--kind once`), try another URL, or remove it. For a source that is not `ok`, say what the check reported and offer to remove it or try another URL.
- Then ask again: keep all / remove some / add more. Move on only when the user confirms the list and at least one source has status `ok`.
- New topic only, after the list is confirmed: ask "Load the past year now (research spike)?" — yes → `ev --topic <id> backfill` (365 days, at most 200 items; the user may choose other limits with `--days` and `--max`). Say it can take a few minutes.

## 5. Defaults

Show language (`output_language`, default en), `fetch_horizon_days` (how old an article may be when fetched, default 7) and `scan_depth` (`headlines`: triage reads titles and excerpts; `full`: every new item's text is loaded first — more thorough, more downloads and tokens) from `ev config show`. Mention `adaptive_fetch` (default off; on means each source is fetched about as often as it publishes, and quiet sources are skipped until due). Mention disputed grades (`ask_on_disputes`, default off: the report shows both grades and the skeptic's reason; on: the report asks you to rule on each). Keep or change → `ev config set output_language <code>` / `ev config set fetch_horizon_days <1-3650>` / `ev config set scan_depth headlines|full` (per topic: `ev topic set <id> scan_depth full`; per source: `ev --topic <id> sources set "<name>" --scan-depth full|headlines|default`) / `ev config set ask_on_disputes on|off` / `ev config set adaptive_fetch on|off`.

Mention `html_reports` (default on: each report also gets an HTML page next to the `.md`, plus a page per topic and `overview.html`; `ev config set html_reports off` turns them off, per topic `ev topic set <id> html_reports off`). Also mention `rejections_report` (`on`/`off`, default `off`; `on` writes `<date>-rejected.md` with every report, listing what the pipeline turned down since the previous one). Then ask whether the user uses Obsidian. Yes → ask for the vault folder and run `ev config set obsidian_dir "<vault folder>"` (it must be an existing folder; the engine never creates a vault; `ev config set obsidian_dir ""` turns the export off). No → leave `obsidian_dir` empty.

## 6. Keys

One line, nothing to do now: adding an Anthropic API key in the plugin's settings (/plugin → Evidence Echo → configure) makes runs faster and unattended.

## 7. Done

Summarize: reports folder, topics with source counts (for a topic added just now, name its sources). Ask "Run the first report now?" — yes → use the `evidence-echo:report` skill for this topic.

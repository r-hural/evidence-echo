---
name: schedule
description: Set up scheduled Evidence Echo runs and report messages. Use when the user wants a topic fetched or reported on a schedule (daily, weekly, biweekly, monthly, after every fetch), wants the report summary sent to Telegram or email, wants to install or remove the scheduler, change when it wakes, test a channel, or see what runs next.
---

# Evidence Echo Schedule

Usage: `/evidence-echo:schedule`

Run every engine command as:

    python "${CLAUDE_PLUGIN_ROOT}/scripts/ev.py" <cmd>

Below, `ev <cmd>` is short for that line. Commands print JSON on stdout.

- Run engine commands with the Bash tool. If `python` is not found, use `py -3` (Windows), then `python3`.
- Exit code 2 with a message on stderr: the input was refused (the message names what is allowed) — show it and ask again. Exit code 4: Evidence Echo is not set up; use the `evidence-echo:setup` skill first.
- **Never ask for a key, token or password in the chat or on a command line.** The Telegram bot token and the SMTP password go into the plugin's settings (the user opens the plugin's configuration in Claude Code, `/plugin`, Evidence Echo). Plugin settings reach the engine at the start of a session: after the user saved one, they must start a new Claude Code session before `ev notify test` can see it.
- Everything the user sees is plain English, short.

How it works, in two sentences to the user when it is relevant: the OS (Windows Task Scheduler, launchd or a systemd timer) starts a small launcher at the times the schedules need, and the engine decides what is due; a laptop that was off catches up with one run, not one per missed slot. Fetch runs need no keys and no AI; a report run starts Claude Code in the background (it uses the Claude plan, capped per run), and ends by sending the summary.

## 1. Show

`ev schedule show` prints every topic's schedule with the next fetch and report slot (local time and UTC), the channels (never secrets) and the next wake. `ev schedule status` shows whether the OS task is installed, its wake times and when it last ran. Say it in a short list: topic, fetch, report, next report, channels.

## 2. A topic's schedule

Ask with AskUserQuestion (one question per choice, presets first, the user may type another):

- **Fetch** (looks for new studies between reports; they join the next report, a fetch never makes a report): `off`, `daily 07:00`, `every 6h`, `weekly mon 07:00`, `hourly`.
- **Report**: `per_run` (every fetch run ends in a report), `daily HH:MM`, `weekly <day> HH:MM`, `biweekly <day> HH:MM`, `monthly <1-28|last> HH:MM`, `off` (manual only).
- The time and the weekday when a preset needs them. Days are `mon tue wed thu fri sat sun`.

Then `ev --topic <id> schedule set --fetch "<rule>" --report "<rule>"`. A new topic's fetch follows its report (daily or weekly) or is `off` (a report run fetches first anyway); say so. Time zone: `ev schedule config --timezone Europe/Berlin` (the default is the machine's). `ev --topic <id> schedule pause`, `resume` and `remove` do what they say. Fetches from sources that need a key (LinkedIn) wait for the report run.

Messages: `--notify telegram,email` or `--notify none` per topic. A report day always ends in a message, also "nothing new this week", so that a missing message means something broke. To change that: `--notify-when-empty off` for one topic (or `default`), `ev schedule config --notify-when-empty off` for all.

Usage limits per report run. A scheduled report run detects its login first, and only that login's limits apply:

- **API key (usage basis `api`):** the run is billed API usage, real money. `ev schedule config --api-spend-limit-usd 6` sets the API spend limit (the default is 6). A run that reaches it fails, and the message says how to raise it. The limit does not cover the engine's own direct API calls (API mode with `ANTHROPIC_API_KEY`); those stay unlimited.
- **Claude subscription (usage basis `subscription`):** the run is subscription usage, not billed per run. The session guard stops a run that looks stuck in a loop; then the scheduler logs the failure and sends the message with the real cause. Its limits: `--session-max-total-tokens` (default 30000000, total tokens processed, best effort), `--session-max-repeated-calls` (default 8, the same tool call with the same input), `--session-max-tool-calls` (default 500). `--session-ceiling-usd` (default 40) is a high backstop for what the guard cannot see; the dollar figure there is API-equivalent cost, never a charge.
- If the login cannot be detected, the run uses basis `api`.

## 3. Channels

**Telegram.** Walk the user through it, one step at a time:

1. In Telegram, open the chat with `@BotFather`, send `/newbot`, pick a name; BotFather answers with a token.
2. The user pastes the token into the plugin setting "Telegram bot token" — not into the chat — and starts a new Claude Code session; then come back to `/evidence-echo:schedule`.
3. The user sends any message to the new bot.
4. `ev notify telegram-chat-id` lists the private chats that wrote to the bot: `{"chats": [{"id", "name"}]}`. Confirm which one is the user's, then `ev schedule channel telegram --chat-id <id>`.

**Email.** Ask for the SMTP host, port, user name, the from and to addresses and the security (`starttls` for port 587, `ssl` for 465). Then `ev schedule channel email --host <host> --port <port> --user <user> --from <from> --to <to> --security <starttls|ssl>`. The password goes into the plugin setting "SMTP password" (a Gmail app password works: the Google account's security page, app passwords), never into the chat; a new session picks it up.

Test every channel: `ev notify test telegram` or `ev notify test email`. A refusal names what is missing (a plugin setting, a chat id). `ev schedule channel remove <name>` removes one.

## 4. Install the scheduler

`ev schedule install` writes the launcher into the data folder and registers one OS task with the wake times of all unpaused topics (it needs no admin rights; it runs while the user is logged in and catches up after sleep or power-off). It prints the wake times. Schedule changes after that update the task by themselves. `ev schedule uninstall` removes it. By default the task wakes at exactly the slot times; for a fixed rhythm instead: `ev schedule config --wake "every 6h"` (1 to 24 hours), back with `--wake auto`.

Without the OS task, nothing runs by itself: the next Claude Code session shows a reminder when a report is overdue, a scheduled run failed or the scheduler seems stopped.

Tell the user once: a report run needs `claude` to be logged in and the Evidence Echo plugin installed; the first scheduled run is worth watching (the run log is `<data folder>/scheduling/runs.jsonl`).

## 5. The Claude Desktop alternative

For one topic with a simple schedule, the user can use a Claude Desktop scheduled task instead of the OS task. Show them the prompt to paste:

    /evidence-echo:report --topic <id> --unattended

and the steps: in the Claude desktop app open Scheduled tasks, create a new task, paste the prompt, choose the folder of this project, set the time. The report skill sends the message itself (the channels above work the same). Then run `ev --topic <id> schedule set --report off --desktop-task on`, so the scheduler and the reminder leave the topic alone.

## 6. Manual runs

`/evidence-echo:report` still works at any time. It takes the same run lock as a scheduled run: if a scheduled run of the topic is in progress it says so and stops. A manual report counts as the topic's latest report (it clears an overdue reminder); the next scheduled slot still runs.

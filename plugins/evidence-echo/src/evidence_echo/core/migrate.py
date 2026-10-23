"""One-time move of 0.3/0.4 data into <data>/topics/<id>/.

0.4 kept <root>/<edition>/{echo.db,sources.json,reports/} in CLAUDE_PLUGIN_DATA
(deleted on uninstall) or ~/.evidence-echo; 0.3 kept echo.db at <root> (SEO data).
echo.db moves last and the guard is topics/<id>/echo.db, so a move that fails
(a locked file on Windows) is retried on the next run. Between drives the db is
copied to echo.db.migrating, verified, then renamed into place: a partial copy
never takes the guard's name. A failed move removes the topic.json it created,
so no command can create an empty echo.db in its place before the retry.
"""
import errno
import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from evidence_echo.core import settings, topics
from evidence_echo.core.settings import ConfigError

LEGACY_EDITION = "seo"
NOT_EDITIONS = {"topics", "reports", "cache", "venv"}
MIGRATING = "echo.db.migrating"
JOURNALS = ("echo.db-journal", "echo.db-wal")  # an unfinished write: copying it would lose data


def _candidates(roots: list[Path]) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        if (root / "echo.db").exists():
            found.setdefault(LEGACY_EDITION, []).append(root)
        for sub in sorted(root.iterdir()):
            if (sub.is_dir() and sub.name not in NOT_EDITIONS and not sub.name.startswith(".")
                    and (sub / "echo.db").exists()):
                found.setdefault(sub.name, []).append(sub)
    return found


def _topic_for(edition_id: str, folder: Path, templates_dir: Path) -> tuple[topics.Topic, str | None]:
    note = None
    try:
        topic = topics.load_template(edition_id, templates_dir)
    except ConfigError:
        topic = topics.Topic(id=edition_id, name=edition_id, domain=edition_id,
                             scope=edition_id, template="blank")
        note = (f"Topic '{edition_id}' has no template: edit its name, domain and scope "
                "(/evidence-echo:setup).")
    src = folder / "sources.json"
    if src.exists():
        try:
            data = settings.read_json_object(src)
            topic = replace(topic, sources=data.get("sources", topic.sources),
                            linkedin_apify_actor=data.get("linkedin_apify_actor",
                                                          topic.linkedin_apify_actor))
        except ConfigError:
            note = f"{src}: unreadable, the template's sources were used."
    return topic, note


def _move_reports(folder: Path, target: Path) -> list[str]:
    """Move regular files only: never a folder, never the target or something containing it
    (the data folder can be the legacy root, so reports/<id>/ may sit inside reports/)."""
    old = folder / "reports"
    messages: list[str] = []
    if not old.is_dir():
        return messages
    target.mkdir(parents=True, exist_ok=True)
    for f in sorted(old.iterdir()):
        if not f.is_file() or f == target or f in target.parents:
            continue
        if (target / f.name).exists():
            messages.append(f"Report left in place: {f} (a report with that name "
                            f"already exists in {target})")
        else:
            shutil.move(str(f), str(target / f.name))
    return messages


def _verified_copy(src: Path, tmp: Path) -> None:
    shutil.copy2(src, tmp)
    if tmp.stat().st_size != src.stat().st_size:
        raise OSError(f"the copy of {src} is incomplete")
    try:
        with closing(sqlite3.connect(tmp.as_uri() + "?mode=ro", uri=True)) as conn:
            check = conn.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.Error as e:
        raise OSError(f"the copy of {src} is not readable: {e}") from None
    if check != "ok":
        raise OSError(f"the copy of {src} failed its check: {check}")


def _move_db(src: Path, dst: Path) -> list[str]:
    """Rename (atomic on one drive); between drives copy, verify, rename, then unlink.
    Any other OSError (a locked file) propagates: the caller retries next run."""
    try:
        os.replace(src, dst)
        return []
    except OSError as e:
        if e.errno != errno.EXDEV:  # Windows "not same device" (WinError 17) maps to EXDEV
            raise
    tmp = dst.with_name(MIGRATING)
    try:
        _verified_copy(src, tmp)
        os.replace(tmp, dst)
    except OSError:
        tmp.unlink(missing_ok=True)  # if this fails too, the next run removes it
        raise
    try:
        src.unlink()
    except OSError as e:
        return [f"The verified copy is in {dst}, but the old copy is still at {src} ({e}). "
                "Delete it when not needed."]
    return []


def rename_retired_settings(data_dir: Path) -> list[str]:
    """Rename retired setting and topic key names (lookback_days, research_horizon_days, themes, ...)
    in config.json and every topic.json, once.

    The renamed key keeps its position; an existing new key wins. A file that cannot be
    read is left alone (the normal load reports it)."""
    messages = []
    paths = [data_dir / settings.CONFIG_FILE] + sorted(
        (data_dir / "topics").glob(f"*/{topics.TOPIC_FILE}"))
    for path in paths:
        if not path.exists():
            continue
        try:
            data = settings.read_json_object(path)
        except ConfigError:
            continue
        renamed = [old for old in settings.RETIRED if old in data]
        if not renamed:
            continue
        kept = {}  # an existing new key wins; else the first old key listed in RETIRED
        for k, v in data.items():
            new = settings.RETIRED.get(k)
            if new is None:
                kept[k] = v
                continue
            winner = new if new in data else next(o for o in settings.RETIRED
                                                  if settings.RETIRED[o] == new and o in data)
            if k == winner:
                kept[new] = v
        data = kept
        settings.write_json_atomic(path, data)
        messages.append(f"Evidence Echo: {path}: renamed {', '.join(renamed)} to "
                        f"{', '.join(settings.RETIRED[o] for o in renamed)}")
    return messages


def run(data_dir: Path, plugin_data_dir: Path, home_dir: Path, templates_dir: Path) -> list[str]:
    roots = list(dict.fromkeys([plugin_data_dir, data_dir, home_dir]))
    topics_dir = data_dir / "topics"
    renamed_messages = rename_retired_settings(data_dir)
    state = settings.read_state(data_dir)
    warned = set(state.get("migration_warned", []))
    done = list(state.get("migrated", []))
    messages, migrated = list(renamed_messages), False
    for edition_id, folders in sorted(_candidates(roots).items()):
        folders.sort(key=lambda f: (f / "echo.db").stat().st_mtime, reverse=True)
        target = topics_dir / edition_id
        if (not topics.ID_RE.match(edition_id) or edition_id in done
                or (target / "echo.db").exists()):
            leftovers = folders
        else:
            source, leftovers = folders[0], folders[1:]
            journal = next((j for j in JOURNALS if (source / j).exists()), None)
            if journal:
                messages.append(f"Could not move {source}: {journal} shows an unfinished "
                                "write; will retry on the next run")
                continue
            topic_file, created = target / topics.TOPIC_FILE, False
            try:
                (target / MIGRATING).unlink(missing_ok=True)  # left by an interrupted copy
                topic, note = _topic_for(edition_id, source, templates_dir)
                if not topic_file.exists():
                    topics.save_topic(topics_dir, topic)
                    created = True
                reports = data_dir / "reports" / edition_id
                report_notes = _move_reports(source, reports)
                report_notes += _move_db(source / "echo.db", target / "echo.db")
            except OSError as e:
                if created:
                    topic_file.unlink(missing_ok=True)
                messages.append(f"Could not move {source}: {e}; will retry on the next run")
                continue
            migrated = True
            done.append(edition_id)
            messages.append(f"Data moved to {target}; reports to {reports}. "
                            "Run /evidence-echo:setup to choose a reports folder.")
            messages.extend(report_notes)
            if note:
                messages.append(note)
        for left in leftovers:
            if str(left) in warned:
                continue
            if edition_id in done:  # a copy of this topic was moved in (verified)
                messages.append(f"Older Evidence Echo data left in place: {left} "
                                f"(topic '{edition_id}' already has data). Delete it when not needed.")
            elif topics.ID_RE.match(edition_id):
                messages.append(f"Older Evidence Echo data left in place (not migrated): {left} — "
                                f"{target / 'echo.db'} already exists and may hold your earlier "
                                "history; keep both until you have checked.")
            else:
                messages.append(f"Older Evidence Echo data left in place (not migrated): {left} — "
                                f"'{edition_id}' is not a valid topic id; keep it until you have "
                                "checked.")
            warned.add(str(left))
    if migrated and settings.load_global(data_dir) is None:
        settings.save_global(data_dir, {})
    changes = {}
    if warned != set(state.get("migration_warned", [])):
        changes["migration_warned"] = sorted(warned)
    if done != list(state.get("migrated", [])):
        changes["migrated"] = done
    if changes:
        settings.update_state(data_dir, **changes)
    return messages

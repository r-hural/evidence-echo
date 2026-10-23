"""Obsidian export: one-way notes per topic (studies, series, sources, an index).

Layout: <vault>/Evidence Echo/<Topic>/{_index.md, studies/, series/, sources/} and one manifest,
<vault>/Evidence Echo/.evidence-echo-export.json, listing per topic the files its last export
wrote (paths relative to Evidence Echo/). Writes only inside Evidence Echo/; deletes only files
the manifest lists for the topic that the new export did not write.
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from evidence_echo.core import knowledge
from evidence_echo.render import evidence_text as et
from evidence_echo.render.markdown import (NOTE_MAX, TITLE_MAX, _cut, counts_text, fact_pairs, metric_text,
                                           quote_text, research_pairs, subtopic_lines)
from evidence_echo.render.markdown import web_url as _web
from evidence_echo.render.model import SeriesSummary, SourceSummary, Topic, TopicStudy

ROOT_NAME = "Evidence Echo"
MANIFEST = ".evidence-echo-export.json"
MANIFEST_VERSION = 1
SLUG_MAX = 60
NAME_MAX = 80
_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]`%$\x00-\x1f\x7f]')  # spec list, code/comment/$, controls
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}  # Windows device names
_YAML_RAW = re.compile("[\x7f-\x9f  ﻿]")  # json.dumps leaves these raw


def study_slug(name: str | None) -> str:
    """Lower-cased, every run of non-alphanumerics -> '-', trimmed, <= 60 chars; 'study' if empty."""
    slug = re.sub(r"[\W_]+", "-", (name or "").lower()).strip("-")
    return slug[:SLUG_MAX].strip("-") or "study"


def clean_name(name: str | None, fallback: str) -> str:
    """A series, source or topic name as a file or folder name (no path separators, ever)."""
    text = _UNSAFE.sub("-", name or "").strip(" .")
    text = text[:NAME_MAX].strip(" .")
    head, dot, rest = text.partition(".")
    if head.strip().upper() in _RESERVED:
        text = f"{head}-{dot}{rest}"
    return text or fallback


def unique_names(names, fallback: str) -> dict[str, str]:
    """name -> file stem; names that clean to the same file (ignoring case) get -2, -3 in name order."""
    out, taken = {}, set()
    for name in sorted(set(names)):
        base = clean_name(name, fallback)
        stem, n = base, 1
        while stem.casefold() in taken:
            n += 1
            stem = f"{base}-{n}"
        taken.add(stem.casefold())
        out[name] = stem
    return out


def topic_folder(topic: Topic, manifest: dict[str, list[str]]) -> str:
    """The topic's cleaned name; 'name (id)' when another topic's export owns that folder."""
    base = clean_name(topic.topic_name, topic.topic_id)
    taken = {p.split("/", 1)[0].casefold() for tid, paths in manifest.items()
             if tid != topic.topic_id for p in paths}
    return base if base.casefold() not in taken else f"{base} ({topic.topic_id})"


def quote(value) -> str:
    """One JSON-quoted string, which is a valid YAML double-quoted scalar (None -> "")."""
    text = json.dumps("" if value is None else str(value), ensure_ascii=False)
    return _YAML_RAW.sub(lambda m: f"\\u{ord(m.group()):04x}", text)


def frontmatter(fields: list[tuple[str, object]]) -> list[str]:
    lines = ["---"]
    for key, value in fields:
        if isinstance(value, (list, tuple)):
            lines.append(f"{key}: [" + ", ".join(quote(v) for v in value) + "]")
        else:
            lines.append(f"{key}: {quote(value)}")
    return lines + ["---"]


GENERATED = ("*Written by Evidence Echo. The next export rewrites this note, so edits here are "
             "lost; keep your own notes in separate files.*")
TIERS = ("A", "B", "C", "D")


def grading_lines(view: dict) -> list[str]:
    """The Markdown report's grading lines, same texts (raw: the caller neutralises them)."""
    return [f"{name}: {value}  " for name, value in fact_pairs(view)]


def _line(text) -> str:
    """One line: newlines and runs of spaces collapsed."""
    return " ".join(str(text or "").split())


def _alias(text) -> str:
    """Text shown inside [[target|alias]]: characters that would end the link or open markup,
    HTML or a comment are replaced; a trailing backslash would escape the closing brackets."""
    text = _line(text).replace("[", "(").replace("]", ")").replace("|", "/").replace("`", "'")
    text = text.replace("<", "‹").rstrip(BACKSLASH)
    text = _escape_math_and_tags(text)
    text = re.sub(r":(?=:)", ": ", text)  # no [k:: v] / (k:: v) Dataview inline field
    return re.sub(r"%(?=%)", "% ", text)


BACKSLASH = chr(92)
_TAG = re.compile(r"#(?=\S)")


def _escape_math_and_tags(text: str) -> str:
    """'$' (Obsidian inline math) and a '#' glued to the next character (a tag) get a backslash."""
    return _TAG.sub(lambda m: BACKSLASH + "#", text.replace("$", BACKSLASH + "$"))
_BLOCK_START = re.compile(r"^(?:[#>\-+*~=|_:]|\d+[.)])")
_ORDERED = re.compile(r"^\d+[.)]")


def _md(text, multiline: bool = False) -> str:
    """Untrusted text made inert in a note body: no code span or fence (so no Dataview code),
    no HTML, no %% comment, no link, image or [key:: value] field, and no line that starts
    a heading, quote, list, rule, fence or table."""
    raw = str(text or "")
    lines = raw.splitlines() if multiline else [_line(raw)]
    out = []
    for line in lines:
        line = line.strip().replace("\\", "\\\\").replace("`", "\\`").replace("<", "&lt;")
        line = line.replace("[", "\\[").replace("]", "\\]")
        line = _escape_math_and_tags(line)
        line = re.sub(r":(?=:)", lambda m: ":" + BACKSLASH, line)  # every pair, also in :::
        line = re.sub(r"%(?=%)", lambda m: "%" + BACKSLASH, line)
        if _ORDERED.match(line):  # CommonMark escapes punctuation, not digits
            line = _ORDERED.sub(lambda m: m[0][:-1] + BACKSLASH + m[0][-1], line)
        elif _BLOCK_START.match(line):
            line = BACKSLASH + line
        out.append(line)
    return "\n".join(out)


@dataclass(frozen=True)
class _Names:
    base: str                   # "Evidence Echo/<folder>": links are vault paths
    studies: dict               # study id -> file stem
    series: dict                # series name -> file stem
    sources: dict               # source name -> file stem
    subtopics: dict = field(default_factory=dict)   # subtopic key -> file stem (SP3b-T)

    def link(self, kind: str, key, alias) -> str:
        stem = getattr(self, kind).get(key)
        if stem is None:  # nothing exported under that key: plain text
            return _md(alias)
        return f"[[{self.base}/{kind}/{stem}|{_alias(alias) or stem}]]"


def _study_stems(topic: Topic) -> dict[int, str]:
    """<slug>-s<id>, the slug from the canonical name (stable when a report title changes)."""
    return {ts.entry.study_id: f"{study_slug(ts.entry.canonical_name or ts.entry.title)}-s{ts.entry.study_id}"
            for ts in topic.studies}


def _subtopic_stems(evidence) -> dict[str, str]:
    """subtopic key -> file stem: the cleaned subtopic name; names that clean alike get -2, -3 (display order)."""
    out, taken = {}, set()
    for sub in evidence:
        if sub.key in out:
            continue
        base = clean_name(sub.name, "subtopic")
        stem, n = base, 1
        while stem.casefold() in taken:
            n += 1
            stem = f"{base}-{n}"
        taken.add(stem.casefold())
        out[sub.key] = stem
    return out


def _study_alias(sid: int, by_id: dict) -> str:
    return by_id[sid].entry.title if sid in by_id else f"s{sid}"


def _row_items(g, names: _Names, by_id: dict) -> list[str]:
    """The question's studies newest first, as Markdown list items, then its change lines."""
    lines = ["", "Studies, newest first:", ""]
    for r in g.rows:
        title = by_id[r.study_id].entry.title if r.study_id in by_id else r.name
        edition = et.edition_text(r)
        parts = [_md(et.date_text(r)), names.link('studies', r.study_id, title), _md(et.headline_text(r))]
        parts.append(_md(r.tier or '?'))
        lines.append("- " + " · ".join(parts) + (f" ({_md(edition)})" if edition else ""))
    if g.changes:
        lines.append("")
        lines += [_md(et.change_text(x)) + "  " for x in g.changes]
    return lines


def _question_callout(g, names: _Names, by_id: dict) -> list[str]:
    """The question's one explanation as a folded callout; every line stays inside the quote block."""
    x = g.explanation
    direction = {members[0]: d for members, d in zip(g.groups, g.group_dirs())}

    def flat(text) -> str:
        return " ".join(str(text or "").split())

    lines = ["> [!note]- Why the results differ"]
    if x.get("summary"):
        lines += [f"> {_md(flat(x['summary']))}", ">"]
    lines += [f"> Main result: {_md(et.direction_text(x.get('main_result')))}", ">"]
    for line in x["lines"]:
        sid = line["study"]
        title = _cut(_study_alias(sid, by_id), TITLE_MAX)
        shown = et.direction_text(direction.get(sid, g.directions.get(sid)))
        lines.append(f"> - {names.link('studies', sid, title)} ({_md(shown)}) — {_md(metric_text(line.get('reason')))}")
        note = _md(_cut(flat(line.get("note")), NOTE_MAX))
        if note:
            lines.append(f">   {note}")
        lines.append(f">   {_md(quote_text(line.get('quote')))}")
    return lines


def _subtopic_note(sub, names: _Names, by_id: dict) -> str:
    lines = [GENERATED, "", f"# {_md(sub.name) or 'Subtopic'}", ""]
    for g in sub.questions:
        label = f" · {_md(et.label_text(g.label))}" if g.label else ""
        compared = et.comparison_text(g)
        lines += [f"## {_md(g.text)}{label}", ""]
        if compared:
            lines.append(_md(compared) + "  ")
        lines.append(_md(counts_text(g)) + "  ")
        lines += _row_items(g, names, by_id)
        if g.explanation:
            lines += ["", *_question_callout(g, names, by_id)]
        lines.append("")
    if sub.others:
        lines += ["## Studies" if sub.key == knowledge.UNSORTED else "## Other studies", ""]
        for o in sub.others:
            title = by_id[o.row.study_id].entry.title if o.row.study_id in by_id else o.row.name
            lines.append(f"- {names.link('studies', o.row.study_id, title)}"
                         + ("" if sub.key == knowledge.UNSORTED else f" · {_md(o.question) or _md(et.NOT_GROUPED)}"))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _subtopic_lines(e, names: _Names) -> list[str]:
    """The Subtopic line (with its wiki link) and, with a question, the Question line; the echo
    goes on the Question line, or on the Subtopic line when there is no question."""
    t = e.subtopic
    pairs = subtopic_lines(t)
    echo = f" · {_md(t['echo'])}" if t.get("echo") else ""
    subtopic = f"Subtopic: {names.link('subtopics', t['key'], t['name'])}"
    if len(pairs) == 1:
        return [subtopic + echo + "  "]
    return [subtopic + "  ", f"Question: {_md(pairs[1][1])}{echo}  "]


def _label(e) -> str:
    """The Markdown report's label: tier (double grade for disputes) · relation · cap · gap note."""
    tier = _md(e.tier or "?")
    if e.disputed and not e.no_quote_only:
        tier = (f"{tier} / skeptic: {_md(', '.join(e.skeptic_tiers))}" if e.skeptic_tiers
                else f"{tier} / skeptic: disputed")
    relation = (e.view.get("provenance") or {}).get("relation")
    extras = [_md(relation) if relation and relation != "unknown" else None,
              f"capped from {_md(e.view['capped_from'])}" if e.view.get("capped_from") else None,
              None if e.disputed or not e.view.get("gap_note") else _md(e.view["gap_note"])]
    return " · ".join([tier] + [x for x in extras if x])


def _study_note(ts: TopicStudy, topic: Topic, names: _Names) -> str:
    e = ts.entry
    prov = e.view.get("provenance") or {}
    sources = sorted({c.name for c in e.coverage if c.name in names.sources})
    fields = [
        ("title", e.title), ("tier", e.tier), ("topic", topic.topic_name),
        ("relation", prov.get("relation")), ("conducted_by", prov.get("conducted_by")),
        ("red_flags", list(e.view.get("flags") or [])),
        ("disputed", "true" if e.disputed else "false"),
        ("series", e.series), ("editions", list(e.editions)),
        ("first_reported", ts.first_reported[:10]), ("last_reported", ts.last_reported[:10]),
        ("research_timeframe", e.research_timeframe),
        ("sources", [names.link("sources", n, n) for n in sources]),
        ("evidence_echo_id", f"s{e.study_id}"),
    ]
    if e.subtopic:
        fields += [("subtopic", e.subtopic["name"])]
    if e.view.get("metric_name"):
        fields += [("metric", e.view["metric_name"])]
    if e.view.get("entities"):
        fields += [("entities", [name for _, name in e.view["entities"]])]
    lines = frontmatter(fields)
    lines += ["", GENERATED, "", f"# {_md(e.title) or '(untitled)'}", "", f"Grade: {_label(e)}  "]
    if e.subtopic:
        lines += _subtopic_lines(e, names)
    if e.disputed:
        prefix = f"Disputed: {_md(e.dispute_reason)}. " if e.dispute_reason else ""
        doubts = "; ".join(_md(n) for n in e.skeptic_notes if n)
        lines.append(f"> {prefix}Skeptic's doubts: {doubts or 'no reason given'}")
    lines.append("")
    if (e.summary or "").strip():
        lines += [_md(e.summary, multiline=True), ""]
    grading = [_md(line) + "  " for line in grading_lines(e.view)]
    if grading:
        lines += grading + [""]
    if e.series:
        lines.append(f"Series: {names.link('series', e.series, e.series)}  ")
    lines += [f"*{name}: {_md(value)}*  " for name, value in research_pairs(e)]
    if _web(e.primary_url):
        lines.append(f"Primary source: <{_web(e.primary_url)}>  ")
    for c in e.coverage:
        source, url = names.link("sources", c.name, c.name), _web(c.url)
        lines.append(f"Coverage: {source}: <{url}>  " if url else f"Coverage: {source}  ")
    lines.append("Reported in: " + ", ".join(_md(d[:10]) for d in ts.reported_on))
    return "\n".join(lines) + "\n"


def _series_note(s: SeriesSummary, names: _Names) -> str:
    lines = [GENERATED, "", f"# {_md(s.name) or 'Series'}", "", "Editions, oldest first:", ""]
    lines += [f"- {names.link('studies', sid, label or f's{sid}')}" for label, sid in s.editions]
    if not s.editions:
        lines.append("*No editions reported yet.*")
    return "\n".join(lines) + "\n"


def _source_note(s: SourceSummary, names: _Names, by_id: dict) -> str:
    lines = [GENERATED, "", f"# {_md(s.name) or 'Source'}", "", "Studies:", ""]
    shown = [by_id[i] for i in s.study_ids if i in by_id]
    lines += [f"- {_md(ts.entry.tier or '?')} · {_md(ts.first_reported[:10])} · "
              f"{names.link('studies', ts.entry.study_id, ts.entry.title)}" for ts in shown]
    if not shown:
        lines.append("*No reported studies.*")
    lines += ["", f"Disputed items: {s.disputes} · Items with red flags: {s.red_flags}"]
    return "\n".join(lines) + "\n"


def _index_note(topic: Topic, names: _Names) -> str:
    lines = [GENERATED, "", f"# {_md(topic.topic_name)} — Evidence Echo", ""]
    if not topic.studies:
        lines += ["*No studies reported yet.*", ""]
    groups = {tier: [] for tier in (*TIERS, None)}
    for ts in topic.studies:  # Topic.studies is newest first; each group keeps that order
        groups[ts.entry.tier if ts.entry.tier in TIERS else None].append(ts)
    for tier, members in groups.items():
        if members:
            lines += [f"## Tier {tier}" if tier else "## Ungraded", ""]
            lines += [f"- {names.link('studies', ts.entry.study_id, ts.entry.title)}"
                      for ts in members] + [""]
    if topic.evidence:
        lines += ["## Subtopics", ""] + [f"- {names.link('subtopics', sub.key, sub.name)}"
                                      for sub in topic.evidence] + [""]
    if topic.series:
        lines += ["## Series", ""] + [f"- {names.link('series', s.name, s.name)}"
                                      for s in topic.series] + [""]
    if topic.sources:
        lines += ["## Sources", ""] + [f"- {names.link('sources', s.name, s.name)}"
                                       for s in topic.sources] + [""]
    lines += ["```dataview",
              f'TABLE tier, relation, red_flags FROM "{names.base}/studies" SORT tier', "```"]
    return "\n".join(lines) + "\n"


def render_notes(topic: Topic, folder: str) -> dict[str, str]:
    """Every note of the topic: path relative to 'Evidence Echo/' -> text."""
    names = _Names(base=f"{ROOT_NAME}/{folder}", studies=_study_stems(topic),
                   series=unique_names([s.name for s in topic.series], "series"),
                   sources=unique_names([s.name for s in topic.sources], "source"),
                   subtopics=_subtopic_stems(topic.evidence))
    by_id = {ts.entry.study_id: ts for ts in topic.studies}
    notes = {f"{folder}/_index.md": _index_note(topic, names)}
    for ts in topic.studies:
        notes[f"{folder}/studies/{names.studies[ts.entry.study_id]}.md"] = _study_note(ts, topic, names)
    for s in topic.series:
        notes[f"{folder}/series/{names.series[s.name]}.md"] = _series_note(s, names)
    for s in topic.sources:
        notes[f"{folder}/sources/{names.sources[s.name]}.md"] = _source_note(s, names, by_id)
    for sub in topic.evidence:
        notes[f"{folder}/subtopics/{names.subtopics[sub.key]}.md"] = _subtopic_note(sub, names, by_id)
    return notes


def _read_manifest(root: Path) -> dict[str, list[str]]:
    """topic id -> files its last export wrote; missing or malformed -> {} (delete nothing).
    Any other read error (a locked file) propagates: rewriting would drop the other topics."""
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) and data.get("version") == MANIFEST_VERSION else None
    if not isinstance(files, dict):
        return {}
    return {tid: [p for p in paths if isinstance(p, str)] for tid, paths in files.items()
            if isinstance(tid, str) and isinstance(paths, list)}


def _inside(root: Path, rel: str) -> Path:
    """root/rel resolved; ValueError unless it lies inside root (root is already resolved)."""
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ValueError(f"refusing to write or delete {path}: outside {root}")
    return path


def _write(path: Path, text: str) -> None:
    """Write via <file>.tmp and a rename; an unchanged file is left alone (keeps its date)."""
    data = text.encode("utf-8", "replace")
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _identity(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_dev, st.st_ino


def _prune(folder: Path, root: Path) -> None:
    """Remove folders the deletions emptied, up to (not including) root."""
    while folder != root and root in folder.parents:
        try:
            folder.rmdir()
        except OSError:  # not empty (a user file) or gone
            return
        folder = folder.parent


def _remove_stale(root: Path, previous: list[str], written) -> tuple[int, list[str]]:
    """Delete files the last export wrote and this one did not -> (removed, failed paths)."""
    kept = set()
    for rel in written:
        try:
            kept.add(_identity(root / rel))
        except OSError:
            pass
    removed, failed = 0, []
    for rel in dict.fromkeys(previous):
        if rel in written or not rel.endswith(".md") or ":" in rel:  # ":" = NTFS data stream
            continue
        try:
            path = _inside(root, rel)
        except ValueError:
            continue  # a manifest entry outside Evidence Echo/ is never followed
        try:
            if not path.is_file() or _identity(path) in kept:
                continue  # gone, a folder, or the note just written under another case
            path.unlink()
        except OSError:
            failed.append(rel)  # locked: stays in the manifest, retried next export
            continue
        removed += 1
        _prune(path.parent, root)
    return removed, failed


def export(topic: Topic, vault_dir: Path) -> dict:
    """Write the topic's notes, delete what its last export wrote and this one did not, then
    record the new list in the manifest (last, so a crash leaves the old list in charge)."""
    vault = Path(vault_dir)
    if not vault.is_dir():
        raise FileNotFoundError(
            f"obsidian_dir {vault} is not an existing folder; Evidence Echo does not create vaults "
            "(ev config set obsidian_dir <vault folder>)")
    (vault / ROOT_NAME).mkdir(exist_ok=True)
    root = (vault / ROOT_NAME).resolve()
    manifest = _read_manifest(root)
    previous = manifest.get(topic.topic_id, [])
    folder = topic_folder(topic, manifest)
    notes = render_notes(topic, folder)
    for rel, text in notes.items():
        _write(_inside(root, rel), text)
    removed, failed = _remove_stale(root, previous, notes)
    manifest[topic.topic_id] = sorted({*notes, *failed})
    _write(root / MANIFEST, json.dumps({"version": MANIFEST_VERSION, "files": manifest},
                                       indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return {"dir": str(root / folder), "written": len(notes), "removed": removed}

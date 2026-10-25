"""Plain-text pieces of the evidence study list, shared by the HTML and Obsidian renderers.

Every value here can come from an article (names, headline numbers and labels), so the callers
escape or neutralise what this module returns. This module does no escaping itself.
"""

LABEL_SYMBOLS = {"consistent": "✓", "contested": "⇅", "varied": "≈"}
NOT_GROUPED = "not grouped yet"      # shown instead of the question for a study without one
DIRECTION_WORDS = {"up": "up", "down": "down", "no_change": "no change", "mixed": "mixed"}


def direction_text(direction) -> str:
    return DIRECTION_WORDS.get(direction, "not read yet")


def result_text(value, kind) -> str:
    """An explanation's result: a direction for a difference question, the value itself for a level
    question (B9: "0.003%")."""
    if kind == "level":
        return str(value) if value else "no value"
    return direction_text(value)


def date_text(row) -> str:
    if row.date is None:
        return "date unknown"
    return f"reported {row.date}" if row.reported else row.date


def headline_text(row) -> str:
    if not row.headline:
        return "no checked number"
    return f"{row.headline} {row.headline_label}" if row.headline_label else row.headline


def edition_text(row) -> str | None:
    return f"edition {row.edition} of series" if row.edition else None


def row_line(row, name: str) -> str:
    line = " · ".join([date_text(row), name, headline_text(row), row.tier or "?"])
    edition = edition_text(row)
    return f"{line} ({edition})" if edition else line


def change_text(change) -> str:
    return f"{change.series}: " + " → ".join(f"{value} ({year})" for value, year in change.points)


def waiting_text(n: int) -> str | None:
    if n <= 0:
        return None
    return "1 study waits to be grouped" if n == 1 else f"{n} studies wait to be grouped"


def comparison_text(group) -> str | None:
    """'Compared: <comparison>' for a difference question that states one."""
    if getattr(group, "kind", None) == "difference" and getattr(group, "comparison", None):
        return f"Compared: {group.comparison}"
    return None


def label_text(label: str) -> str:
    """A question label with its symbol: '✓ consistent' (an unknown label: the word alone)."""
    symbol = LABEL_SYMBOLS.get(label)
    return f"{symbol} {label}" if symbol else label

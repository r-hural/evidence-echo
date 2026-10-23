"""Helpers for step tests: turn today's database back into an older (pre-step-7) one."""

LEGACY_RENAMES = (("studies", "subtopic_id", "theme_id"), ("pair_explanations", "subtopic_id", "theme_id"),
                  ("items", "subtopic_choice", "theme_choice"), ("studies", "match_keys", "entities"))


def to_legacy_names(conn) -> None:
    """Rename the step-7 tables and columns back, as a schema-6 database has them."""
    conn.execute("ALTER TABLE subtopics RENAME TO themes")
    for table, new, old in LEGACY_RENAMES:
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN {new} TO {old}")

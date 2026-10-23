"""Source kinds: one module per kind, looked up by a source's "type" (imported on first use)."""
import importlib

from evidence_echo.core.settings import ConfigError

KIND_MODULES: dict[str, str] = {"rss": "evidence_echo.sources.rss",
                                "linkedin": "evidence_echo.sources.linkedin",
                                "page": "evidence_echo.sources.page",
                                "once": "evidence_echo.sources.once"}


def get_kind(type_):
    if type_ not in KIND_MODULES:
        raise ConfigError(f"source type must be one of {', '.join(sorted(KIND_MODULES))}, "
                          f"got '{type_}'")
    return importlib.import_module(KIND_MODULES[type_])

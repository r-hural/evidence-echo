# Contributing

Thanks for your interest.

- **Issues are welcome:** bugs (please use the bug report form), missing features, sources that
  fail to fetch, and grades you think are wrong (include the article URL).
- **Pull requests** are read, but not merged as they are: this repository is published from a
  separate development repository, so the maintainer re-applies a good change there and credits
  you in the changelog. Small, focused pull requests with a test are the easiest to take over.
- **Security issues:** see [SECURITY.md](SECURITY.md); please do not open a public issue.

## Running the tests

Python 3.11 or newer:

    python -m venv .venv
    .venv/bin/python -m pip install -e "plugins/evidence-echo[dev]"    # Windows: .venv\Scripts\python
    .venv/bin/python -m pytest -q

The default run is offline. `pytest -m network` checks real fetching; `pytest -m slow` builds the
engine's environment from scratch.

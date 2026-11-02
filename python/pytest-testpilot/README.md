# pytest-testpilot

pytest plugin that records test outcomes and per-test line coverage into the shared TestPilot SQLite database.

```bash
pip install -e .
# optional xdist support:
# pip install -e '.[xdist]'
pytest --testpilot
# DB path (use equals form — a bare path arg changes pytest rootdir):
pytest --testpilot --testpilot-db=/abs/path/to/.testpilot/db.sqlite
```

Paths and the default DB location are resolved from `git rev-parse --show-toplevel`.

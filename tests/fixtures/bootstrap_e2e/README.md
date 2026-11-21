# bootstrap_e2e fixture

Tiny pytest package for `tests/e2e/test_bootstrap_e2e.sh`.

The e2e script copies this tree into a temp git repo, makes a short first-parent
history with real `pkg/calc.py` edits (binop sites for mutate), then runs
`testpilot bootstrap` with small N/K. Not a committed `.git`.

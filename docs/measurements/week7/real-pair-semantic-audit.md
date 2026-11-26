# PR-intermediate semantic audit

Independent read-only review of all 26 function-audited positive changes. The reviewer inspected first-parent/source diffs, base-to-head diffs, commit subjects/bodies, and GitHub PR metadata where available. No tests were rerun during this audit; stable positive outcomes are inherited from the saved four-run JUnit evidence in the full-history run.

## Decisions

| Repository | PR group | Decision | Source change and relationship to observed failures |
| --- | --- | --- | --- |
| attrs | 1488 | valid candidate | `_make.py`: cached-property behavior changes to a descriptor; affected behavior is direct. |
| attrs | 1511/1512 | valid candidate | `_make.py`: cached-property slots and doc-property behavior; shared heads are grouped. |
| attrs | 1516 | valid candidate | `_make.py`: slot metadata representation changes from tuple to dict; directly affects slot construction. |
| attrs | 1533 | valid candidate; correlated | Validator exceptions gain index/key prefixes; overlaps issue #1245 and the sole failing test with #1554. |
| attrs | 1554 | valid candidate; correlated | Same issue #1245 and same failing test as #1533; do not treat as independent evidence. |
| click | 51 | valid candidate | Eager-help parameter ordering changes and can affect option/list ordering. |
| click | 352 | valid candidate | Long-argument wrapping changes the exact formatter behavior under test. |
| click | 765 | valid candidate; correlated | `main`/`ctx.exit` return behavior; shares six failures and behavior with #849. |
| click | 849 | valid candidate; correlated | Same return-value behavior and six failing tests as #765; do not treat as independent evidence. |
| click | 1222 | valid candidate | Loose option ordering changes parser outcomes directly. |
| click | 1280 | valid candidate | `Choice(show_index)` changes choice rendering/lookup and completion behavior. |
| click | 1364 | valid intermediate | First commit uses undefined `tries`; next PR commit fixes the variable. This is a real transient PR defect. |
| click | 1368 | valid candidate, WIP/RFC | Explicitly unfinished; changes `open_stream`/`fdopen` error handling implicated by the failures. |
| click | 1392 | valid candidate | MSYS2/platform branch and `sysconfig` import; implicated lightweight import contract is related. |
| click | 1489 | valid intermediate | First commit changes no-command group exit from 0 to 1; later PR commits replace/fix behavior. |
| click | 1504 | valid candidate | Bash completion switches from static params to context params in option/value completion. |
| click | 2622 | valid candidate | Extracts the `Choice` error-message path under test. |
| click | 2767 | valid candidate | Changes Context lifetime/cleanup in shell completion paths. |
| click | 2796 | valid candidate | Changes `Choice` normalization and generic metavar signatures implicated by tests. |
| click | 3833 | valid candidate | Safe repr changes error text/escaping; one positive is in an unchanged test function even though that test file changed. |
| click | 3872/3873 | **exclude** | Both PRs are titled “spam”, closed, and unmerged. The increment chain is non-substantive; the head contributes 84 failures and is excluded from training and evaluation. |
| click | 422 | valid candidate | Option separator parsing changes directly; changed test file does not mean the positive test function changed. |
| click | 446 | valid candidate | Auto-trim short-help behavior changes; the changed test file has another, non-positive function. |
| click | 868 | valid intermediate | First commit wires stdout/stderr incorrectly (`stdout` reads stderr bytes and passes unsupported `mix_stderr`); later commit fixes it and adds tests. |
| jinja | 2242 | valid candidate | Safe repr in compiler changes constant/template-data representation directly. |
| jinja | 2256 | **uncertain; withheld from primary** | LRUCache behavior directly relates to failures, but the PR is titled `[rejected AI]`, closed, and unmerged. The source's eligibility as a “real” source is unclear. |

There are 24 semantically plausible candidates, one excluded spam group, and one uncertain rejected-AI PR. The primary manifest conservatively contains 24 rows (click 18, Jinja 1, attrs 5); the uncertain row and excluded row remain in separate JSONL files for auditability.

## Dependence caveat

The registered sampling unit is a unique failing-test set inside a PR group. Two pairs of separate PR groups are correlated: attrs #1533/#1554 refer to issue #1245 and the same test; click #765/#849 share six failures and the same return-value behavior. They remain separate under the pre-registered grouping rule, but effective independent evidence is lower than the row count suggests.

Several included candidates are unfinished intermediate commits. This is intentional for the PR-intermediate design: the outcomes represent observed intermediate states, not merged releases. The report keeps that distinction explicit.

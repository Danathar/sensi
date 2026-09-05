# GitHub label descriptions are capped at 100 characters

**Symptom** — the triage workflow's first real run failed with
`'tier/runtime' not found` from `gh pr edit --add-label`. The label had just
been created two lines earlier.

**Why the wrong answer looked right** — the visible error points at the
add-label call, so the obvious diagnosis is a permissions or timing problem
with label creation. It is neither. The real error was one line earlier:
`gh label create` returned `HTTP 422: description is too long (maximum is 100
characters)` and the `|| true` on the end of that pipeline swallowed it, so the
label never existed.

**Rule** — GitHub rejects a label description over 100 characters. Keep label
text short and put the prose somewhere a human reads it; `.github/risk-tiers.yml`
carries a short `label_description` for the label and a long `description` for
the file, and `scripts/classify_pr.py` rejects an over-long one at load time so
the failure names the tier instead of arriving as an HTTP 422 in CI.

The generalisation is the more useful half: **`|| true` on a command whose
output the next step depends on converts a clear error into a confusing one.**
Tolerate a failure only where the next step does not care about the result.

Also worth knowing: a `pull_request_target` workflow runs the workflow file and
any checked-out scripts from the *base* branch. A fix to that workflow cannot go
green on the pull request that contains it - verify it against the real API by
hand, then merge, then confirm on the next pull request.

**Source** — `.github/workflows/labeler.yml`, first run on PR #46.

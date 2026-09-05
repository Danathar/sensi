# Tooling and CI commits are not `feat:`

**Symptom** — a pull request that added end-to-end tests, a coverage gate, a
ruff config and contributor docs was committed as
`feat: add the L0 prerequisites ...`.

**Why the wrong answer looked right** — the change adds a lot, and "feature"
is the natural English word for it.

**Rule** — `.releaserc` drives `jossef/action-semantic-release-info`, which
computes the released version of the *integration* from commit prefixes.
`feat:` produces a minor bump and `fix:` a patch bump, both of which appear in
the HACS release notes users read. A tooling change that bumps 2.1.6 to 2.2.0
tells every user something shipped when nothing did.

Reserve `feat:` and `fix:` for user-visible integration behaviour. Everything
else — CI, lint config, tests, docs, agent instructions — is `ci:`, `test:`,
`docs:`, `refactor:` or `chore:`.

The release workflow is `workflow_dispatch` only, so a wrong prefix is not an
immediate incident; it is still wrong at the next manual release.

**Source** — PR #39, amended before merge.

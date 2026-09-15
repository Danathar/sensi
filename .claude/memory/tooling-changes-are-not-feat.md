# Tooling and CI commits are not `feat:`

**Symptom** — a pull request that added end-to-end tests, a coverage gate, a
ruff config and contributor docs was committed as
`feat: add the L0 prerequisites ...`.

**Why the wrong answer looked right** — the change adds a lot, and "feature"
is the natural English word for it.

**Rule** — the prefix no longer decides the version. `.github/workflows/release.yml`
tags the CalVer number already committed in
`custom_components/sensi/manifest.json`, and states the reason in its own
header: "The version is never derived from commit messages." What the prefix
still decides is how the change reads in the release notes GitHub generates
(`generate_release_notes: true`), which is the list a user is shown when HACS
offers them the update. A tooling change filed as `feat:` is announced there as
something shipped when nothing did.

Reserve `feat:` and `fix:` for user-visible integration behaviour. Everything
else — CI, lint config, tests, docs, agent instructions — is `ci:`, `test:`,
`docs:`, `refactor:` or `chore:`. The same table is in `AGENTS.md`, "Commits
and pull requests"; this file records why the wrong answer looked right, not a
second copy of the rule.

A wrong prefix is not an immediate incident — the release workflow runs monthly
on a schedule and on `workflow_dispatch`, and neither reads the commit log. It
is still wrong in the notes at the next release.

**Source** — PR #39, amended before merge.

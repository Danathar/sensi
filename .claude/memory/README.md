# Correction memory

Corrections that came out of real work on this repository — the things a
capable agent gets wrong here without being told, and would get wrong again.

This is not a changelog and not a design document. Each file records one
mistake that was actually made, why the wrong answer looked right, and the rule
that prevents it. If a correction is not likely to recur, it does not belong
here; if it is already stated in `AGENTS.md`, link to it rather than restating.

## Format

One Markdown file per correction, named for the mistake:

```markdown
# <what went wrong, in one line>

**Symptom** — what it looked like when it happened.

**Why the wrong answer looked right** — the reasonable inference that led there.

**Rule** — what to do instead.

**Source** — where this came from (PR, review comment, CI failure).
```

## Adding to it

Add a file when a human corrects you on something specific to this codebase and
the correction generalises. Do not add one for a one-off typo, and do not add
one for something already covered in `AGENTS.md` or `CONTRIBUTING.md`.

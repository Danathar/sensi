# Pull request metrics

What gets measured about changes to this repository, why, and how to reproduce
the numbers.

## Why measure acceptance at all

An increasing share of the changes here are agent-authored. The useful question
is not "did the agent produce a diff" but **"was the diff taken, and what did it
cost to get it there"** — acceptance rate, time to merge, and how many review
rounds it needed. An agent that opens twenty PRs of which three merge is worse
than one that opens four of which four merge, and only this measurement tells
them apart.

The metrics are deliberately about *outcomes*, not activity. Lines written,
files touched, and PRs opened are not tracked as goals.

## How to get the numbers

```bash
python3 scripts/pr_metrics.py                 # last 50 closed PRs
python3 scripts/pr_metrics.py --limit 200
python3 scripts/pr_metrics.py --since 2026-01-01
python3 scripts/pr_metrics.py --json          # for piping somewhere
```

Reads through `gh`, so it uses whatever `gh auth status` reports. No third-party
packages.

## What each column means

| Column | Definition | What a bad value looks like |
| --- | --- | --- |
| **Proposed** | closed PRs in the window | — |
| **Merged** | of those, merged | — |
| **Closed unmerged** | of those, abandoned or rejected | rising share = work is being wasted upstream of review |
| **Acceptance** | merged / proposed | below ~70% means changes are being proposed before they are understood |
| **Median h to merge** | open → merge, hours | rising with constant size = review is the bottleneck |
| **Median reviews** | review submissions before merge | consistently 0 means nothing is being reviewed, not that everything is perfect |
| **Median lines** | additions + deletions of merged PRs | large *and* fast is the combination to worry about |

Rows are split by author: `all`, `human`, and `bot` (GitHub Apps and `[bot]`
accounts). The split is the point — a single blended acceptance rate hides the
thing worth knowing.

## Reading it honestly

**Acceptance rate is not a quality score.** A repository with one maintainer who
merges their own work reads at 100% regardless of how good the work is. On this
repository the honest reading today is "self-merged, so acceptance says nothing
yet" — it becomes informative once changes are proposed by someone who is not
the person merging them.

**Zero reviews is a real signal, not a clean bill of health.** It means the
automated gates — `pytest`, the 90% coverage gate, `ruff`, hassfest, HACS
validation — are the only reviewer. That is a deliberate trade-off for a
single-maintainer fork, and it is why those gates are not optional.

**Time to merge on a solo repository measures nothing but attention.** Do not
optimise it.

## Baseline

Taken at the point this file was added, over all closed pull requests:

| Author | Proposed | Merged | Closed unmerged | Acceptance | Median h to merge | Median reviews | Median lines |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all | 7 | 7 | 0 | 100% | 0.1 | 0 | 423 |
| human | 7 | 7 | 0 | 100% | 0.1 | 0 | 423 |

Regenerate rather than trusting this table — it is a snapshot, and it is here so
a later reading has something to compare against.

## Related

- [`docs/quality.md`](quality.md) — the code-quality signals (coverage, CI, lint)
- [`docs/review-rubric.md`](review-rubric.md) — what a review is supposed to check

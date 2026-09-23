# Protecting `master`

`master` is the default branch. Until the ruleset below was applied on
2026-09-08 it was unprotected: no branch protection, no rulesets. Every gate in
this repository — CI, the coverage threshold, the review rubric, the rules
agents are given in `docs/SECURITY-AI.md` — sat downstream of a pull request
that nothing forced anyone to open.

That was the gap [#109](https://github.com/Danathar/sensi/issues/109) closed,
and it matters more here than in a repository maintained by hand: Hive runs
agents against this one continuously. Hive's own policy is one layer. GitHub
refusing the push is a second, independent one, and independence is the point —
a layer that the thing it constrains can edit is not a layer.

`.github/rulesets/master.json` is the agreed ruleset, committed so that what is
enforced can be reviewed in a diff like anything else.
`scripts/check_ruleset.py` reports whether GitHub is actually enforcing it.

## What the ruleset does

| Rule | Effect |
| --- | --- |
| `pull_request` | Direct pushes to `master` are refused. Changes arrive as pull requests. |
| `required_status_checks` | Six checks must pass before merge (below). |
| `non_fast_forward` | Force pushes are refused, so history cannot be rewritten in place. |
| `deletion` | The branch cannot be deleted. |

Conditions target `~DEFAULT_BRANCH` rather than the literal name `master`, so
the rule follows the default branch if it is ever renamed instead of quietly
protecting nothing.

**`bypass_actors` is empty.** No app, team or role is exempt, including the
repository owner. That is deliberate: a bypass is invisible from every other
view — the rules still read as enforced, for everyone except whoever holds it.
If something genuinely cannot work through a pull request, the fix is to change
that thing, not to add an actor here. `check_ruleset.py` treats an added bypass
actor as drift and fails on it.

### The six required checks

`pytest (Python 3.14)`, `line coverage >= threshold`, `ruff`, `hassfest`,
`HACS`, and `manifest requirements match requirements_component.txt`.

All six run unconditionally on every pull request, which is the property that
makes a check safe to require. Three that also appear on a pull request are
deliberately **not** required:

- **`publish badge and trend`** runs only on a push to `master`
  (`coverage-gate.yml` guards it with an `if:`). Requiring a check that never
  reports on a pull request leaves every pull request waiting for it forever.
- **`path labels`** and **`risk tier and size`** classify a change; they do not
  verify it. Making a labelling job a merge blocker means a labelling outage
  blocks every merge, and buys nothing a reviewer was relying on.

Adding a required check means editing `master.json` *and* re-applying it. A
check added to CI is not automatically required.

## The release workflow, and why it had to change first

The ruleset could not be applied while `release.yml` committed the version bump
and pushed it straight to `master`. With the monthly scheduled run armed by the
`AUTO_RELEASE_ENABLED` repository variable, applying it would not have produced
a warning — it would have produced a failed release on the 1st, and the
obvious-looking fix would have been a bypass actor for Actions, which hands back
most of what the ruleset is for.

[#116](https://github.com/Danathar/sensi/pull/116) restructured the release
workflow so it tags an already-approved commit instead of pushing one, and the
version moves through an ordinary pull request. It merged before the ruleset
was applied. That was the dependency #109 named, and it was the whole of it:
nothing else in the repository wrote to `master` outside a pull request.

`release.yml` now pushes two things, and neither is `master`: the `prepare` job
pushes a `release/<version>` branch and opens the version-bump pull request
from it, and the `release` job pushes a tag. Keep it that way. A step that
pushes to `master` again fails on the next scheduled run, and the fix is to
change that step, not to add a bypass actor.

## Review requirements, and why they are set where they are

`required_approving_review_count` is **0** and `require_code_owner_review` is
**false**. Both look wrong for a security ruleset, and both are deliberate.

GitHub does not let anyone approve their own pull request. With a single
maintainer who is also the author of most changes, requiring one approval does
not mean "reviewed" — it means **nothing can ever be merged**, including the
change that would relax the rule again. The realistic outcome of that is a
bypass actor added in frustration, which is worse than where this started.

What is enforced instead is the part that does work single-handed: a change
cannot reach `master` without a pull request, and cannot merge without the six
checks. A person still presses merge. For agent-authored work — which is most
of it — that human merge is the approval, and it is mechanical rather than
conventional because the alternative (pushing) is refused.

**When a second reviewer exists**, two fields make it real, and they are the
only edit needed:

```json
"required_approving_review_count": 1,
"require_code_owner_review": true
```

`.github/CODEOWNERS` is already committed and already lists the control-plane
paths — workflows, the ruleset, the AI policy, `AGENTS.md`,
`custom_components/sensi/auth.py`. Until code-owner review is turned on, that
file auto-requests review rather than requiring it. Committing it now is still
worth doing: agreeing which paths are the control plane is a separate act from
enforcing it, and the list is the part worth reviewing.

## Checking it, and applying it elsewhere

```bash
# Validate the committed definition without contacting GitHub:
python3 scripts/check_ruleset.py --offline

# Verify GitHub agrees with the file:
python3 scripts/check_ruleset.py
```

The second command is the one worth repeating. A ruleset is repository configuration,
not a file, so nothing in a checkout proves what is being enforced — and the
changes that matter here (a bypass actor, enforcement switched to `disabled`, a
required check dropped) leave no trace in any diff. Run it after any change to
repository settings, and consider running it on a schedule; it needs admin on
the repository to read rulesets.

To change the ruleset, edit `master.json`, open a pull request, and after it
merges re-apply with `--method PUT repos/…/rulesets/{id}`. The ruleset already
exists on this repository, so a `POST` here would add a second one beside it.
A repository that does not have it yet — a fork, say — applies it once with:

```bash
gh api --method POST repos/<owner>/<repo>/rulesets \
  --input .github/rulesets/master.json
```

## What this does not cover

Rulesets condition on refs, not paths, so "changes to `.github/workflows/**`
need a stricter review than changes to a docstring" cannot be expressed in the
ruleset itself. `CODEOWNERS` does the path scoping and the ruleset makes it
mandatory or not — that is the whole mechanism available, and it is why the
control-plane list lives in `CODEOWNERS` rather than here.

Tags are not covered. This ruleset targets branches, and only the default
one; the release workflow's tag push needs the first, and the `release/*`
branch it opens the version-bump pull request from needs the second. A
separate tag ruleset would be a reasonable follow-up if tag rewriting ever
becomes a concern.

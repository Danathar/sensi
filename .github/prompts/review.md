# Review a pull request

Review a change to the Sensi Home Assistant integration against
[`docs/review-rubric.md`](../../docs/review-rubric.md). Read `AGENTS.md` first.

## Input

The diff, plus the PR description. `gh pr diff <number>` and
`gh pr view <number>` produce both.

## Do this

1. **Do not re-check what CI already checks.** Formatting, lint, coverage
   threshold, manifest validity and HACS metadata are gated. A review that
   reports them has spent itself on nothing.

2. **Work the rubric in priority order** and stop escalating once you have a
   blocking finding:

   1. credential exposure — tokens, refresh tokens, real `icd_id` values in
      code, logs, fixtures, or the PR description
   2. breaking existing installs — entity `unique_id`, config flow shape,
      stored credential shape, hand-edited `manifest.json` version
   3. payload handling — direct indexing instead of `.get()`, a parser that can
      raise, an assumed field with no fixture, a response shape change that
      drops support for the old shape
   4. async and lifecycle — blocking I/O on the loop, unawaited or uncancelled
      tasks, futures not resolved on the error path, wrong choice between
      `ConfigEntryAuthFailed` and `ConfigEntryNotReady`
   5. capability gating — a new entity that will be permanently broken on models
      lacking the feature
   6. tests — right tier, asserts an effect rather than just executing lines,
      a bug fix that actually fails without the fix
   7. fit — layering, style, and an honest commit prefix

3. **Every finding needs a concrete failure case.** Name the input or sequence
   and the resulting wrong behaviour. If you cannot construct one, it is a
   comment, not a defect. Delete findings you cannot substantiate rather than
   softening them.

4. **Verify the claims in the description.** If it says a test fails without the
   fix, check that it would. If it says behaviour is unchanged, check the diff
   agrees.

5. **Say what you could not check.** No thermostat exists in CI and the protocol
   is undocumented. Anything whose correctness depends on what Sensi actually
   sends is unverifiable here — state it rather than guessing either way.

## Output

For each finding: the file and line, the severity from the rubric, the concrete
failure case, and the fix.

Then one verdict — **Block**, **Request changes**, **Comment** or **Approve** —
and the single reason for it. If nothing survives, say so plainly; a review with
no findings is a valid result and should not be padded.

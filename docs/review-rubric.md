# Pull request review rubric

What a review of this repository is supposed to check, in priority order. The
automated gates already cover formatting, lint, coverage and manifest validity —
do not spend review on those. Spend it on what CI cannot see.

Use it as a human, or hand it to an assistant via
[`.github/prompts/review.md`](../.github/prompts/review.md).

## Priority order

Work down. A finding at a higher level outranks anything below it.

### 1. Credential exposure — blocking, always

- Does any diff hunk introduce a token, a refresh token, or a real `icd_id` into
  source, a log line, a docstring, a fixture, or the PR description itself?
- Does new logging print a value that could be token-shaped without going
  through `redact_token`?
- Does a new test fixture come from a real account, and was it scrubbed
  (`icd_id`, `serial_number`, `unique_hardware_id`, `wifi_mac_address`, the
  `registration` address fields)?

This has happened in this repository's history. It is the one category where
"probably fine" is not an acceptable review outcome.

### 2. Breaking existing installs — blocking unless deliberate and stated

- Does an entity `unique_id` change? Existing users get orphaned entities and a
  duplicate set.
- Does the config flow change shape, or the stored credential structure?
- Does `manifest.json` `version` change by hand? The release workflow owns it.
- Do `manifest.json` `requirements` and `requirements_component.txt` still
  agree?

If any of these are intended, the PR must say so in the *Risk* section. Silence
is the defect.

### 3. Payload handling — the most common real bug here

The Sensi protocol is reverse engineered and undocumented. Ask:

- Is any payload field read with `[...]` rather than `.get(..., default)`?
- Would a missing or `null` field raise, or degrade to unknown? Raising in a
  parser takes the whole integration down, not one entity.
- Are `to_bool` / `to_int` / `to_float` used for values that arrive as strings?
- If the change assumes a new field exists — is it actually present in
  `tests/sample.json`, or was it inferred? Inferred fields need a captured
  fixture, not a hand-written dict.
- Does the change handle both the old and new shape when a response format
  changed? Users run older firmware.

### 4. Async and lifecycle correctness

- Any blocking I/O on the event loop — `open`, `requests`, `time.sleep`?
- Is every `asyncio` task that is created either awaited or cancelled on
  teardown? `client._emit_loop` is a background task; anything similar needs the
  same treatment in `stop()`.
- Are futures resolved on *every* path, including the error path? A never-resolved
  future in `_futures` is a slow leak and a hung setter.
- Does new connection handling survive a disconnect mid-session, and a token
  that expires mid-session? Those are the two failure modes users actually hit.
- Is `ConfigEntryAuthFailed` raised where reauth is the right outcome, and
  `ConfigEntryNotReady` where a retry is?

### 5. Capability gating

- Is a new entity gated on the matching capability in `capabilities.py`, or will
  it appear permanently broken on models that lack the feature? The aux heat
  switch is the pattern.

### 6. Tests

- Does new behaviour have a test? Does a bug fix have a test that fails without
  the fix — verify this by reading it, not by trusting the description.
- Is it at the right tier? Pure logic in `tests/test_<module>.py`; anything
  touching the connection, emit loop, coordinator or entity lifecycle in
  `tests/e2e/`.
- Does the test assert on an *effect*, or does it just execute lines? A test
  that raises coverage without asserting is worse than no test — it makes the
  gate lie.
- For a setter, does the assertion check the emitted wire payload, not only the
  resulting entity state?

### 7. Fit with the codebase

- Does it match the surrounding style, or import a different idiom?
- Is new logic in the layer that owns it — payload shape in `data.py` /
  `capabilities.py` / `event.py`, not leaked into a platform module?
- Is the commit prefix honest? `feat:` and `fix:` bump the version users see in
  HACS; tooling and docs must not use them.

## Verdicts

| Verdict | Means |
| --- | --- |
| **Block** | anything in §1 or §2, or a parser that can raise on a missing field |
| **Request changes** | a real defect in §3–§6 with a concrete failure case |
| **Comment** | style, naming, a suggestion the author can reasonably decline |
| **Approve** | nothing above survives, and the *How it was verified* section is filled in |

## What not to do in review

- Do not re-flag what `ruff` and the coverage gate already enforce.
- Do not report a finding without a concrete failure case — "this could be
  fragile" is not reviewable. Say which input produces which wrong result.
- Do not ask for hardware verification as a condition of merge. There is no
  thermostat in CI; ask for it to be *stated* as unverified instead.
- Do not guess at protocol behaviour. If the answer depends on what Sensi
  actually sends and no fixture shows it, say that is unknown.

# Risk tiers

Every pull request gets one `tier/*` label and one `size/*` label, applied
automatically by `.github/workflows/labeler.yml` from the rules in
[`.github/risk-tiers.yml`](../.github/risk-tiers.yml).

The tier answers one question: **what is the worst thing that happens if this
change is wrong?** It is the highest tier whose paths the change touches — not a
score, not additive. A change touching both `client.py` and a doc is
`tier/runtime`, because the doc cannot make it safer.

## The tiers

### `tier/breaking` — can break an existing install on upgrade

`config_flow.py`, `auth.py`, `manifest.json`, `entity.py`.

These decide whether an existing user's integration still loads, still finds its
credentials, and still owns the same entities after an update. Getting one wrong
does not produce a bug report about a wrong temperature — it produces an
integration that will not start, or a duplicate set of entities with the old
ones orphaned, and the user has no way to roll back through HACS without
knowing to.

**What is required:** the *Risk* section of the pull request must say explicitly
what changes for an existing install and what the user will see. A breaking
change with an empty Risk section is blocked on that alone. It also belongs in
the release notes, not buried in a commit subject.

### `tier/runtime` — runs against the live service

`client.py`, `coordinator.py`, `__init__.py`, `data.py`, `capabilities.py`,
`event.py`.

The connection, the reconnect and token-refresh path, the event queue, and the
parsing of whatever Sensi actually sends. CI cannot fully verify any of it: the
protocol is reverse engineered and undocumented, and there is no thermostat in
CI. `tests/e2e/` covers the shapes we know about, which is not the same as the
shapes that exist.

**What is required:** end-to-end coverage via `FakeSensiBackend` for the new
path, including the failure case — not only the happy one. Parsing must degrade
rather than raise; an exception in a parser takes the whole integration down
rather than one entity. If the change assumes a payload field, that field must
appear in a committed fixture. Say plainly whether it was exercised against real
hardware.

### `tier/behaviour` — changes what an entity reports or accepts

The platform modules, `strings.json`, `translations/`.

User-visible, testable, and recoverable. A wrong unit, a wrong availability
rule, or a missing capability gate shows up as one entity behaving oddly rather
than as a dead integration.

**What is required:** a unit test at the platform level; an end-to-end test if
the entity writes back to the thermostat, asserting on the emitted payload
rather than only on the resulting state. New entities must be gated on
`capabilities.py` so they do not appear permanently broken on models that lack
the feature.

### `tier/support` — cannot reach a user's installation

Tests, CI, tooling, documentation, agent instruction files.

The default. A mistake here costs contributor time, not user trust.

**What is required:** the gates pass. Note that a change to `ruff.toml`, the
coverage threshold, or a workflow is `tier/support` but still deserves a real
review — weakening a gate is how the other tiers stop being caught.

## Size labels

`size/XS` ≤20 lines, `size/S` ≤100, `size/M` ≤400, `size/L` ≤1000, `size/XL`
above that, counting additions plus deletions.

Size is context for the tier, not a limit. Nothing is rejected for being large.
The combination worth pausing on is **high tier and large size** — a 900-line
`tier/breaking` change is hard to review carefully in one pass, and splitting it
is usually cheaper than reviewing it badly.

A large `tier/support` change is normal here: a test suite or a documentation
set lands in one piece.

## Running the classifier yourself

```bash
python3 scripts/classify_pr.py --pr 44
python3 scripts/classify_pr.py --files custom_components/sensi/client.py --lines 30
git diff --name-only master... | python3 scripts/classify_pr.py --stdin --lines 0
```

## Changing the rules

Edit [`.github/risk-tiers.yml`](../.github/risk-tiers.yml) and this file
together. Tiers are evaluated in file order, highest first, so a new path
pattern must go in the tier that describes its worst outcome — not the one that
is most convenient.

Each tier carries two descriptions. `label_description` is what GitHub shows on
the label and is capped at 100 characters; `description` is the longer prose for
anyone reading the rules file. `scripts/classify_pr.py` rejects an over-long
`label_description`, because GitHub's own failure is an HTTP 422 on label
creation followed by an unrelated-looking "label not found" when the label is
applied.

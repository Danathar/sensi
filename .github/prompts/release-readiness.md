# Check release readiness

The release workflow is `workflow_dispatch` only, and it derives the version
from Conventional Commit prefixes since the last tag. Run this before
triggering it.

## Do this

1. **List the commits since the last tag** and classify each prefix.

   ```bash
   git describe --tags --abbrev=0
   git log --oneline "$(git describe --tags --abbrev=0)"..master
   ```

2. **Check the prefixes are honest.** `feat:` produces a minor bump and `fix:` a
   patch bump, and both appear in the release notes users read in HACS. A
   tooling, CI, test or docs commit prefixed `feat:` will bump the integration's
   version and tell users something shipped when nothing did. Flag any
   mismatch — the prefix is what users see, not the diff.

3. **Predict the version** the workflow will compute, and check it against
   `manifest.json` `version`. Do not edit that field by hand; the workflow owns
   it.

4. **Confirm master is green** — `pytest`, `ruff`, the coverage gate, hassfest,
   HACS validation, and the requirements sync check.

5. **Check for user-visible breakage** in the range: config flow changes,
   entity `unique_id` changes, stored-credential shape changes. Each of these
   breaks existing installs and belongs in the release notes explicitly, not
   buried in a commit subject.

## Output

- The predicted next version, and the commits that drive it.
- Any prefix that misrepresents its change, named individually.
- Anything breaking for existing installs, called out separately.
- A plain go / no-go.

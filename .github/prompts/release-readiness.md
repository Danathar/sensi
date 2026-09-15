# Check release readiness

The release workflow runs monthly on a schedule and on `workflow_dispatch`, and
it never derives the version from commit messages — the number it tags is the
CalVer string already committed in `custom_components/sensi/manifest.json`, put
there by a reviewed `chore(release):` pull request that a `prepare` run opened.
Run this before triggering a release.

## Do this

1. **List the commits since the last tag** and classify each prefix.

   ```bash
   git describe --tags --abbrev=0
   git log --oneline "$(git describe --tags --abbrev=0)"..master
   ```

2. **Check the prefixes are honest.** The prefix does not move the version, but
   it decides how the change reads in the notes GitHub generates, which is what
   a user is shown when HACS offers the update. A tooling, CI, test or docs
   commit prefixed `feat:` announces something shipped when nothing did. Flag
   any mismatch — the prefix is what users see, not the diff.

3. **Check the version that will be released.** It is `manifest.json`'s
   `version` field, verbatim: confirm it is CalVer `YYYY.M.PATCH`, that no tag
   of that name exists yet, and that the bump landed through its own pull
   request. If the manifest still holds the version already tagged, no release
   is ready — run the workflow with `prepare` first and merge what it opens.

4. **Confirm master is green** — `pytest`, `ruff`, the coverage gate, hassfest,
   HACS validation, and the requirements sync check.

5. **Check for user-visible breakage** in the range: config flow changes,
   entity `unique_id` changes, stored-credential shape changes. Each of these
   breaks existing installs and belongs in the release notes explicitly, not
   buried in a commit subject.

6. **Check there is something to release.** The scheduled run skips a month
   whose commits left `custom_components/sensi` untouched, unless it is run by
   hand with `force`. A release nobody can observe trains people to ignore the
   update prompt.

## Output

- The version in the manifest, and whether it is ready to tag.
- Any prefix that misrepresents its change, named individually.
- Anything breaking for existing installs, called out separately.
- A plain go / no-go.

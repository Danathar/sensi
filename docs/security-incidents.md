# Security incidents

A record of security incidents affecting this repository, and what was done
about them.

**No entry in this file contains credential material, and none ever should.**
An incident record is written so that people can act on it — it gets pasted
into issues, chat and tickets. A record that quoted the secret would put the
secret in all of those places too. Everything here is identifiers: commit
SHAs, blob SHAs, paths, counts.

---

## SEC-001 — Sensi refresh credential in inherited Git history

**Status: open.** The code-level redaction has landed. Revocation and the
history rewrite have not, and neither can be done by a pull request — see
"What remains" below.

Tracking issue: [#108](https://github.com/Danathar/sensi/issues/108).

### What was exposed

A complete Sensi refresh-token bearer credential, and an account identifier,
captured from a real debug session and committed into
`custom_components/sensi/client.py`. A refresh token is a bearer credential:
possession is authorisation, so anyone who read the file could act as that
account until the token was revoked.

### What has been done

`94947876eba626c49af193af06f565fca5176cf1` ("fix: stop leaking refresh tokens
into logs and source"), merged as `88323080b06a824fa3c5fa0ead84f9e7e3b61a50`,
removed the value from the tip and added `redact_token()` in
`custom_components/sensi/utils.py`, so tokens are no longer written to logs
either. **The current tree is clean**: the only JWT-shaped string in it today
is a 53-character synthetic fixture in `tests/test_utils.py` carrying a single
`user_id` claim, which does not match the leaked value.

Redacting the tip does not remove anything from history, which is why this
incident is still open.

### Scope, as measured

Measured with `scripts/scan_history_for_secret.py` (see below) against all
reachable refs:

| | |
| --- | --- |
| Distinct leaked strings | 1 |
| Blob versions carrying it | 3, all of `custom_components/sensi/client.py` |
| Reachable commits carrying it | 12, from 2026-07-19 to 2026-09-05 |
| Tags carrying it | **0** — `2026.9.0` is clean |
| Branches descending from the carrying history | 31 of 32 |

The earliest carrying commit is `10eb5e6` (2026-07-19). Everything from there
to the tip — 107 of master's 480 commits — changes SHA in a rewrite, including
the `2026.9.0` tag, which descends from the carrying history even though it
carries nothing itself.

### The finding that changes the remediation

**All 12 carrying commits also exist in the upstream this repository was forked
from, `iprak/sensi`** — verified by resolving each SHA against the upstream
repository through the GitHub API. The credential was not introduced here; it
arrived with the fork.

Three consequences, and they matter more than the cleanup:

1. **Rewriting this repository's history does not remove the credential from
   the internet.** It remains publicly retrievable from `iprak/sensi` and from
   every other fork of it. Acceptance criterion 3 — "a fresh clone/search
   cannot recover the leaked values" — is not achievable by any action taken
   solely in this repository.
2. **Revocation is not merely the priority; it is the only control that
   actually closes the exposure.** Everything else reduces where the value is
   convenient to find.
3. **Upstream should be told**, so the account holder and the upstream
   maintainer can act. That is a human-to-human disclosure, not an issue
   comment: do not open a public issue quoting the value or the file and line.

Because this repository is a fork, GitHub keeps objects in the fork network
retrievable by SHA. A blob can stay fetchable through a fork-network URL after
it is unreachable from every branch here. Only GitHub Support can purge that,
and only after the rewrite.

### Detection posture

Checked on the repository at the time of writing:

| Control | Status |
| --- | --- |
| Secret scanning | **enabled** |
| Push protection | **enabled** |
| Non-provider patterns | **disabled** |
| Validity checks | disabled |
| Dependabot security updates | disabled |

Push protection is on, which is the control that would stop the next one at
push time. The gap worth closing is **non-provider patterns**: GitHub's
provider patterns match credentials from partnered issuers, and a Sensi
refresh token is not one of them. With provider patterns alone, neither
scanning nor push protection would have caught this leak, and neither would
catch a recurrence. Enabling non-provider patterns is a repository setting, not
a code change.

### What remains

Ordered. Item 1 is the one that matters; the rest is cleanup.

- [ ] **Revoke or rotate the credential, and confirm it can no longer
      authenticate.** Account action on the Sensi side. Until this is done the
      exposure is live regardless of anything in Git, and after it is done the
      remaining steps are tidying.
- [ ] **Report the exposure to `iprak/sensi`** privately, since the carrying
      commits originate there.
- [ ] **Rewrite this repository's history** (runbook below).
- [ ] **Force-update the cleaned refs**, after telling every collaborator, and
      re-tag `2026.9.0`.
- [ ] **Ask GitHub Support to purge the fork-network cache** for the affected
      blob SHAs, which the runbook prints.
- [ ] **Enable secret-scanning non-provider patterns** so a non-partnered
      credential is caught next time.

### Runbook: rewriting the history

Read this whole section before running any of it. The rewrite is destructive,
invalidates every open pull request, and requires everyone with a clone to
re-clone.

**Prerequisites.** `git-filter-repo` (not bundled with Git; `pip install
git-filter-repo`). A fresh `--mirror` clone. Agreement from collaborators on a
window — nobody should be pushing while this happens.

**1. Put the literal somewhere Git cannot reach.** Extract it from the
historical blob rather than typing or pasting it, so it never passes through a
terminal, a clipboard, or an agent prompt:

```bash
git show a59aaab:custom_components/sensi/client.py \
  | grep -oE 'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+' \
  | sort -u > ~/sensi-leak.secrets     # OUTSIDE the repository
```

Check it by length and line count, not by reading it. Add the account
identifier as a second line if it is not part of the token.

**2. Confirm the scope before changing anything.**

```bash
python3 scripts/scan_history_for_secret.py --secrets-file ~/sensi-leak.secrets
```

Expect the table above: 3 blobs, 12 commits. If the numbers differ, the scope
has changed since this was written — re-scope before rewriting.

**3. Rewrite.** `--replace-text` redacts the string and keeps the file, which
is what is wanted here: `client.py` is real source, and only one value in it is
bad.

```bash
git clone --mirror https://github.com/Danathar/sensi.git sensi-mirror
cd sensi-mirror
printf 'literal:%s==>***REMOVED***\n' "$(head -1 ~/sensi-leak.secrets)" > /tmp/replacements.txt
git filter-repo --replace-text /tmp/replacements.txt
rm -f /tmp/replacements.txt
```

**4. Verify before pushing.** This is the gate; do not skip it.

```bash
python3 /path/to/scripts/scan_history_for_secret.py \
  --secrets-file ~/sensi-leak.secrets --repo .
```

Exit 0 — "No reachable blob contains any of the given strings" — is the pass
condition. Anything else means the rewrite was incomplete.

**5. Push, then clean up.**

```bash
git push --force --mirror
```

Then: re-tag `2026.9.0` at its rewritten commit; ask every collaborator to
re-clone rather than pull; close and reopen any pull request open at the time;
and send GitHub Support the blob SHAs from step 2 for fork-network purging.
Finally, `shred -u ~/sensi-leak.secrets`.

### The verification tool

`scripts/scan_history_for_secret.py` searches every reachable blob for literal
strings supplied in a file, and prints **identifiers only** — blob SHAs, paths,
commit subjects. It never prints a needle, a matched line, or file content,
which is the same rule this document follows and for the same reason: a
credential must not be copied into new places while it is being cleaned up.

It refuses to read its needles from a file that Git is tracking, because a
committed list of live credentials would be this incident again in a new shape.
`.gitignore` carries `*.secrets` so that following the runbook cannot stage one.

It exits 1 when anything is found and 0 when nothing is, so step 4 above can
gate on it.

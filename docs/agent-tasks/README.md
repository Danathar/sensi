# Tracing agent work

Almost every change an agent makes here can be followed back to the task
that asked for it, and forward to the commit that landed it. The exceptions
are listed under [Where the gaps are](#where-the-gaps-are). No task list is
kept in this directory, because GitHub already holds the record. This page
explains how to read it.

## The chain

```text
merge commit on master
  └─ pull request   (author, branch, footer: agent, backend, model)
       └─ issue     (the task: filed by an agent or a person)
            └─ Hive's audit log   (agent_issue_created / agent_pr_created)
```

1. **Merge commit.** Every pull request since 2026-09-05 has landed as a
   merge commit. The ruleset also allows squash and rebase, so this is a
   habit, not a rule. While the habit holds, `master`'s first-parent history
   has one `Merge pull request #N from Danathar/<branch>` line per change:

   ```bash
   git log --first-parent --oneline master
   ```

2. **Pull request.** An agent's pull request is authored by the
   `danathar-atomic-hive` GitHub App. It normally ends with a footer that
   names the agent and the model that wrote it:

   ```text
   — hive: agent=quality backend=claude model=… effort=…
   ```

   The branch prefix usually names the agent too (`quality/`, `sec/`,
   `scanner/`, `guide/`, `architect/`), but that is a convention. The footer
   is the record.

3. **Issue.** The pull request body names the issue it works on, almost
   always as `Closes #N`, so the issue closes when the pull request merges.
   The issue says why the work was wanted. If an agent filed it, it carries a
   footer too. Most name the backend and model, but the ACMM evaluation's
   issues (`agent=dashboard`) name only the agent.

4. **Hive's audit log.** Outside this repository, Hive records an
   `agent_issue_created` or `agent_pr_created` event for each issue and pull
   request an agent opens. Each event names the repository, the number, the
   agent, the backend and the model.

## Useful queries

Every pull request an agent opened, with the issue it closes:

```bash
gh pr list -R Danathar/sensi --state all --limit 500 \
  --author app/danathar-atomic-hive \
  --json number,title,closingIssuesReferences \
  --jq '.[] | "#\(.number) closes \([.closingIssuesReferences[].number]) \(.title)"'
```

Which agent opened a given pull request:

```bash
gh pr view <N> -R Danathar/sensi --json body --jq '.body' | grep 'hive: agent='
```

## Where the gaps are

- A few agent pull requests link their issue without a closing keyword, and
  two early ones (#76, #86) name no issue at all.
- One early pull request, #179, has no footer.
- Interactive sessions run by a person with an AI assistant are not Hive
  agents, so they leave no audit-log entry. Those pull requests are authored
  by the person, and the trail is the pull request and its issue.

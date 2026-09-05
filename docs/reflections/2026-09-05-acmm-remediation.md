# Working a list of file-existence issues

**Take-away.** Thirty issues of the form "this repository is missing one of the
following files" produced one change that mattered a great deal, several that
were worth having, and a real risk of accumulating documentation nobody reads.
Separating those three categories deliberately, rather than treating the list as
uniform, is what made the exercise worth doing.

## What the list was

An automated maturity evaluation filed issues #5–#34 against this repository.
Every one had the same shape: a criterion ID, a list of acceptable paths, and
"the ACMM evaluation checks for file existence — the content can follow your
project's conventions".

Read literally, all thirty could have been closed by `touch`. That is the
failure mode the format invites, and it is worth being explicit that it was
available and not taken.

## How they actually differed

**One was load-bearing.** #5 asked for an end-to-end test directory. The
repository genuinely had one test tier, and the module with the most runtime
risk — the socket.io client — sat at 52% line coverage while the repository
read 85%. Building that tier properly took `client.py` to 90% and the total to
96%, and found a live trap in the process. See
[`testing-an-undocumented-protocol.md`](testing-an-undocumented-protocol.md).
This one issue justified the whole exercise.

**Several were real gaps with modest value.** A coverage gate that had not
existed, so a regression merged silently. A `ruff.toml`, without which lint and
format were only reproducible inside the devcontainer image. `timeout-minutes`
on workflow jobs — filed separately as #40, and genuinely sharp on this
repository because three workflows use `cancel-in-progress` concurrency groups,
so a hung run blocks everything behind it for six hours.

**Several were only worth what the content made them worth.** `AGENTS.md`,
`CLAUDE.md`, a prompt catalog, a review rubric. These are the ones where a file
satisfying the criterion and a file worth reading are entirely different
artifacts, and only the author can tell which they wrote.

## What was done about the third category

The test applied to each was: **does this say something that is not already
obvious from the code?** If not, it was not worth writing.

What survived that test was specific to this repository — that the protocol is
reverse engineered and what follows for parsing; that `pyproject.toml` is
gitignored because the devcontainer supplies one, so creating one is wrong;
that `feat:` and `fix:` bump the version users see in HACS, so tooling commits
must not use them; that `strings.json` cannot contain literal URLs because
hassfest rejects them.

What was cut was everything that amounted to "write good tests" and "follow the
existing style".

The same test produced the `.claude/memory/` entries, and those are the most
clearly useful documents of the set — each one records a mistake actually made
during the work, including why the wrong answer looked right. A fake socket.io
client must implement `shutdown()`; `except ValueError, TypeError:` is valid on
Python 3.14 and should not be "fixed"; the `hass` fixture must be created before
anything that patches `Store.async_load` globally.

## Duplication that was accepted on purpose

`AGENTS.md` is canonical and `CLAUDE.md` and `.cursor/rules/` point at it.
`.github/copilot-instructions.md` restates the hard rules in full instead,
because Copilot reads that file directly and will not follow a link. That
duplication will drift. The mitigation is a line in `AGENTS.md` saying so, which
is weaker than not duplicating, and was still the better trade.

## What was deliberately made inert

The L4 issues asked for automation that acts on the repository: an AI-fix
workflow, automated review application, nightly runs, issue labelling.

The labeller and the nightly run are live — they only add labels and open one
self-closing issue. The AI-fix workflow is not: it requires both an
`ANTHROPIC_API_KEY` secret and an `AI_FIX_ENABLED` repository variable.

Two switches rather than one, because the `ai-fix-requested` label is applied
*automatically by the bot that filed these issues*. A single switch would mean
that adding an API key for an unrelated purpose silently starts autonomous work
on every issue that bot files. Satisfying the criterion did not require turning
it on, and turning it on is a decision the repository owner should make
knowingly.

## Honest assessment

The end-to-end tier and the coverage gate are worth keeping regardless of the
framework that prompted them. The agent instruction files are worth keeping if
they are maintained and worth deleting if they are not — a stale `AGENTS.md` is
worse than none, because it is confidently wrong.

The maturity score itself was never the point and should not be treated as one.
What changed is that `client.py` is now tested, a coverage regression now fails
CI, and lint is reproducible outside one Docker image. Those would have been
worth doing with no issues filed at all.

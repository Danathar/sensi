# Reflections

Knowledge that outlives the change that produced it.

`git log` records what changed. [`.claude/memory/`](../../.claude/memory/)
records specific corrections — small, sharp, "do this not that". This directory
is for the middle thing: what a piece of work *taught* about this codebase or
about working on it, in a form the next person can use without having done the
work.

## What belongs here

- A technique that turned out to be the right one, and the two that did not.
- A constraint that was discovered rather than documented — something true about
  this repository that nothing in it says out loud.
- An honest assessment of a piece of work, including what it did not achieve.

## What does not

- Anything already in `AGENTS.md`, `CONTRIBUTING.md`, or the code.
- A changelog entry. Use the commit message.
- A single correction. Use `.claude/memory/`.
- Speculation about work not actually done.

## Format

One file per reflection, `YYYY-MM-DD-slug.md` for dated ones and a plain slug
for durable knowledge that is not tied to a date. Start with what the reader
should take away, not with a narrative.

## Index

| Reflection | Take-away |
| --- | --- |
| [`testing-an-undocumented-protocol.md`](testing-an-undocumented-protocol.md) | how to get real coverage of a reverse-engineered client, and what that coverage still does not prove |
| [`2026-09-05-acmm-remediation.md`](2026-09-05-acmm-remediation.md) | what came out of working a list of file-existence issues, and what it changed |

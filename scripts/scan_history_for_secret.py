#!/usr/bin/env python3
"""Search reachable Git history for known-leaked literal strings.

Written for #108, where a Sensi refresh-token bearer credential reached public
history. Removing a secret from history is only half the job: the other half is
being able to say, mechanically and repeatably, that it is gone. That is what
this answers -- before a rewrite, to scope it; after, to verify it.

**It never prints a needle, a matched line, or any file content.** Output is
identifiers and counts only: blob SHAs, paths, commit SHAs. The whole reason the
incident record in docs/security-incidents.md carries no values is that a
credential must not be copied into new places while being cleaned up, and a
verification tool that echoed what it found would undo that on its first run.

The needles come from a file the operator writes, one literal per line, and
that file must not be tracked by Git -- checked, not assumed, because a
verification tool whose input got committed would be the same accident again in
a new shape. .gitignore carries `*.secrets` for this.

Usage:

    python3 scripts/scan_history_for_secret.py --secrets-file leaked.secrets

Exit status is 0 when nothing is found and 1 when anything is, so it works as
the final gate in the runbook.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

# Blobs larger than this are skipped. Nothing in this repository comes close,
# and reading an arbitrarily large object into memory to look for a token is a
# poor trade -- a secret pasted into a multi-megabyte binary is not the shape
# of accident this exists for.
MAX_BLOB_BYTES = 4_000_000
# Below this length a literal matches incidental text everywhere, and a report
# naming every file in the repository hides a real finding instead of showing
# it.
MIN_NEEDLE_LENGTH = 8


class ScanError(RuntimeError):
    """A problem with the inputs, as opposed to a finding."""


def git(repo: Path, *args: str, binary: bool = False) -> bytes | str:
    """Run git in `repo` and return stdout, empty on failure."""
    result = subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def assert_untracked(repo: Path, secrets_file: Path) -> None:
    """Refuse to read needles out of a file Git is tracking.

    The failure this prevents is the original incident repeating: a file full
    of live credentials, committed because it was sitting in the working tree
    when someone ran `git add -A`.
    """
    try:
        relative = secrets_file.resolve().relative_to(repo.resolve())
    except ValueError:
        return  # Outside the repository entirely, so Git cannot be tracking it.
    if git(repo, "ls-files", "--error-unmatch", str(relative)).strip():
        raise ScanError(
            f"{relative} is tracked by Git. Move the secrets file outside the "
            "repository, or ignore it and remove it from the index, before "
            "scanning."
        )


def load_needles(secrets_file: Path) -> list[bytes]:
    """Read one literal secret per line, ignoring blanks and # comments."""
    try:
        text = secrets_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScanError(f"Unable to read {secrets_file}: {exc}") from exc
    needles = [
        line.strip().encode("utf-8")
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not needles:
        raise ScanError(f"{secrets_file} contains no secrets to search for.")
    too_short = [needle for needle in needles if len(needle) < MIN_NEEDLE_LENGTH]
    if too_short:
        raise ScanError(
            f"{len(too_short)} entry/entries are shorter than {MIN_NEEDLE_LENGTH} "
            "characters. Refusing to scan: a short literal matches incidental "
            "text and buries a real finding."
        )
    return needles


def iter_named_blobs(repo: Path) -> dict[str, set[str]]:
    """Every object reachable from any ref, mapped to the paths it appears at."""
    named: dict[str, set[str]] = {}
    for line in git(repo, "rev-list", "--objects", "--all").splitlines():
        sha, _, path = line.partition(" ")
        if not path.strip():
            continue
        named.setdefault(sha, set()).add(path.strip())
    return named


def carrying_blobs(repo: Path, needles: list[bytes]) -> dict[str, set[str]]:
    """Blob SHA -> paths, for every reachable blob containing any needle."""
    found: dict[str, set[str]] = {}
    for sha, paths in iter_named_blobs(repo).items():
        if git(repo, "cat-file", "-t", sha).strip() != "blob":
            continue
        size = git(repo, "cat-file", "-s", sha).strip()
        if size.isdigit() and int(size) > MAX_BLOB_BYTES:
            continue
        content = git(repo, "cat-file", "blob", sha, binary=True)
        if any(needle in content for needle in needles):
            found[sha] = paths
    return found


def carrying_commits(repo: Path, blob_shas: set[str]) -> list[str]:
    """Return the commits whose tree contains any of `blob_shas`, newest first."""
    if not blob_shas:
        return []
    carriers = []
    for commit in git(repo, "rev-list", "--all").splitlines():
        listing = git(repo, "ls-tree", "-r", commit)
        if any(sha in listing for sha in blob_shas):
            carriers.append(commit)
    return carriers


def report(repo: Path, blobs: dict[str, set[str]], commits: list[str]) -> list[str]:
    """Render the finding. Identifiers only -- never a value."""
    if not blobs:
        return ["No reachable blob contains any of the given strings."]
    lines = [
        f"FOUND: {len(blobs)} blob(s) in {len(commits)} reachable commit(s) "
        "still contain a known-leaked string.",
        "",
        "Blobs:",
    ]
    for sha in sorted(blobs):
        for path in sorted(blobs[sha]):
            lines.append(f"  {sha[:12]}  {path}")
    lines.extend(["", "Commits:"])
    for commit in commits:
        subject = git(repo, "log", "-1", "--format=%h %ad %s", "--date=short", commit)
        lines.append(f"  {subject.strip()}")
    return lines


def main(argv: list[str] | None = None) -> int:
    """Scan and report, returning 0 when clean, 1 when found, 2 on bad input."""
    parser = argparse.ArgumentParser(
        description="Search reachable Git history for known-leaked strings.",
        epilog="Prints identifiers only; never the strings themselves.",
    )
    parser.add_argument(
        "--secrets-file",
        type=Path,
        required=True,
        help="File of literal secrets, one per line. Must not be tracked by Git.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository to scan (default: the one this script lives in).",
    )
    args = parser.parse_args(argv)

    try:
        assert_untracked(args.repo, args.secrets_file)
        needles = load_needles(args.secrets_file)
    except ScanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    blobs = carrying_blobs(args.repo, needles)
    commits = carrying_commits(args.repo, set(blobs))
    for line in report(args.repo, blobs, commits):
        print(line)
    return 1 if blobs else 0


if __name__ == "__main__":
    raise SystemExit(main())

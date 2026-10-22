#!/usr/bin/env python3
"""Break each changed line in small ways and require a test to notice.

Coverage says a line ran. It does not say a test would fail if the line were
wrong, and in this repository that gap has been found by hand seven times
(#378, #402, #434, #451, #617, #640, #655): a branch the tests execute but
never assert on. The last of those found twelve small edits to the command
gate hook that let a refused command through while every test still passed.

This script is that hand check, run on the lines a pull request changes. For
every changed `.py` file under `custom_components/sensi/`, `scripts/` and
`.claude/hooks/`, it makes one small edit at a time to a changed line - a
mutant - and runs the tests that load that file. A mutant no test fails on is
a survivor. A survivor that a reviewer has judged harmless is listed in
`tests/mutation-equivalents.txt`; any other survivor fails the run.

The edits, each applied on its own:

- a comparison negated (`==` to `!=`, `in` to `not in`) or moved across its
  boundary (`<` to `<=`)
- `and` swapped with `or`, and each operand of either dropped in turn
- a `not` dropped
- an integer moved by one either way, and `True` swapped with `False`
- an `if` or `elif` test forced to `True` and to `False`, a `while` test
  forced to `False`
- `continue` or `break` replaced by `pass`
- `return x` replaced by `return None`

The run never edits the checkout it is started from. It copies the
repository into a temporary directory, one copy per parallel job, writes each
mutant into a copy and puts the original bytes back before the next one, in
a `finally`. Python runs with `-B` and `PYTHONDONTWRITEBYTECODE` set: a mutant
written and restored within one second can leave a `__pycache__` entry whose
timestamp and size match the original, and the next run then tests the
mutant instead of the original.

A mutant counts as caught when the tests fail or run past the time limit, and
as a survivor when they pass. Before any mutant of a file runs, its tests run
once unmutated; if that fails, nothing can be judged and the run stops.

Exit status: 0 when every survivor is listed, 1 when one is not, 2 when the
run could not judge (a git error, a failing baseline, a malformed equivalents
file), 130 when interrupted.
"""

import argparse
import ast
import bisect
import concurrent.futures
from dataclasses import dataclass
import io
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import warnings

ROOT = Path(__file__).resolve().parent.parent
EQUIVALENTS = ROOT / "tests" / "mutation-equivalents.txt"
EQUIVALENTS_NAME = "tests/mutation-equivalents.txt"

# The directories whose Python is mutated.
SCOPES = ("custom_components/sensi/", "scripts/", ".claude/hooks/")
COMPONENT = "custom_components/sensi/"

# This script is excluded: its tests load it, so a mutant of it would be
# judged by the code the mutant just changed.
SELF = "scripts/mutate_changed.py"

# The fields of one entry in the equivalents file, in the order they are
# printed. `line` is informational: the key is the file, the original text
# and the mutant text, so an entry still matches after lines move.
FIELDS = ("file", "line", "original", "mutant", "reason")

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# Each comparison and what it becomes: its negation, and for an ordering its
# neighbour across the boundary.
_COMPARISONS = {
    ast.Eq: (ast.NotEq,),
    ast.NotEq: (ast.Eq,),
    ast.Lt: (ast.GtE, ast.LtE),
    ast.LtE: (ast.Gt, ast.Lt),
    ast.Gt: (ast.LtE, ast.GtE),
    ast.GtE: (ast.Lt, ast.Gt),
    ast.In: (ast.NotIn,),
    ast.NotIn: (ast.In,),
    ast.Is: (ast.IsNot,),
    ast.IsNot: (ast.Is,),
}

# Replacements that need no parentheses wherever an expression can stand.
_ATOMS = (ast.Name, ast.Attribute, ast.Call, ast.Subscript, ast.Constant)

# Places where any operator expression can stand without parentheses.
_BARE_PARENTS = (
    ast.stmt,
    ast.keyword,
    ast.List,
    ast.Tuple,
    ast.Set,
    ast.Dict,
    ast.comprehension,
)

# Replacements that need parentheses even in those places.
_NEEDS_PARENTHESES = (
    ast.NamedExpr,
    ast.Lambda,
    ast.IfExp,
    ast.Starred,
    ast.Yield,
    ast.YieldFrom,
)

# What a copy of the repository leaves behind. A stale bytecode file is what
# `-B` is there to rule out, so none is carried into a copy either.
_UNCOPIED = (
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "*.pyc",
)

_STOP = threading.Event()


class _Stopped(Exception):
    """The run was interrupted while a mutant's tests were running."""


class _BaselineFailed(Exception):
    """A file's tests fail before any mutant is written."""


@dataclass(frozen=True)
class Mutant:
    """One small edit to the changed lines of one file."""

    path: str
    line: int
    end: int
    original: str
    replacement: str

    @property
    def mutated(self) -> str:
        """Return the text of the mutated line, stripped."""
        return self.replacement.strip()

    @property
    def key(self) -> tuple[str, str, str]:
        """Return what identifies this mutant in the equivalents file."""
        return (self.path, self.original, self.mutated)

    def apply(self, lines: list[str]) -> str:
        """Return the file's source with this mutant in place."""
        before, after = lines[: self.line - 1], lines[self.end :]
        return "".join([*before, self.replacement, *after])

    def entry(self, reason: str = "") -> str:
        """Return this mutant as an equivalents-file entry."""
        values = (self.path, str(self.line), self.original, self.mutated, reason)
        pairs = zip(FIELDS, values, strict=True)
        return "\n".join(f"{name}: {value}".rstrip() for name, value in pairs)


def changed_lines(diff: str) -> dict[str, set[int]]:
    """Return {path: added or rewritten line numbers} read from `git diff -U0`.

    A `+++ ` line is a file header only before the first hunk of that file;
    inside a hunk it is an added line that happens to start with `++ `.
    """
    changed: dict[str, set[int]] = {}
    path = None
    in_header = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = None
            in_header = True
            continue
        if in_header and line.startswith("+++ "):
            target = line[4:]
            path = target[2:] if target.startswith("b/") else None
            continue
        match = _HUNK.match(line)
        if match is None:
            continue
        in_header = False
        if path is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        changed.setdefault(path, set()).update(range(start, start + count))
    return {path: lines for path, lines in changed.items() if lines}


def in_scope(path: str) -> bool:
    """Return whether a changed file is one this gate mutates."""
    return path.endswith(".py") and path != SELF and path.startswith(SCOPES)


def _source_lines(source: str) -> list[str]:
    """Split source into lines the way the parser numbers them."""
    return io.StringIO(source, newline="").readlines()


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Return each node's parent."""
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _bare(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Return whether an operator expression can replace node unparenthesised."""
    parent = parents.get(node)
    if isinstance(parent, ast.Call):
        return node is not parent.func
    if isinstance(parent, ast.Subscript):
        return node is parent.slice
    return isinstance(parent, _BARE_PARENTS)


def _expression(replacement: ast.expr, node: ast.AST, parents: dict) -> str:
    """Return replacement's source, parenthesised where node's place needs it."""
    text = ast.unparse(replacement)
    if isinstance(replacement, _ATOMS):
        return text
    if _bare(node, parents) and not isinstance(replacement, _NEEDS_PARENTHESES):
        return text
    return f"({text})"


def _comparison_edits(node: ast.Compare, parents: dict) -> list[tuple[ast.AST, str]]:
    """Return each comparison of node negated, or moved across its boundary."""
    edits: list[tuple[ast.AST, str]] = []
    for index, operator in enumerate(node.ops):
        for swapped in _COMPARISONS[type(operator)]:
            operators = [*node.ops]
            operators[index] = swapped()
            mutated = ast.Compare(
                left=node.left, ops=operators, comparators=node.comparators
            )
            edits.append((node, _expression(mutated, node, parents)))
    return edits


def _boolean_edits(node: ast.BoolOp, parents: dict) -> list[tuple[ast.AST, str]]:
    """Return node with `and` and `or` swapped, and with each operand dropped."""
    other = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    swapped = ast.BoolOp(op=other, values=node.values)
    edits: list[tuple[ast.AST, str]] = [(node, _expression(swapped, node, parents))]
    for index in range(len(node.values)):
        rest = [*node.values[:index], *node.values[index + 1 :]]
        kept = rest[0] if len(rest) == 1 else ast.BoolOp(op=node.op, values=rest)
        edits.append((node, _expression(kept, node, parents)))
    return edits


def _edits(node: ast.AST, parents: dict) -> list[tuple[ast.AST, str]]:
    """Return (the node replaced, its replacement source) for each mutant of node."""
    if isinstance(node, ast.If):
        current = ast.unparse(node.test)
        return [(node.test, value) for value in ("True", "False") if value != current]
    if isinstance(node, ast.While):
        return [] if ast.unparse(node.test) == "False" else [(node.test, "False")]
    if isinstance(node, (ast.Continue, ast.Break)):
        return [(node, "pass")]
    if isinstance(node, ast.Return):
        if node.value is None or ast.unparse(node.value) == "None":
            return []
        return [(node, "return None")]
    if isinstance(node, ast.Compare):
        return _comparison_edits(node, parents)
    if isinstance(node, ast.BoolOp):
        return _boolean_edits(node, parents)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return [(node, _expression(node.operand, node, parents))]
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return [(node, repr(not node.value))]
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return [(node, repr(node.value + 1)), (node, repr(node.value - 1))]
    return []


def _splice(lines: list[str], node: ast.AST, text: str) -> str:
    """Return the lines node spans, with node's source replaced by text.

    Column offsets count UTF-8 bytes, not characters, so the splice is done on
    bytes.
    """
    first = lines[node.lineno - 1].encode("utf-8")
    last = lines[node.end_lineno - 1].encode("utf-8")
    spliced = first[: node.col_offset] + text.encode("utf-8")
    return (spliced + last[node.end_col_offset :]).decode("utf-8")


def _parses(source: str) -> bool:
    """Return whether source is still Python."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            ast.parse(source)
        except SyntaxError, ValueError:
            return False
    return True


def _touches(wanted: list[int], node: ast.AST) -> bool:
    """Return whether node spans any of the sorted line numbers in wanted."""
    first = getattr(node, "lineno", None)
    if first is None:
        return False
    index = bisect.bisect_left(wanted, first)
    return index < len(wanted) and wanted[index] <= node.end_lineno


def mutants(path: str, source: str, changed: set[int]) -> list[Mutant]:
    """Return every mutant of the changed lines of one file.

    A mutant is kept when the node it replaces touches a changed line and the
    result differs from the original and still parses. Two edits that produce
    the same text are one mutant.
    """
    lines = _source_lines(source)
    tree = ast.parse(source)
    parents = _parents(tree)
    wanted = sorted(changed)
    found: dict[tuple[int, int, str], Mutant] = {}
    for node in ast.walk(tree):
        if not _touches(wanted, node):
            continue
        for target, text in _edits(node, parents):
            if not _touches(wanted, target):
                continue
            first, last = target.lineno, target.end_lineno
            original = " ".join(line.strip() for line in lines[first - 1 : last])
            replacement = _splice(lines, target, text)
            mutant = Mutant(path, first, last, original, replacement)
            if mutant.mutated == original or not _parses(mutant.apply(lines)):
                continue
            found.setdefault((first, last, replacement), mutant)
    return sorted(found.values(), key=lambda mutant: (mutant.line, mutant.mutated))


def tests_for(path: str, tests: Path) -> list[str]:
    """Return the test files that load a module, relative to tests' parent.

    A test file loads a module when it names the module's file, its dotted
    import path, or imports it from its package. The component's modules are
    also exercised by every end-to-end test, which drives the whole
    integration without naming a module.
    """
    module = PurePosixPath(path)
    dotted = ".".join(module.with_suffix("").parts)
    package = ".".join(module.parent.parts)
    patterns = [
        rf"(?<![\w.-]){re.escape(module.name)}(?!\w)",
        rf"(?<![\w.]){re.escape(dotted)}(?!\w)",
    ]
    if module.name == "__init__.py":
        patterns.append(rf"(?<![\w.]){re.escape(package)}(?![\w.])")
    else:
        importing = rf"\bfrom\s+{re.escape(package)}\s+import\s+"
        named = rf"(?:\([^)]*|[^\n]*)\b{re.escape(module.stem)}\b"
        patterns.append(importing + named)
    end_to_end = path.startswith(COMPONENT)
    found = []
    for candidate in sorted(tests.rglob("test_*.py")):
        relative = candidate.relative_to(tests)
        text = candidate.read_text(encoding="utf-8")
        named_here = any(re.search(pattern, text) for pattern in patterns)
        if named_here or (end_to_end and relative.parts[0] == "e2e"):
            found.append(candidate.relative_to(tests.parent).as_posix())
    return found


def read_equivalents(text: str) -> set[tuple[str, str, str]]:
    """Return the (file, original, mutant) keys a reviewer has accepted.

    Entries are blocks of `name: value` lines separated by blank lines, with
    every field in FIELDS given once. A line starting with `#` is a comment.
    """
    keys: set[tuple[str, str, str]] = set()
    entry: dict[str, str] = {}
    start = 0
    for number, raw in enumerate([*text.splitlines(), ""], 1):
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not line:
            if entry:
                keys.add(_entry_key(entry, start))
                entry = {}
            continue
        name, separator, value = line.partition(":")
        if not separator or name not in FIELDS:
            raise ValueError(f"line {number}: expected one of {', '.join(FIELDS)}")
        if name in entry:
            raise ValueError(f"line {number}: {name} given twice in one entry")
        if not entry:
            start = number
        entry[name] = value.strip()
    return keys


def _entry_key(entry: dict[str, str], start: int) -> tuple[str, str, str]:
    """Return one parsed entry's key, refusing an incomplete entry."""
    missing = [name for name in FIELDS if not entry.get(name)]
    if missing:
        raise ValueError(f"entry at line {start}: no {', '.join(missing)}")
    if not entry["line"].isdigit():
        raise ValueError(f"entry at line {start}: line is not a number")
    return (entry["file"], entry["original"], entry["mutant"])


def unlisted(survivors: list[Mutant], listed: set) -> list[Mutant]:
    """Return the survivors no equivalents entry accounts for."""
    return [mutant for mutant in survivors if mutant.key not in listed]


def _git(*arguments: str) -> str:
    """Run git in the repository and return what it printed."""
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def changed_python_lines(base: str) -> dict[str, set[int]]:
    """Return the in-scope lines changed since the merge base with base."""
    merge_base = _git("merge-base", base, "HEAD").strip()
    diff = _git(
        "diff",
        "-U0",
        "-M",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        merge_base,
        "--",
        *SCOPES,
    )
    return {
        path: lines
        for path, lines in changed_lines(diff).items()
        if in_scope(path) and (ROOT / path).is_file()
    }


def _passes(copy: Path, tests: list[str], timeout: float, log=None) -> bool:
    """Return whether the tests pass in a copy within timeout seconds."""
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen(
        [sys.executable, "-B", "-m", "pytest", "-x", "-q", *tests],
        cwd=copy,
        env=environment,
        stdout=log or subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                return process.wait(timeout=1) == 0
            except subprocess.TimeoutExpired:
                if _STOP.is_set():
                    raise _Stopped from None
                if time.monotonic() > deadline:
                    return False
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def _survives(work: tuple, copies: queue.Queue) -> bool:
    """Return whether the tests still pass with a mutant written into a copy."""
    mutant, lines, tests, timeout = work
    copy = copies.get()
    try:
        target = copy / mutant.path
        original = target.read_bytes()
        try:
            target.write_text(mutant.apply(lines), encoding="utf-8")
            return _passes(copy, tests, timeout)
        finally:
            target.write_bytes(original)
    finally:
        copies.put(copy)


def _baseline(path: str, tests: list[str], copy: Path, scratch: Path) -> float:
    """Run a file's tests unmutated and return the time limit for its mutants.

    The limit is five times the unmutated run plus a minute, so a mutant that
    turns a loop infinite is caught rather than hanging the job.
    """
    log_path = scratch / "baseline.log"
    with log_path.open("w", encoding="utf-8") as log:
        started = time.monotonic()
        passed = _passes(copy, tests, 3600, log)
        elapsed = time.monotonic() - started
    if not passed:
        tail = "\n".join(log_path.read_text(encoding="utf-8").splitlines()[-40:])
        message = f"the tests for {path} fail before any mutant is written:"
        raise _BaselineFailed(f"{message}\n{tail}")
    print(f"{path}: {len(tests)} test file(s), baseline {elapsed:.0f}s", flush=True)
    return elapsed * 5 + 60


def _judge(work: list[tuple], copies: queue.Queue, jobs: int) -> list[Mutant]:
    """Run each mutant's tests, jobs at a time, and return the survivors."""
    survivors = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
    try:
        futures = {executor.submit(_survives, item, copies): item[0] for item in work}
        completed = concurrent.futures.as_completed(futures)
        for done, future in enumerate(completed, 1):
            mutant = futures[future]
            survived = future.result()
            verdict = "SURVIVED" if survived else "caught"
            where = f"[{done}/{len(work)}] {mutant.path}:{mutant.line}: {verdict}"
            print(f"{where}: {mutant.mutated}", flush=True)
            if survived:
                survivors.append(mutant)
    finally:
        _STOP.set()
        executor.shutdown(wait=True, cancel_futures=True)
        _STOP.clear()
    return survivors


def run(plan: list[tuple[str, list[Mutant], list[str]]], jobs: int) -> list[Mutant]:
    """Run every planned mutant and return the survivors.

    plan holds (path, its mutants, the tests that load it). A file no test
    loads cannot have a mutant caught, so its mutants all survive unrun.
    """
    survivors = []
    runnable = []
    for path, found, tests in plan:
        if found and not tests:
            print(f"{path}: no test file loads it, so its mutants all survive")
            survivors.extend(found)
        elif found:
            runnable.append((path, found, tests))
    total = sum(len(found) for _, found, _ in runnable)
    if total:
        with tempfile.TemporaryDirectory(prefix="mutate-") as directory:
            survivors.extend(_run_in(Path(directory), runnable, min(jobs, total)))
    return sorted(survivors, key=lambda mutant: (mutant.path, mutant.line))


def _run_in(scratch: Path, runnable: list[tuple], jobs: int) -> list[Mutant]:
    """Copy the repository jobs times into scratch and judge every mutant there."""
    copies: queue.Queue = queue.Queue()
    destinations = [scratch / f"copy{index}" for index in range(max(1, jobs))]
    for destination in destinations:
        ignore = shutil.ignore_patterns(*_UNCOPIED)
        shutil.copytree(ROOT, destination, symlinks=True, ignore=ignore)
        copies.put(destination)
    work = []
    for path, found, tests in runnable:
        timeout = _baseline(path, tests, destinations[0], scratch)
        lines = _source_lines((ROOT / path).read_text(encoding="utf-8"))
        work.extend((mutant, lines, tests, timeout) for mutant in found)
    return _judge(work, copies, len(destinations))


def _interrupt(signum: int, frame: object) -> None:
    """Turn SIGTERM into the KeyboardInterrupt the cleanup paths handle."""
    raise KeyboardInterrupt


def report(survivors: list[Mutant], listed: set) -> int:
    """Print the survivors and return the exit status they call for."""
    new = unlisted(survivors, listed)
    accepted = len(survivors) - len(new)
    if accepted:
        print(f"{accepted} survivor(s) are listed in {EQUIVALENTS_NAME}.")
    if not new:
        print("Every changed line has a test that notices when it is broken.")
        return 0
    print(f"\n{len(new)} mutant(s) survived that no entry lists:\n")
    for mutant in new:
        print(mutant.entry() + "\n")
    print(
        "Each block above is a small edit to a changed line that every test "
        "still passed with.\nAdd a test that fails on it. If the edit cannot "
        "change behaviour, copy the block into\n"
        f"{EQUIVALENTS_NAME} with a one-line reason after `reason:`."
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """Mutate the changed lines and report the survivors."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base",
        default="origin/master",
        help="the ref whose merge base the changed lines are counted from",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the mutants and the tests each file would run, run nothing",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count() or 1,
        help="how many copies of the repository to test mutants in at once",
    )
    args = parser.parse_args(argv)

    try:
        changed = changed_python_lines(args.base)
    except subprocess.CalledProcessError as error:
        print(f"git failed: {error.stderr.strip()}", file=sys.stderr)
        return 2
    if not changed:
        print(f"No Python lines changed under {', '.join(SCOPES)}.")
        return 0

    plan = []
    for path, lines in sorted(changed.items()):
        source = (ROOT / path).read_text(encoding="utf-8")
        try:
            found = mutants(path, source, lines)
        except SyntaxError as error:
            print(f"{path} does not parse: {error}", file=sys.stderr)
            return 2
        plan.append((path, found, tests_for(path, ROOT / "tests")))

    if args.list:
        for path, found, tests in plan:
            print(f"{path}: {len(found)} mutant(s); tests: {' '.join(tests) or 'none'}")
            for mutant in found:
                print(f"  {mutant.line}: {mutant.original}  ->  {mutant.mutated}")
        return 0

    try:
        listed = read_equivalents(EQUIVALENTS.read_text(encoding="utf-8"))
    except ValueError as error:
        print(f"{EQUIVALENTS_NAME}: {error}", file=sys.stderr)
        return 2

    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        survivors = run(plan, args.jobs)
    except _BaselineFailed as error:
        print(error, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted; the checkout was never edited", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous)
    return report(survivors, listed)


if __name__ == "__main__":
    sys.exit(main())

"""The PostToolUse formatting hook, `.claude/hooks/format-edited-python.sh`.

`.claude/settings.json` wires this script to run after every `Edit` and
`Write`, and its header states the contract twice over: it is the agent's
equivalent of the devcontainer's format-on-save, "so a change never arrives at
CI failing `ruff format --check` for a reason nobody had to think about", and
it "deliberately never fails the tool call ... Exits 0 on every path."

Both halves of that contract are invisible when they break. The script sends
`ruff`'s output to `/dev/null`, runs without `set -e`, and exits 0 whatever
happened, so a hook that has quietly stopped formatting looks exactly like a
hook that had nothing to do. The failure surfaces later, as a red
`ruff format --check` in `validate.yml` or as an import reorder nobody asked
for. Nothing executed the script: `.coveragerc` measures
`custom_components/sensi`, the file is bash, and the one test that mentions it
(`test_no_real_identifiers.py`) reads it as text in a credential scan.

The tests here run the committed script - by its own path, so the shebang and
the executable bit are part of what is under test - with the hook payload on
stdin exactly as Claude Code delivers it, and a hermetic `PATH` holding only
the tools the run is meant to have. Routing assertions use a recording `ruff`
stub and look at the recorded argument vector rather than at printed text,
because nothing the script prints survives its redirects. Behaviour
assertions use the real pinned `ruff` from `requirements_test.txt`, which is
what joins the hook's output to the `validate.yml` gate that judges it.

Two mutants are deliberately not claimed, because the missing `set -e` and the
surrounding redirects make their removal unobservable from outside the
process: dropping `command -v ruff >/dev/null 2>&1 || exit 0` (an absent
`ruff` then fails as a command not found, and the script still exits 0) and
dropping the `or {}` in the payload parse (the `python3` process then dies
with its stderr already discarded, and the empty capture takes the same exit
path). The tests below pin the behaviour those lines produce rather than
asserting that removing them is caught.
"""

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_HOOK = _ROOT / ".claude" / "hooks" / "format-edited-python.sh"
_SETTINGS = _ROOT / ".claude" / "settings.json"
_RUFF_CONFIG = _ROOT / "ruff.toml"

# The hook's own `ruff` invocations, in the order the script runs them. Import
# sorting first and only (`--select I`), then formatting; the header explains
# that a blanket `ruff check --fix` would delete "unused" imports behind the
# author's back.
_EXPECTED_CHECK = ["check", "--fix", "--select", "I", "--quiet"]
_EXPECTED_FORMAT = ["format", "--quiet"]

_BASH = shutil.which("bash")


def _real_ruff_argv() -> list[str]:
    """Return the command that runs the pinned ruff from `requirements_test.txt`.

    `pip install -r requirements_test.txt` puts a `ruff` console script on
    PATH, which is what CI sees. A local run started from a virtualenv's
    interpreter without its `bin` directory on PATH has the module but not the
    script, so fall back to `-m ruff` rather than skipping the behaviour
    tests - a skip here is indistinguishable from the gap this file closes.
    """

    found = shutil.which("ruff")
    if found:
        return [found]
    return [sys.executable, "-m", "ruff"]


class HookResult:
    """What one run of the hook produced."""

    def __init__(self, returncode: int, stdout: str, stderr: str, calls: list) -> None:
        """Store the exit status, both streams and the recorded `ruff` calls."""

        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = calls


def _tracked_files() -> set[str]:
    """Return every path git tracks, so an untracked artefact cannot vouch for a rule."""

    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {entry for entry in listing.split("\0") if entry}


def _tool_dir(
    tmp_path: Path,
    *,
    ruff: str,
    python3: bool = True,
    ruff_exit: int = 0,
) -> Path:
    """Build the only PATH directory a hook run will see.

    `ruff` is one of "stub" (a recorder), "real" (the pinned ruff from
    `requirements_test.txt`) or "absent". `bash` is always present because the
    script's `#!/usr/bin/env bash` line searches PATH for it, and `python3` is
    a passthrough shim that execs this interpreter rather than a symlink, so
    the test interpreter's environment survives.
    """

    bin_dir = tmp_path / f"bin-{ruff}-{python3}"
    if bin_dir.exists():
        shutil.rmtree(bin_dir)
    bin_dir.mkdir()

    assert _BASH is not None, "bash is required to run the hook"
    (bin_dir / "bash").symlink_to(_BASH)

    if python3:
        shim = bin_dir / "python3"
        shim.write_text(
            f"#!{sys.executable}\n"
            "import os\n"
            "import sys\n"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)

    if ruff == "stub":
        stub = bin_dir / "ruff"
        stub.write_text(
            f"#!{sys.executable}\n"
            "import json\n"
            "import os\n"
            "import sys\n"
            'with open(os.environ["RUFF_LOG"], "a", encoding="utf-8") as log:\n'
            '    log.write(json.dumps(sys.argv[1:]) + "\\n")\n'
            'sys.stdout.write("ruff stub stdout\\n")\n'
            'sys.stderr.write("ruff stub stderr\\n")\n'
            f"sys.exit({ruff_exit})\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
    elif ruff == "real":
        argv = _real_ruff_argv()
        shim = bin_dir / "ruff"
        shim.write_text(
            f"#!{sys.executable}\n"
            "import os\n"
            "import sys\n"
            f"argv = {argv!r}\n"
            "os.execv(argv[0], [*argv, *sys.argv[1:]])\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)
    elif ruff != "absent":
        raise AssertionError(f"unknown ruff mode {ruff!r}")

    return bin_dir


def _run_hook(
    payload: str,
    *,
    tmp_path: Path,
    ruff: str = "stub",
    ruff_exit: int = 0,
    python3: bool = True,
    cwd: Path | None = None,
) -> HookResult:
    """Feed `payload` to the committed hook on stdin and return what happened.

    The hook is executed by its own path rather than under `bash <file>`, so a
    lost executable bit or a broken shebang fails here too.
    """

    bin_dir = _tool_dir(tmp_path, ruff=ruff, python3=python3, ruff_exit=ruff_exit)
    log = tmp_path / "ruff.log"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)

    environment = {
        "PATH": str(bin_dir),
        "RUFF_LOG": str(log),
        # Keep ruff away from any user-level configuration on the machine
        # running the suite; the only ruff.toml that may influence a run is
        # the one a test puts in the workspace.
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
    }

    completed = subprocess.run(
        [str(_HOOK)],
        input=payload,
        cwd=str(cwd) if cwd is not None else str(tmp_path),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )

    calls = []
    if log.exists():
        calls = [
            json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
        ]
    return HookResult(completed.returncode, completed.stdout, completed.stderr, calls)


def _payload(file_path: str, *, tool: str = "Edit") -> str:
    """Return a PostToolUse payload of the shape Claude Code sends."""

    return json.dumps(
        {
            "session_id": "0000",
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": {"file_path": file_path},
            "tool_response": {"success": True},
        }
    )


@pytest.fixture(name="workspace")
def workspace_fixture(tmp_path: Path) -> Path:
    """Return an empty directory to hold the file an edit is pretending to touch."""

    space = tmp_path / "workspace"
    space.mkdir()
    return space


def test_the_hook_is_a_tracked_executable_file() -> None:
    """The script exists, is executable, and git records the mode bit.

    A hook wired by path stops running the moment the bit is lost, and git
    stores the mode, so a checkout on a filesystem that drops it still works
    only because the index says 100755.
    """

    assert _HOOK.is_file(), f"{_HOOK} is missing; these tests cover it"
    assert _HOOK.stat().st_mode & stat.S_IXUSR, f"{_HOOK} is not executable"

    recorded = subprocess.run(
        ["git", "ls-files", "-s", "--", str(_HOOK.relative_to(_ROOT))],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert recorded, "the hook is not tracked by git"
    assert recorded[0] == "100755", (
        f"git records mode {recorded[0]} for the hook; it must be 100755 or a "
        "fresh checkout cannot execute it"
    )


# --------------------------------------------------------------------------
# Routing: which file, if any, reaches ruff.
# --------------------------------------------------------------------------


def test_a_python_edit_runs_import_sorting_then_formatting(
    tmp_path: Path, workspace: Path
) -> None:
    """The two ruff calls, their exact argv, and their order.

    `--select I` is the whole point of the first call and the order matters:
    sorting after formatting would leave the file in a shape ruff format has
    not seen.
    """

    target = workspace / "module.py"
    target.write_text("import os\n", encoding="utf-8")

    result = _run_hook(_payload(str(target)), tmp_path=tmp_path)

    assert result.returncode == 0
    assert result.calls == [
        [*_EXPECTED_CHECK, str(target)],
        [*_EXPECTED_FORMAT, str(target)],
    ]


def test_the_hook_prints_nothing(tmp_path: Path, workspace: Path) -> None:
    """Neither stream carries anything, even when ruff is noisy on both.

    A PostToolUse hook's output is fed back to the agent, so the `>/dev/null
    2>&1` redirects are what keep a formatting run from becoming transcript.
    """

    target = workspace / "module.py"
    target.write_text("import os\n", encoding="utf-8")

    result = _run_hook(_payload(str(target)), tmp_path=tmp_path)

    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "name",
    [
        "notes.md",
        "settings.json",
        "script.sh",
        "module.pyi",
        "module.pyc",
        "py",
        "module.py.bak",
        "PLAIN",
    ],
)
def test_a_non_python_edit_never_reaches_ruff(
    tmp_path: Path, workspace: Path, name: str
) -> None:
    """The `*.py` case arm is what keeps ruff off everything else.

    `.pyi` and `.pyc` are the interesting rows: a suffix test looser than
    `*.py` catches both, and ruff rewriting a stub or a byte-compiled file is
    not what the hook is for.
    """

    target = workspace / name
    target.write_text("import os\n", encoding="utf-8")

    result = _run_hook(_payload(str(target)), tmp_path=tmp_path)

    assert result.returncode == 0
    assert result.calls == []


def test_a_python_path_that_no_longer_exists_is_skipped(
    tmp_path: Path, workspace: Path
) -> None:
    """`[ -f ]` covers the edit that deleted or renamed the file it names."""

    result = _run_hook(_payload(str(workspace / "gone.py")), tmp_path=tmp_path)

    assert result.returncode == 0
    assert result.calls == []


def test_a_directory_named_like_a_module_is_skipped(
    tmp_path: Path, workspace: Path
) -> None:
    """`[ -f ]` and not `[ -e ]`: a directory ending in .py is not a file to format."""

    directory = workspace / "package.py"
    directory.mkdir()

    result = _run_hook(_payload(str(directory)), tmp_path=tmp_path)

    assert result.returncode == 0
    assert result.calls == []


def test_a_path_containing_spaces_is_passed_as_one_argument(
    tmp_path: Path, workspace: Path
) -> None:
    """The quoting around `"$file_path"`.

    Unquoted, this path word-splits and ruff is handed three arguments that
    name nothing; with the redirects in place the hook still exits 0 and the
    file is silently left unformatted.
    """

    target = workspace / "a file with spaces.py"
    target.write_text("import os\n", encoding="utf-8")

    result = _run_hook(_payload(str(target)), tmp_path=tmp_path)

    assert result.returncode == 0
    assert [call[-1] for call in result.calls] == [str(target), str(target)]
    assert [len(call) for call in result.calls] == [6, 3]


def test_the_file_path_comes_from_tool_input(tmp_path: Path, workspace: Path) -> None:
    """A `file_path` outside `tool_input` is not the field the hook reads."""

    target = workspace / "module.py"
    target.write_text("import os\n", encoding="utf-8")
    payload = json.dumps(
        {
            "tool_name": "Edit",
            "file_path": str(target),
            "tool_input": {"command": "ls"},
        }
    )

    result = _run_hook(payload, tmp_path=tmp_path)

    assert result.returncode == 0
    assert result.calls == []


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
def test_the_hook_does_not_filter_on_the_tool_name(
    tmp_path: Path, workspace: Path, tool: str
) -> None:
    """Matching is the settings file's job, not the script's.

    The script looks only at the path, which is why narrowing the matcher in
    `.claude/settings.json` is enough to disable the hook without touching
    this file - see the settings tests below.
    """

    target = workspace / "module.py"
    target.write_text("import os\n", encoding="utf-8")

    result = _run_hook(_payload(str(target), tool=tool), tmp_path=tmp_path)

    assert len(result.calls) == 2


# --------------------------------------------------------------------------
# The never-fail contract.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("empty stdin", ""),
        ("not json", "this is not json"),
        ("truncated json", '{"tool_input": {"file_path":'),
        ("json null", "null"),
        ("json list", "[]"),
        ("json string", '"hello"'),
        ("no tool_input", '{"tool_name": "Edit"}'),
        ("null tool_input", '{"tool_name": "Edit", "tool_input": null}'),
        ("tool_input not a map", '{"tool_name": "Edit", "tool_input": "x"}'),
        ("no file_path", '{"tool_name": "Edit", "tool_input": {"command": "ls"}}'),
        ("null file_path", '{"tool_name": "Edit", "tool_input": {"file_path": null}}'),
        ("empty file_path", '{"tool_name": "Edit", "tool_input": {"file_path": ""}}'),
    ],
)
def test_a_payload_the_hook_cannot_use_is_a_silent_no_op(
    tmp_path: Path, label: str, payload: str
) -> None:
    """Every malformed shape ends as exit 0, no ruff call, no output.

    "Exits 0 on every path" is the header's promise; a non-zero exit from a
    PostToolUse hook is reported against the edit that triggered it.
    """

    result = _run_hook(payload, tmp_path=tmp_path)

    assert result.returncode == 0, f"{label} produced exit {result.returncode}"
    assert result.calls == [], f"{label} reached ruff"
    assert result.stdout == "", f"{label} printed to stdout"
    assert result.stderr == "", f"{label} printed to stderr"


def test_a_failing_ruff_does_not_fail_the_tool_call(
    tmp_path: Path, workspace: Path
) -> None:
    """No `set -e`, so a ruff that exits non-zero still leaves the hook at 0.

    Adding `-e` to the `set -uo pipefail` line would turn a file that does not
    parse mid-edit into a failed tool call, which the header rules out.
    """

    target = workspace / "module.py"
    target.write_text("import os\n", encoding="utf-8")

    result = _run_hook(_payload(str(target)), tmp_path=tmp_path, ruff_exit=2)

    assert result.returncode == 0
    # Both calls are made: the failing sort does not stop the format.
    assert len(result.calls) == 2


def test_a_python_file_that_does_not_parse_is_a_silent_no_op(
    tmp_path: Path, workspace: Path
) -> None:
    """Real ruff on a syntactically broken file: exit 0, file untouched.

    This is the case the missing `set -e` exists for - an agent's edit is
    routinely observed halfway through.
    """

    target = workspace / "broken.py"
    source = "def f(:\n    return 1\n"
    target.write_text(source, encoding="utf-8")

    result = _run_hook(_payload(str(target)), tmp_path=tmp_path, ruff="real")

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert target.read_text(encoding="utf-8") == source


def test_without_ruff_installed_the_hook_is_a_no_op(
    tmp_path: Path, workspace: Path
) -> None:
    """A machine with no ruff gets a silent success, not a failed edit.

    This pins the behaviour, not the guard: with no `set -e`, removing
    `command -v ruff || exit 0` leaves an unfound command and the same exit 0,
    so the line is an equivalent mutant.
    """

    target = workspace / "module.py"
    target.write_text("import os\n", encoding="utf-8")

    result = _run_hook(_payload(str(target)), tmp_path=tmp_path, ruff="absent")

    assert result.returncode == 0
    assert result.calls == []
    assert result.stdout == ""
    assert result.stderr == ""


def test_without_python3_the_hook_is_a_no_op(tmp_path: Path, workspace: Path) -> None:
    """The payload parser is `python3`; without one there is no file path to act on."""

    target = workspace / "module.py"
    target.write_text("import os\n", encoding="utf-8")

    result = _run_hook(
        _payload(str(target)), tmp_path=tmp_path, python3=False, ruff="stub"
    )

    assert result.returncode == 0
    assert result.calls == []


# --------------------------------------------------------------------------
# Behaviour, against the real pinned ruff: what the hook writes has to be what
# `validate.yml` accepts.
# --------------------------------------------------------------------------


_UNSORTED = '''"""A module."""

import sys
import json


def dump(value: object) -> str:
    """Return the value as JSON."""

    return json.dumps(value) + sys.prefix[:0]
'''


def test_imports_are_sorted_in_place(tmp_path: Path, workspace: Path) -> None:
    """The hook's whole reason to exist: `--select I` rewrites the file."""

    target = workspace / "module.py"
    target.write_text(_UNSORTED, encoding="utf-8")

    result = _run_hook(_payload(str(target)), tmp_path=tmp_path, ruff="real")

    assert result.returncode == 0
    body = target.read_text(encoding="utf-8")
    assert body.index("import json") < body.index("import sys")


def test_the_result_passes_the_gate_validate_yml_runs(
    tmp_path: Path, workspace: Path
) -> None:
    """After the hook, `ruff format --check` is clean.

    That command is `validate.yml`'s `ruff format` step, and agreeing with it
    is the stated purpose of the hook.
    """

    target = workspace / "module.py"
    target.write_text(
        '"""A module."""\n\nimport sys\nimport json\n\n\n'
        "def dump( value ):\n"
        '    """Return the value as JSON."""\n\n'
        "    return json.dumps(value)+sys.prefix[:0]\n",
        encoding="utf-8",
    )
    shutil.copy(_RUFF_CONFIG, workspace / "ruff.toml")

    result = _run_hook(_payload(str(target)), tmp_path=tmp_path, ruff="real")
    assert result.returncode == 0

    verify = subprocess.run(
        [*_real_ruff_argv(), "format", "--check", str(target)],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_an_unused_import_survives(tmp_path: Path, workspace: Path) -> None:
    """`--select I` and not a blanket `--fix`.

    The header calls deleting an "unused" import behind the author's back a
    semantic change no silent hook should make; widening the select list is
    the mutation this catches.
    """

    target = workspace / "module.py"
    target.write_text(
        '"""A module."""\n\nimport json\nimport sys\n\n\n'
        "def dump(value: object) -> str:\n"
        '    """Return the value as JSON."""\n\n'
        "    return json.dumps(value)\n",
        encoding="utf-8",
    )

    result = _run_hook(_payload(str(target)), tmp_path=tmp_path, ruff="real")

    assert result.returncode == 0
    assert "import sys" in target.read_text(encoding="utf-8")


def _first_import(target: Path) -> str:
    """Return the first import line of a formatted module."""

    for line in target.read_text(encoding="utf-8").splitlines():
        if line.startswith(("import ", "from ")):
            return line
    raise AssertionError(f"{target} has no import line")


_SPLIT_SECTIONS = '''"""A module."""

import sys
from pathlib import Path


def where() -> Path:
    """Return the interpreter path."""

    return Path(sys.executable)
'''


def test_the_committed_ruff_toml_is_what_decides_the_import_order(
    tmp_path: Path, workspace: Path
) -> None:
    """Config discovery is live, and it is the repository's config that is found.

    The hook passes neither `--config` nor `--isolated`, so its output agrees
    with CI only because ruff walks up from the file and finds the committed
    `ruff.toml`. `force-sort-within-sections = true` puts `from pathlib
    import Path` ahead of `import sys`; ruff's default puts straight imports
    first. Adding `--isolated` to the hook would make it format files into a
    shape `validate.yml` rejects, and both halves of this test are needed to
    see that - without the "no config" half, a hook that ignores the config
    could still produce the expected order by accident.
    """

    isolated = workspace / "isolated"
    isolated.mkdir()
    bare = isolated / "module.py"
    bare.write_text(_SPLIT_SECTIONS, encoding="utf-8")

    configured = workspace / "configured"
    configured.mkdir()
    shutil.copy(_RUFF_CONFIG, configured / "ruff.toml")
    with_config = configured / "module.py"
    with_config.write_text(_SPLIT_SECTIONS, encoding="utf-8")

    assert (
        _run_hook(_payload(str(bare)), tmp_path=tmp_path, ruff="real").returncode == 0
    )
    assert (
        _run_hook(_payload(str(with_config)), tmp_path=tmp_path, ruff="real").returncode
        == 0
    )

    assert _first_import(bare) == "import sys"
    assert _first_import(with_config) == "from pathlib import Path"


def test_config_is_resolved_from_the_edited_file_not_the_hook_cwd(
    tmp_path: Path, workspace: Path
) -> None:
    """The hook runs from wherever the agent was; ruff walks up from the file.

    A hook that resolved config against its own working directory would format
    a file in the repository correctly only when the agent happened to be
    standing in it.
    """

    project = workspace / "project"
    project.mkdir()
    shutil.copy(_RUFF_CONFIG, project / "ruff.toml")
    target = project / "module.py"
    target.write_text(_SPLIT_SECTIONS, encoding="utf-8")

    elsewhere = workspace / "elsewhere"
    elsewhere.mkdir()

    result = _run_hook(
        _payload(str(target)), tmp_path=tmp_path, ruff="real", cwd=elsewhere
    )

    assert result.returncode == 0
    assert _first_import(target) == "from pathlib import Path"


def test_the_hook_leaves_an_already_formatted_file_alone(
    tmp_path: Path, workspace: Path
) -> None:
    """Idempotence: running the hook on its own output changes nothing.

    A hook that rewrote a file on every edit would show up as churn in every
    diff the agent produces.
    """

    shutil.copy(_RUFF_CONFIG, workspace / "ruff.toml")
    target = workspace / "module.py"
    target.write_text(_UNSORTED, encoding="utf-8")

    assert (
        _run_hook(_payload(str(target)), tmp_path=tmp_path, ruff="real").returncode == 0
    )
    once = target.read_text(encoding="utf-8")
    assert (
        _run_hook(_payload(str(target)), tmp_path=tmp_path, ruff="real").returncode == 0
    )

    assert target.read_text(encoding="utf-8") == once


# --------------------------------------------------------------------------
# The wiring in .claude/settings.json. The script can be perfect and never run.
# --------------------------------------------------------------------------


def _settings() -> dict:
    """Return the parsed settings file."""

    return json.loads(_SETTINGS.read_text(encoding="utf-8"))


def _hook_entries(event: str) -> list[dict]:
    """Return every command entry registered for a hook event."""

    entries = []
    for block in _settings().get("hooks", {}).get(event, []):
        for entry in block.get("hooks", []):
            entries.append({"matcher": block.get("matcher"), **entry})
    return entries


def test_the_formatting_hook_is_registered_for_post_tool_use() -> None:
    """Exactly one PostToolUse command, and it is this script.

    The command is written against `$CLAUDE_PROJECT_DIR`; a relative path here
    resolves against whatever directory the tool call ran in, which is not
    reliably the repository root.
    """

    entries = _hook_entries("PostToolUse")
    assert len(entries) == 1, f"expected one PostToolUse hook, got {entries}"
    entry = entries[0]

    assert entry["type"] == "command"
    assert entry["command"] == (
        "$CLAUDE_PROJECT_DIR/.claude/hooks/format-edited-python.sh"
    )


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("Edit", True),
        ("Write", True),
        ("Read", False),
        ("Bash", False),
        ("NotebookEdit", False),
        ("MultiEdit", False),
    ],
)
def test_the_matcher_selects_the_file_writing_tools(tool: str, expected: bool) -> None:
    """The matcher is a regex, and a narrowed one disables the hook silently.

    `NotebookEdit` and `MultiEdit` are the rows that matter in both
    directions: they are excluded today, and a matcher rewritten as a bare
    substring (`Edit`) would start matching them without anyone deciding to.
    """

    matcher = _hook_entries("PostToolUse")[0]["matcher"]
    assert bool(re.fullmatch(matcher, tool)) is expected


def test_every_registered_hook_command_names_a_tracked_executable() -> None:
    """The `$CLAUDE_PROJECT_DIR` path resolves, and the target can be run.

    Renaming or moving the script leaves this string pointing at nothing; the
    hook then fails to start, and Claude Code carries on, so nothing tells
    anybody.
    """

    tracked = _tracked_files()
    entries = [
        entry
        for event in _settings().get("hooks", {})
        for entry in _hook_entries(event)
    ]
    assert entries, "settings.json registers no hooks; these tests cover one"

    for entry in entries:
        command = entry["command"]
        assert command.startswith("$CLAUDE_PROJECT_DIR/"), (
            f"hook command {command!r} is not anchored to the project directory"
        )
        relative = command[len("$CLAUDE_PROJECT_DIR/") :]
        assert relative in tracked, f"hook command {command!r} names an untracked path"
        resolved = _ROOT / relative
        assert os.access(resolved, os.X_OK), f"{relative} is not executable"


def test_hook_directory_cannot_be_edited_by_claude_code() -> None:
    """The command hook is a security boundary, not ordinary source code.

    Claude Code executes this script after every Edit and Write without a
    separate permission check. Denying edits to its directory prevents a
    session that can edit the checkout from replacing the command it runs.
    """

    assert "Edit(.claude/hooks/**)" in _settings()["permissions"]["deny"]


_PATH_RULE = re.compile(r"^(?:Edit|Write|Read)\((?P<path>[^)]+)\)$")
_BASH_PATH = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+)")


def _rule_paths(rule: str) -> list[str]:
    """Return the repository paths a permission rule names, normalised."""

    found = []
    match = _PATH_RULE.match(rule)
    if match:
        found.append(match.group("path"))
    elif rule.startswith("Bash("):
        found.extend(_BASH_PATH.findall(rule[len("Bash(") : -1]))

    normalised = []
    for path in found:
        path = path.removeprefix("./").removesuffix(":*")
        path = re.sub(r"/\*\*$", "", path).removesuffix("/*")
        if path and not path.startswith("*"):
            normalised.append(path)
    return normalised


def test_allowed_and_asked_permission_rules_name_paths_that_exist() -> None:
    """A rule pointing at a renamed path reads as "still guarded" and guards nothing.

    `allow` and `ask` name four concrete repository paths today; each must
    still be tracked, either as a file or as a directory with tracked
    contents.
    """

    tracked = _tracked_files()
    directories = {
        parent for path in tracked for parent in (str(p) for p in Path(path).parents)
    }
    permissions = _settings()["permissions"]

    checked = 0
    for section in ("allow", "ask"):
        for rule in permissions[section]:
            for path in _rule_paths(rule):
                checked += 1
                assert path in tracked or path in directories, (
                    f"{section} rule {rule!r} names {path!r}, which git does not track"
                )

    assert checked >= 4, (
        f"only {checked} concrete paths were checked; the rule parser has "
        "stopped seeing the paths in settings.json"
    )


def test_denied_permission_rules_name_paths_that_stay_absent() -> None:
    """The deny list guards files this repository deliberately does not have.

    `pyproject.toml` is the interesting one: `ruff.toml`'s header explains
    that the devcontainer image supplies a `pyproject.toml`, so one appearing
    here would silently take over lint configuration. Checking that these stay
    absent is what makes the deny rules more than decoration.
    """

    tracked = _tracked_files()
    permissions = _settings()["permissions"]

    checked = 0
    for rule in permissions["deny"]:
        if rule in {
            "Edit(.claude/hooks/**)",
            "Edit(.claude/settings.json)",
            "Edit(docs/SECURITY-AI.md)",
        }:
            continue
        for path in _rule_paths(rule):
            checked += 1
            assert path not in tracked, (
                f"deny rule {rule!r} names {path!r}, which is now tracked; the "
                "rule and this test need a decision, not a quiet pass"
            )
            assert not (_ROOT / path).exists(), (
                f"deny rule {rule!r} names {path!r}, which exists in the tree"
            )

    assert checked >= 4, (
        f"only {checked} denied paths were checked; the rule parser has "
        "stopped seeing the paths in settings.json"
    )

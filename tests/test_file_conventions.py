"""`.editorconfig` and `.gitattributes` decide the shape of every file here.

Both are read by tools nobody in this repository runs: an editor, and git's
own checkout and diff machinery. Neither is read by a test, so both have been
free to say things the tree does not do.

`.editorconfig` says so itself - "mirrors what ruff.toml and the Home
Assistant conventions already enforce, so an editor without the ruff
extension still produces conforming files". That is a restatement of a
machine-readable value, exactly like the prose ones
`tests/test_instruction_docs.py` joins, except this copy is the one a
contributor's editor actually obeys. When it drifts from `ruff.toml` the
editor produces a file CI then rejects, and the contributor is told their
correctly configured editor is wrong.

`.gitattributes` is the more dangerous of the two, because `* text eol=lf`
normalises the line endings of *every* path git does not consider binary.
The `binary` lines below it are what keeps that from rewriting the bytes of
an image; a file type added without one is corrupted on checkout, quietly,
and only on the platforms the rule exists for.

This module is the join. Every assertion resolves what the two files declare
and checks it against what the committed tree and git itself do:

- the declared line length against `ruff.toml`
- the declared end-of-line against the attribute git computes for a path
- charset, final newline, trailing whitespace and indent style against the
  bytes of every tracked text file
- every literal path `.gitattributes` names against `git ls-files`
- `.devcontainer/devcontainer.json` - the file whose indentation started
  this - against the `Dockerfile` it points at

Three conventions carried from `tests/test_codeowners.py` and
`tests/test_instruction_docs.py`:

- Sections are resolved in file order, last match wins, because that is how
  EditorConfig itself resolves them. A test that read the first match would
  pass while an editor did something else.
- The pattern matcher is hand-rolled and carries its own case table.
  `fnmatch` is wrong here (its `*` crosses `/`), and every other assertion
  quantifies over lists this matcher builds - a matcher stuck at True or
  False would turn the module green either way.
- Every scan asserts how much it found before asserting anything about it.

What is deliberately NOT asserted: that a section matches any committed file
(an editor config legitimately pre-declares conventions for file types the
tree does not have yet), that Python indentation is a multiple of four (a
triple-quoted string may be indented however its content needs), that every
Python line fits in the declared width (ruff selects no line-length lint rule,
so `line-length` drives the formatter only and the tree has longer docstring
lines), or that the `binary` list is minimal.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
import re
import subprocess
import tomllib

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_EDITORCONFIG = _ROOT / ".editorconfig"
_GITATTRIBUTES = _ROOT / ".gitattributes"
_RUFF_TOML = _ROOT / "ruff.toml"
_DEVCONTAINER = _ROOT / ".devcontainer" / "devcontainer.json"
_DOCKERFILE = _ROOT / "Dockerfile"

# git's own heuristic: a NUL byte in the first 8000 bytes means binary.
_BINARY_SNIFF_BYTES = 8000


# --------------------------------------------------------------------------
# EditorConfig glob matching
#
# Hand-rolled on purpose. The subset implemented is the subset the committed
# file uses; anything outside it raises rather than resolving to something
# plausible, so a section this matcher cannot read is a loud failure instead
# of a silently ignored rule.
# --------------------------------------------------------------------------


def _translate(pattern: str) -> str:
    """Return the regex body for an EditorConfig pattern fragment."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "{":
            close = pattern.find("}", index)
            if close == -1:
                raise ValueError(f"unbalanced brace in {pattern!r}")
            choices = pattern[index + 1 : close].split(",")
            out.append("(?:" + "|".join(_translate(c) for c in choices) + ")")
            index = close + 1
        elif char == "*":
            if pattern.startswith("**", index):
                out.append(".*")
                index += 2
            else:
                out.append("[^/]*")
                index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        elif char in "[]!":
            raise ValueError(f"character class in {pattern!r} is not supported")
        else:
            out.append(re.escape(char))
            index += 1
    return "".join(out)


@functools.cache
def _compile(pattern: str) -> re.Pattern[str]:
    """Return the compiled matcher for one EditorConfig section header."""
    if pattern.startswith("/"):
        body = _translate(pattern[1:])
    elif "/" in pattern:
        body = _translate(pattern)
    else:
        # A pattern with no separator at all floats to any depth.
        body = "(?:.*/)?" + _translate(pattern)
    return re.compile(f"{body}\\Z")


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


@functools.cache
def _preamble_and_sections() -> tuple[dict[str, str], list[tuple[str, dict[str, str]]]]:
    """Return `.editorconfig` as its preamble pairs and its sections, in order."""
    preamble: dict[str, str] = {}
    sections: list[tuple[str, dict[str, str]]] = []
    for raw in _read(_EDITORCONFIG).splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            sections.append((line[1:-1], {}))
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"unparsable .editorconfig line: {raw!r}")
        target = sections[-1][1] if sections else preamble
        target[key.strip().lower()] = value.strip()
    return preamble, sections


@functools.cache
def _resolve(relpath: str, key: str) -> str | None:
    """Return the value EditorConfig gives `key` for `relpath`, last match winning."""
    _, sections = _preamble_and_sections()
    found: str | None = None
    for pattern, pairs in sections:
        if key in pairs and _compile(pattern).match(relpath):
            found = pairs[key]
    return found


def _tracked() -> list[str]:
    """Return every path git tracks, read from the index rather than a walk."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [path for path in out.split("\0") if path]


def _check_attr(attribute: str, paths: list[str]) -> dict[str, str]:
    """Return git's computed value of `attribute` for each path."""
    out = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", attribute],
        cwd=_ROOT,
        input="\0".join(paths),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    fields = [field for field in out.split("\0") if field != ""]
    return {fields[index]: fields[index + 2] for index in range(0, len(fields) - 2, 3)}


def _is_binary(path: str) -> bool:
    """Return whether git would treat the committed bytes as binary."""
    return b"\0" in (_ROOT / path).read_bytes()[:_BINARY_SNIFF_BYTES]


def _text_files() -> list[str]:
    """Return every tracked path whose bytes are text."""
    return [path for path in _tracked() if not _is_binary(path)]


def _gitattributes_rules() -> list[tuple[str, list[str]]]:
    """Return `.gitattributes` as (pattern, attributes) pairs, in order."""
    rules: list[tuple[str, list[str]]] = []
    for raw in _read(_GITATTRIBUTES).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *attributes = line.split()
        rules.append((pattern, attributes))
    return rules


_TRACKED = _tracked()
_TEXT_FILES = _text_files()
_BINARY_FILES = [path for path in _TRACKED if _is_binary(path)]


# --------------------------------------------------------------------------
# The matcher's own case table
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("*", "README.md", True),
        ("*", "docs/risk-tiers.md", True),
        ("*.py", "custom_components/sensi/auth.py", True),
        ("*.py", "auth.py", True),
        ("*.py", "auth.pyi", False),
        ("*.py", "py", False),
        ("*.{json,yml}", "hacs.json", True),
        ("*.{json,yml}", ".github/workflows/ci.yml", True),
        ("*.{json,yml}", "README.md", False),
        ("Makefile", "Makefile", True),
        ("Makefile", "docs/Makefile", True),
        ("Makefile", "Makefile.in", False),
        ("/Makefile", "Makefile", True),
        # An anchored pattern does not float, and `*` does not cross `/`.
        ("/Makefile", "docs/Makefile", False),
        ("docs/*.md", "docs/quality.md", True),
        ("docs/*.md", "docs/reflections/a.md", False),
        ("docs/**.md", "docs/reflections/a.md", True),
        ("?.py", "a.py", True),
        ("?.py", "ab.py", False),
    ],
)
def test_the_pattern_matcher_answers_its_case_table(
    pattern: str, path: str, expected: bool
) -> None:
    """Prove the matcher every other assertion rests on is not stuck."""
    assert bool(_compile(pattern).match(path)) is expected


def test_the_pattern_matcher_refuses_syntax_it_does_not_implement() -> None:
    """Reject a character class rather than resolving it to something plausible."""
    with pytest.raises(ValueError):
        _compile("*.[ch]")


# --------------------------------------------------------------------------
# The scans are not empty
# --------------------------------------------------------------------------


def test_the_tree_scan_finds_the_repository() -> None:
    """Every assertion below quantifies over these lists."""
    assert len(_TRACKED) > 100, f"git ls-files returned {len(_TRACKED)} paths"
    assert len(_TEXT_FILES) > 100
    assert _BINARY_FILES == ["images/image.png"], (
        "the binary set changed; every entry needs a `binary` attribute in "
        ".gitattributes or `* text eol=lf` will rewrite its bytes"
    )


def test_editorconfig_declares_sections() -> None:
    """A file that parses to nothing would satisfy every resolution below."""
    preamble, sections = _preamble_and_sections()
    assert preamble, ".editorconfig has no settings before its first section"
    assert len(sections) >= 4, f".editorconfig parsed to {len(sections)} sections"


# --------------------------------------------------------------------------
# .editorconfig: authority and ordering
# --------------------------------------------------------------------------


def test_editorconfig_is_the_root_of_the_search() -> None:
    """Without `root = true` an editor keeps walking up past the checkout."""
    preamble, _ = _preamble_and_sections()
    assert preamble.get("root") == "true", (
        "`root = true` is what stops EditorConfig reading a file outside the "
        "repository; without it what a contributor's editor does depends on "
        "where they cloned"
    )


def test_the_catch_all_section_comes_first() -> None:
    """Last match wins, so `[*]` decides nothing unless every override follows it."""
    _, sections = _preamble_and_sections()
    patterns = [pattern for pattern, _ in sections]
    assert patterns[0] == "*", f".editorconfig starts with [{patterns[0]}], not [*]"


def test_every_override_follows_the_section_it_overrides() -> None:
    """A narrower section placed above a wider one is silently overruled."""
    _, sections = _preamble_and_sections()
    verified = 0
    for index, (early_pattern, early_pairs) in enumerate(sections):
        for late_pattern, late_pairs in sections[index + 1 :]:
            for key in set(early_pairs) & set(late_pairs):
                if early_pairs[key] == late_pairs[key]:
                    continue
                # Two sections that share no path disagree about nothing, and
                # neither does a section matching no committed path at all.
                late = _compile(late_pattern)
                early = _compile(early_pattern)
                sample = [path for path in _TRACKED if late.match(path)]
                if not any(early.match(path) for path in sample):
                    continue
                # The later section wins. That is only a refinement while it
                # matches strictly fewer paths than the one it overrules.
                verified += 1
                assert all(early.match(path) for path in sample), (
                    f"[{late_pattern}] sets {key}={late_pairs[key]!r} for paths "
                    f"[{early_pattern}] does not match; the two sections "
                    "disagree and the later one wins by position alone"
                )
    assert verified >= 2, (
        f"only {verified} overrides were checkable; too few to mean anything"
    )


# --------------------------------------------------------------------------
# .editorconfig against ruff.toml
# --------------------------------------------------------------------------


def test_python_max_line_length_matches_ruff_toml() -> None:
    """The editor's column ruler and the lint gate must be the same number."""
    ruff = tomllib.loads(_read(_RUFF_TOML))
    declared = _resolve("custom_components/sensi/auth.py", "max_line_length")
    assert declared is not None, ".editorconfig no longer states a line length"
    assert int(declared) == ruff["line-length"], (
        f".editorconfig wraps Python at {declared} columns, ruff.toml sets "
        f"{ruff['line-length']}; an editor obeying the first produces a file "
        "`ruff format --check` rejects"
    )


def test_the_declared_line_length_is_the_one_the_formatter_uses() -> None:
    """`line-length` drives `ruff format`; E501 is not in the select list."""
    ruff = tomllib.loads(_read(_RUFF_TOML))
    assert not any(
        rule.startswith("E5") or rule == "E" for rule in ruff["lint"]["select"]
    ), (
        "ruff now selects a line-length lint rule, so max_line_length in "
        ".editorconfig has become a gate rather than a formatting hint; the "
        "tree has committed docstrings longer than it"
    )


# --------------------------------------------------------------------------
# .editorconfig against .gitattributes and the committed bytes
# --------------------------------------------------------------------------


def test_declared_end_of_line_matches_the_attribute_git_computes() -> None:
    """The editor writes the endings; git enforces them. They must agree."""
    declared = _resolve("README.md", "end_of_line")
    assert declared == "lf", f".editorconfig declares end_of_line={declared!r}"
    computed = _check_attr("eol", ["README.md", "custom_components/sensi/auth.py"])
    assert set(computed.values()) == {"lf"}, (
        f"git computes eol={computed}; .gitattributes and .editorconfig "
        "disagree about line endings"
    )


def test_gitattributes_normalises_every_path_by_default() -> None:
    """`* text eol=lf` is what makes the `binary` lines below it load-bearing."""
    rules = _gitattributes_rules()
    assert ("*", ["text", "eol=lf"]) in rules, (
        "the blanket text rule is gone; with it goes the reason every binary "
        f"file type needs a `binary` line. Rules read: {rules}"
    )


def test_no_tracked_text_file_contains_a_carriage_return() -> None:
    """What `eol=lf` promises, checked against the bytes that are committed."""
    offenders = [path for path in _TEXT_FILES if b"\r" in (_ROOT / path).read_bytes()]
    assert not offenders, f"CRLF survived normalisation in {offenders}"


def test_every_binary_file_is_marked_binary() -> None:
    """An unmarked binary file is rewritten by `* text eol=lf` on checkout."""
    computed = _check_attr("binary", _BINARY_FILES)
    unmarked = [path for path, value in computed.items() if value != "set"]
    assert not unmarked, (
        f"{unmarked} hold NUL bytes but git does not treat them as binary; "
        ".gitattributes needs a rule for that file type"
    )


def test_gitattributes_names_no_path_that_left_the_tree() -> None:
    """A rule for a deleted file is dead weight that reads as coverage."""
    tracked = set(_TRACKED)
    literals = [
        pattern
        for pattern, _ in _gitattributes_rules()
        if not set(pattern) & set("*?[]{}")
    ]
    missing = [pattern for pattern in literals if pattern not in tracked]
    assert not missing, (
        f".gitattributes names {missing}, which git tracks no file at; the "
        "rule applies to nothing"
    )


def test_python_whitespace_errors_are_attributed_to_python_files() -> None:
    """`*.py whitespace=error` is the git-side half of trim_trailing_whitespace."""
    computed = _check_attr("whitespace", ["custom_components/sensi/auth.py"])
    assert "error" in computed["custom_components/sensi/auth.py"], (
        f"git computes whitespace={computed} for a Python file; "
        "`git diff --check` no longer flags trailing whitespace there"
    )


def test_every_text_file_decodes_as_the_declared_charset() -> None:
    """`charset = utf-8` is a claim about bytes that are already committed."""
    declared = _resolve("README.md", "charset")
    assert declared == "utf-8", f".editorconfig declares charset={declared!r}"
    offenders = []
    for path in _TEXT_FILES:
        try:
            (_ROOT / path).read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            offenders.append(path)
    assert not offenders, f"{offenders} are not valid UTF-8"


def test_every_text_file_ends_with_a_newline() -> None:
    """`insert_final_newline = true`, checked against the tree that declares it."""
    assert _resolve("README.md", "insert_final_newline") == "true"
    offenders = [
        path
        for path in _TEXT_FILES
        if (_ROOT / path).read_bytes()
        and not (_ROOT / path).read_bytes().endswith(b"\n")
    ]
    assert not offenders, (
        f"{offenders} end without a newline; every diff that touches their "
        "last line shows it as rewritten"
    )


def test_trailing_whitespace_is_trimmed_where_editorconfig_trims_it() -> None:
    """Markdown is exempt on purpose; nothing else is."""
    trimmed = 0
    exempt = 0
    offenders = []
    for path in _TEXT_FILES:
        if _resolve(path, "trim_trailing_whitespace") != "true":
            exempt += 1
            continue
        trimmed += 1
        for number, line in enumerate(_read(_ROOT / path).splitlines(), start=1):
            if line != line.rstrip():
                offenders.append(f"{path}:{number}")
    assert not offenders, f"trailing whitespace in {offenders[:10]}"
    assert trimmed > 50, f"only {trimmed} files resolved to trim_trailing_whitespace"
    assert exempt > 0, "no file is exempt; the markdown override resolved to nothing"


def test_markdown_keeps_its_trailing_whitespace() -> None:
    """Two trailing spaces are a line break; trimming them rewrites the prose."""
    assert _resolve("README.md", "trim_trailing_whitespace") == "false", (
        "the [*.md] override is gone or no longer follows [*]; an editor will "
        "now strip markdown line breaks"
    )


def test_indentation_uses_the_declared_style() -> None:
    """`indent_style = space` against the first character of every indented line."""
    assert _resolve("hacs.json", "indent_style") == "space"
    offenders = []
    for path in _TEXT_FILES:
        if _resolve(path, "indent_style") != "space":
            continue
        for number, line in enumerate(_read(_ROOT / path).splitlines(), start=1):
            if line.startswith("\t"):
                offenders.append(f"{path}:{number}")
                break
    assert not offenders, f"tab-indented lines in {offenders}"


def test_json_indentation_is_a_multiple_of_the_declared_size() -> None:
    """JSON cannot carry a multi-line string, so every indent is structural."""
    checked = 0
    for path in _TEXT_FILES:
        if not path.endswith(".json"):
            continue
        size = _resolve(path, "indent_size")
        assert size is not None, f"no indent_size resolves for {path}"
        checked += 1
        for number, line in enumerate(_read(_ROOT / path).splitlines(), start=1):
            indent = len(line) - len(line.lstrip(" "))
            assert indent % int(size) == 0, (
                f"{path}:{number} is indented {indent} columns, which is not a "
                f"multiple of the declared {size}"
            )
    assert checked >= 8, f"only {checked} JSON files were checked"


# --------------------------------------------------------------------------
# The devcontainer, which is where the drift showed
# --------------------------------------------------------------------------


def test_the_devcontainer_points_at_the_committed_dockerfile() -> None:
    """`dockerFile` is a path nothing else resolves."""
    config = json.loads(_read(_DEVCONTAINER))
    referenced = (_DEVCONTAINER.parent / config["dockerFile"]).resolve()
    assert referenced == _DOCKERFILE.resolve(), (
        f"devcontainer.json builds {config['dockerFile']}, which is not the "
        "committed Dockerfile"
    )
    assert str(referenced.relative_to(_ROOT)) in _TRACKED


def test_the_devcontainer_build_context_is_the_repository_root() -> None:
    """A context below the root cannot see the component it is meant to mount."""
    config = json.loads(_read(_DEVCONTAINER))
    context = (_DEVCONTAINER.parent / config["context"]).resolve()
    assert context == _ROOT, f"devcontainer.json builds from {context}"


def test_the_dockerfile_base_image_is_pinned_by_digest() -> None:
    """The file's own header says the digest, not the tag, is what resolves."""
    froms = re.findall(r"^FROM\s+(\S+)", _read(_DOCKERFILE), flags=re.MULTILINE)
    assert len(froms) == 1, f"Dockerfile has {len(froms)} FROM lines"
    assert re.search(r"@sha256:[0-9a-f]{64}$", froms[0]), (
        f"FROM {froms[0]} is not digest-pinned; what a devcontainer runs is "
        "then whatever was last pushed to a namespace this fork does not own"
    )


def test_the_dockerfile_reresolve_recipe_names_the_image_it_pins() -> None:
    """The comment is a second copy of the image reference, kept by hand."""
    text = _read(_DOCKERFILE)
    pinned = re.search(r"^FROM\s+(\S+?)@sha256:", text, flags=re.MULTILINE)
    assert pinned, "no digest-pinned FROM to compare the recipe against"
    recipe = re.search(r"imagetools inspect (\S+)", text)
    assert recipe, "the re-resolve recipe is gone; the digest is now unexplained"
    assert recipe.group(1) == pinned.group(1), (
        f"the recipe re-resolves {recipe.group(1)} but FROM pins "
        f"{pinned.group(1)}; following the comment updates a different image"
    )

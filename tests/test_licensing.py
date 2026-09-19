"""The licence files and the attribution rules that depend on them.

`LICENSE` and `LICENSE.MIT` were the only two non-test tracked files whose
contents no test opened. Their *existence* was pinned, but only incidentally:
`tests/test_file_conventions.py` reads every tracked file as bytes, so deleting
one errors at collection. Nothing read what either file said.

That left the repository's dual-licence story told entirely in prose, joined to
nothing:

- `README.md` §License - the fork is GPL-3.0, it incorporates `iprak/sensi`
  code under MIT, and "its licence text and copyright notice are preserved in
  LICENSE.MIT as the MIT licence requires".
- The `License: GPL-3.0` badge at the top of `README.md`, which links to
  `LICENSE`.
- `AGENTS.md` - "`manifest.json` `documentation`, `issue_tracker` and
  `codeowners` point at *this* repository, not upstream [...] Credit for the
  original work belongs in `README.md` and `LICENSE.MIT`, which is where it
  is". Of those three manifest fields only `codeowners` was joined to anything
  (`tests/test_codeowners.py` checks it against `CODEOWNERS`, and says nothing
  about upstream).

Every one of those claims could be falsified without a test noticing. Six
mutations to the committed tree, one per run, all survived the whole suite:
pointing `documentation` and `issue_tracker` back at upstream; renaming the MIT
copyright holder; deleting the MIT permission-notice clause the README says is
being satisfied; downgrading `LICENSE` to "Version 2, June 1991"; and rewriting
the badge to claim the project is MIT.

The two with teeth beyond tidiness: Home Assistant sends a user to
`issue_tracker` when a custom integration raises, so an upstream URL there
files this fork's bugs on a maintainer who did not publish this code; and the
MIT notice plus the copyright line are the terms under which this fork is
allowed to redistribute upstream's code at all.

This module is the join. Its conventions, carried from
`tests/test_prompt_procedures.py` and `tests/test_slash_commands.py`:

- Nothing about either repository is hard-coded. This repository's slug is read
  from the workflow badges at the top of `README.md` (which must agree with
  each other and name committed workflows); upstream's is read from the
  sentence in `README.md` that defines the fork; the MIT copyright line is read
  out of `LICENSE.MIT`. A test that hard-coded either name would pass after the
  rename it is supposed to catch.
- Every scan asserts how much it found before asserting anything about it, so a
  reader that silently returned nothing cannot turn an assertion green.

What is deliberately NOT asserted: that `LICENSE` is byte-identical to the FSF
original (no network, and a fetched copy would be checking GitHub rather than
this tree), that the MIT copyright *year* is any particular year, or that
upstream is credited in any file beyond the two `AGENTS.md` names.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

import pytest

_ROOT = Path(__file__).resolve().parent.parent

_LICENSE = _ROOT / "LICENSE"
_LICENSE_MIT = _ROOT / "LICENSE.MIT"
_README = _ROOT / "README.md"
_AGENTS = _ROOT / "AGENTS.md"
_MANIFEST = _ROOT / "custom_components" / "sensi" / "manifest.json"
_WORKFLOWS = _ROOT / ".github" / "workflows"

# The three `manifest.json` fields AGENTS.md rules on. Listed here so the rule
# is checked field by field; the list itself is asserted against the AGENTS.md
# sentence below, in both directions, so it cannot drift away from the rule it
# claims to enforce.
_FORK_POINTER_FIELDS = ("codeowners", "documentation", "issue_tracker")

# GPL-3.0 runs sections 0 through 17. Anything else is a different licence or a
# truncated copy of this one.
_GPL_SECTION_NUMBERS = tuple(range(18))

# The structural headings of the FSF text, in the order they appear.
_GPL_HEADINGS = (
    "GNU GENERAL PUBLIC LICENSE",
    "Preamble",
    "TERMS AND CONDITIONS",
    "END OF TERMS AND CONDITIONS",
    "How to Apply These Terms to Your New Programs",
)

# The MIT licence is three operative sentences. Each is a separate obligation,
# so each gets its own row rather than being folded into one "looks like MIT"
# check.
_MIT_CLAUSES = {
    "grant": (
        "Permission is hereby granted, free of charge, to any person obtaining "
        "a copy of this software and associated documentation files (the "
        '"Software"), to deal in the Software without restriction'
    ),
    "notice": (
        "The above copyright notice and this permission notice shall be "
        "included in all copies or substantial portions of the Software."
    ),
    "disclaimer": (
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, '
        "EXPRESS OR IMPLIED"
    ),
}

_COPYRIGHT_LINE = re.compile(r"^Copyright \(c\) (?P<year>\d{4}) (?P<holder>.+)$", re.M)

_GPL_VERSION_LINE = re.compile(r"^ *Version (?P<major>\d+), \d{1,2} \w+ \d{4}$", re.M)

_GPL_SECTION = re.compile(r"^ {2}(\d{1,2})\. \S", re.M)

# `[![label](shield url)](link target)` - the badge form used at the top of
# README.md.
_BADGE = re.compile(
    r"\[!\[(?P<alt>[^\]]*)\]\((?P<shield>[^)\s]+)\)\]\((?P<href>[^)\s]+)\)"
)

# A workflow status badge names the repository that runs it and the workflow
# file it reports on.
_WORKFLOW_BADGE = re.compile(
    r"https://github\.com/(?P<slug>[\w.-]+/[\w.-]+)"
    r"/actions/workflows/(?P<workflow>[\w.-]+\.ya?ml)/badge\.svg"
)

# shields.io renders `badge/<label>-<message>-<color>`; the label is what a
# reader sees before the dash.
_SHIELD_PATH = re.compile(r"https://img\.shields\.io/badge/(?P<slug>[^?]+)")

# The sentence that defines the fork, in README.md and in AGENTS.md. The
# README form carries the slug twice - once as link text, once inside the URL -
# and the backreference makes the two agree.
_README_FORK_OF = re.compile(
    r"fork of \[`(?P<slug>[\w.-]+/[\w.-]+)`\]\(https://github\.com/(?P=slug)\)"
)
_AGENTS_FORK_OF = re.compile(r"is a fork of `(?P<slug>[\w.-]+/[\w.-]+)`")

# "Credit for the original work belongs in `README.md` and `LICENSE.MIT`".
_CREDIT_SENTENCE = re.compile(
    r"Credit for the original work belongs in (?P<files>.+?), which is where it is"
)

_PATH_IN_BACKTICKS = re.compile(r"`([\w./-]+)`")

# The clause that enumerates the manifest fields the fork-pointer rule governs,
# and the sentence that closes it. Read the enumeration from the clause, not
# from the paragraph: the sentence after it explains what `issue_tracker` does,
# so a paragraph-wide search finds the word whether or not the rule still
# governs the field.
_FORK_POINTER_CLAUSE = re.compile(
    r"`manifest\.json` (?P<fields>[^.]+?) point at \*this\* repository, not upstream"
)
_FORK_POINTER_COUNT = re.compile(
    r"Do not point any of the (?P<count>\w+) back at upstream"
)

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def _read(path: Path) -> str:
    """Return the text of a committed file."""
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """Return a file's text with every run of whitespace collapsed to a space.

    The prose here is hard-wrapped, so a sentence this module quotes is split
    across lines at whatever column the wrap fell on. Searching the raw text
    for it would make every assertion sensitive to re-wrapping rather than to
    the claim.
    """
    return " ".join(_read(path).split())


@functools.cache
def _tracked_files() -> frozenset[str]:
    """Return every path git tracks, read from the index rather than a walk."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return frozenset(part for part in out.stdout.split("\0") if part)


def _shield_fields(slug: str) -> tuple[str, str]:
    """Return the label and message of a `badge/<label>-<message>-<color>` path.

    shields.io escapes a literal dash inside a field by doubling it, so the
    path cannot simply be split on `-`. Park the doubled ones out of the way
    first, or `GPL--3.0` reads as two fields and the version disappears.
    """
    parts = [
        part.replace("\0", "-") for part in unquote(slug).replace("--", "\0").split("-")
    ]
    assert len(parts) == 3, f"badge path is not `<label>-<message>-<color>`: {slug}"
    return parts[0], parts[1]


def _section(text: str, heading: str) -> str:
    """Return the body of a `## <heading>` section of a markdown document."""
    marker = f"\n## {heading}\n"
    start = text.find(marker)
    assert start != -1, f"README.md has no `## {heading}` section"
    body_at = start + len(marker)
    end = text.find("\n## ", body_at)
    return text[body_at:] if end == -1 else text[body_at:end]


@functools.cache
def _manifest() -> dict:
    """Return the integration manifest Home Assistant reads."""
    return json.loads(_read(_MANIFEST))


@functools.cache
def _this_repository() -> str:
    """Return this repository's `owner/name`, read from the README's badges.

    The workflow status badges each embed the slug of the repository whose runs
    they report. Taking the slug from them rather than hard-coding it means a
    rename has one place to go wrong, and the workflow half of each badge URL
    is checked against the committed workflow set so the source cannot decay
    into an arbitrary string.
    """
    badges = _WORKFLOW_BADGE.findall(_read(_README))
    assert len(badges) >= 4, f"README.md carries {len(badges)} workflow badges"

    slugs = {slug for slug, _ in badges}
    assert len(slugs) == 1, (
        f"README.md badges name several repositories: {sorted(slugs)}"
    )

    for _, workflow in badges:
        assert (_WORKFLOWS / workflow).is_file(), (
            f"README.md badges a workflow this repository does not ship: {workflow}"
        )
    return next(iter(slugs))


@functools.cache
def _upstream_repository() -> str:
    """Return the upstream `owner/name` this fork descends from."""
    match = _README_FORK_OF.search(_flat(_README))
    assert match, "README.md no longer states which repository this is a fork of"
    return match["slug"]


@functools.cache
def _mit_copyright() -> re.Match[str]:
    """Return the single copyright line `LICENSE.MIT` carries."""
    matches = _COPYRIGHT_LINE.findall(_read(_LICENSE_MIT))
    assert len(matches) == 1, f"LICENSE.MIT carries {len(matches)} copyright lines"
    match = _COPYRIGHT_LINE.search(_read(_LICENSE_MIT))
    assert match
    return match


def test_license_is_the_gpl_3_text() -> None:
    """`LICENSE` is GPL version 3, which is the licence everything else names."""
    text = _read(_LICENSE)

    assert text.lstrip().startswith("GNU GENERAL PUBLIC LICENSE"), (
        "LICENSE does not open with the GNU General Public License title"
    )

    version = _GPL_VERSION_LINE.search(text)
    assert version, "LICENSE carries no `Version <n>, <date>` line"
    assert version["major"] == "3", (
        f"LICENSE is GPL version {version['major']}, not version 3 - "
        "README.md's badge and its License section both say GPL-3.0"
    )
    assert "Version 3, 29 June 2007" in text, (
        "LICENSE is not the 29 June 2007 revision of GPL-3.0"
    )


def test_license_carries_every_gpl_section_in_order() -> None:
    """A truncated or substituted licence fails here, not silently.

    Phrase spotting is not enough: most of the GPL's sentences also appear in
    GPL-2.0, and a copy cut off halfway still contains the opening ones. The
    numbered sections are the structure, so assert the whole run of them.
    """
    numbers = tuple(int(number) for number in _GPL_SECTION.findall(_read(_LICENSE)))

    assert numbers == _GPL_SECTION_NUMBERS, (
        f"LICENSE's numbered sections are {numbers}, not 0 through 17"
    )


def test_license_carries_the_structural_headings_in_order() -> None:
    """The preamble, the terms, and the how-to-apply appendix are all present."""
    text = _read(_LICENSE)
    positions = []

    for heading in _GPL_HEADINGS:
        found = text.find(heading)
        assert found != -1, f"LICENSE is missing the `{heading}` heading"
        positions.append(found)

    assert positions == sorted(positions), (
        f"LICENSE's headings are out of order: {_GPL_HEADINGS}"
    )


def test_license_mit_is_the_mit_text() -> None:
    """`LICENSE.MIT` is the MIT licence, not a summary or a link to one."""
    text = _flat(_LICENSE_MIT)

    assert _read(_LICENSE_MIT).lstrip().startswith("MIT License"), (
        "LICENSE.MIT does not open with the MIT License title"
    )
    for name, clause in _MIT_CLAUSES.items():
        assert clause in text, f"LICENSE.MIT is missing the MIT {name} clause"


def test_license_mit_carries_the_permission_notice_clause() -> None:
    """The clause `README.md` claims this file exists to satisfy.

    README.md §License says upstream's "licence text and copyright notice are
    preserved in LICENSE.MIT as the MIT licence requires". The requirement is
    this one sentence. Dropping it while the README keeps claiming compliance
    is the failure this test exists for, so it is asserted on its own rather
    than as one row of the clause table above.
    """
    assert _MIT_CLAUSES["notice"] in _flat(_LICENSE_MIT), (
        "LICENSE.MIT no longer carries the MIT permission notice, which is the "
        "condition under which this fork may redistribute upstream's code"
    )


def test_license_mit_names_one_copyright_holder() -> None:
    """`LICENSE.MIT` carries exactly one `Copyright (c) <year> <holder>` line."""
    match = _mit_copyright()

    assert match["holder"].strip(), "LICENSE.MIT's copyright line names no holder"
    assert 2000 <= int(match["year"]) <= 2100, (
        f"LICENSE.MIT's copyright year is implausible: {match['year']}"
    )


def test_readme_repeats_the_mit_copyright_line_verbatim() -> None:
    """The README's credit and `LICENSE.MIT`'s notice are one string.

    They are two hand copies of the same attribution, and the whole dual-licence
    arrangement rests on it. Asserting the README carries `LICENSE.MIT`'s line
    verbatim - rather than each against a literal spelled here - means renaming
    the holder in either file fails.
    """
    licence_line = _mit_copyright().group(0)
    section = _section(_read(_README), "License")

    assert licence_line in " ".join(section.split()), (
        f"README.md's License section does not carry LICENSE.MIT's "
        f"copyright line: {licence_line!r}"
    )


def test_readme_names_no_second_copyright_holder() -> None:
    """The README's License section attributes the MIT code to one party.

    The reverse direction of the test above: every copyright line the section
    carries must be the one `LICENSE.MIT` carries, so a second, divergent
    attribution cannot be added beside it.
    """
    section = " ".join(_section(_read(_README), "License").split())
    lines = _COPYRIGHT_LINE.findall(section) or re.findall(
        r"Copyright \(c\) \d{4} [^,.]+", section
    )

    assert lines, "README.md's License section carries no copyright line at all"
    for line in lines:
        assert line in _mit_copyright().group(0), (
            f"README.md's License section attributes MIT code to {line!r}, "
            f"which LICENSE.MIT does not"
        )


def test_the_license_badge_names_the_licence_the_repository_ships() -> None:
    """The `License:` badge, its message and its link target all agree.

    A badge is the first licence statement a reader sees and the one least
    likely to be revisited. Its message is checked against the version
    `LICENSE` actually carries, and its link against the file that carries it.
    """
    badges = [
        badge
        for badge in _BADGE.finditer(_read(_README))
        if badge["alt"].startswith("License")
    ]
    assert len(badges) == 1, f"README.md carries {len(badges)} licence badges"
    badge = badges[0]

    version = _GPL_VERSION_LINE.search(_read(_LICENSE))
    assert version
    expected = f"GPL-{version['major']}.0"

    shield = _SHIELD_PATH.search(badge["shield"])
    assert shield, f"the licence badge is not a shields.io badge: {badge['shield']}"
    label, message = _shield_fields(shield["slug"])

    assert label.lower() == "license", (
        f"the licence badge's label is {label!r}, not a licence label"
    )
    assert message == expected, (
        f"the licence badge advertises {message!r} while LICENSE is {expected!r}"
    )
    assert badge["alt"] == f"License: {expected}", (
        f"the licence badge's alt text is {badge['alt']!r}, not 'License: {expected}'"
    )
    assert badge["href"] == _LICENSE.name, (
        f"the licence badge links to {badge['href']!r}, not to {_LICENSE.name}"
    )


def test_the_project_licence_link_points_at_the_gpl_text() -> None:
    """README §License links the fork's own licence to `LICENSE`.

    `LICENSE.MIT` covers the incorporated upstream code, not this distribution.
    Presenting it as the project licence would misstate the terms the combined
    work ships under.
    """
    section = " ".join(_section(_read(_README), "License").split())

    match = re.search(
        r"This fork is licensed under the \[[^\]]+\]\(([^)\s]+)\)", section
    )
    assert match, "README.md §License no longer states what the fork is licensed under"
    assert match[1] == _LICENSE.name, (
        f"README.md §License points the fork's licence at {match[1]!r}, "
        f"not {_LICENSE.name}"
    )
    assert f"[{_LICENSE_MIT.name}]({_LICENSE_MIT.name})" in section, (
        f"README.md §License no longer links to {_LICENSE_MIT.name}"
    )


def test_the_combined_work_is_stated_to_ship_under_the_project_licence() -> None:
    """README §License says which licence the distributed whole is under."""
    version = _GPL_VERSION_LINE.search(_read(_LICENSE))
    assert version
    section = " ".join(_section(_read(_README), "License").split())

    assert re.search(
        rf"combined work[^.]*is distributed under GPL-{version['major']}\.0", section
    ), (
        "README.md §License no longer states that the combined work is "
        f"distributed under GPL-{version['major']}.0"
    )


@pytest.mark.parametrize("field", ["documentation", "issue_tracker"])
def test_manifest_url_points_at_this_repository(field: str) -> None:
    """Home Assistant sends users wherever these two fields point.

    AGENTS.md: "Home Assistant sends users to `issue_tracker` when a custom
    integration raises, so pointing it upstream files this fork's bugs on a
    maintainer who did not publish this code."
    """
    value = _manifest()[field]
    prefix = f"https://github.com/{_this_repository()}"

    assert value == prefix or value.startswith(f"{prefix}/"), (
        f"manifest.json `{field}` is {value!r}, which is not {_this_repository()}"
    )


def test_no_manifest_fork_pointer_names_upstream() -> None:
    """None of the three fields AGENTS.md rules on points back at upstream.

    Checked against upstream's slug and its owner separately: a URL under a
    differently-named upstream repository, and a codeowner who is upstream's
    maintainer, are both the failure the rule is about.
    """
    upstream = _upstream_repository()
    owner = upstream.split("/")[0]
    manifest = _manifest()

    for field in _FORK_POINTER_FIELDS:
        value = manifest[field]
        rendered = " ".join(value) if isinstance(value, list) else value
        assert upstream not in rendered, (
            f"manifest.json `{field}` points at upstream {upstream}: {value!r}"
        )
        assert f"github.com/{owner}/" not in rendered, (
            f"manifest.json `{field}` points into upstream's account: {value!r}"
        )
        assert f"@{owner}" not in rendered, (
            f"manifest.json `{field}` names upstream's maintainer: {value!r}"
        )


def test_the_fork_pointer_rule_governs_fields_that_exist() -> None:
    """AGENTS.md's rule and `_FORK_POINTER_FIELDS` name the same three fields.

    Both directions, and read from the enumerating clause itself rather than
    from the paragraph around it: the next sentence explains what `issue_tracker`
    is for, so a paragraph-wide search still finds the word after the field has
    been dropped from the list of fields the rule governs.

    A field renamed or dropped from the manifest turns the rule into a guard
    over an absent key, and a field added to the rule without a row here would
    go unchecked.
    """
    rule = _flat(_AGENTS)
    clause = _FORK_POINTER_CLAUSE.search(rule)
    assert clause, "AGENTS.md no longer carries the fork-pointer rule"

    named = set(_PATH_IN_BACKTICKS.findall(clause["fields"]))
    assert named == set(_FORK_POINTER_FIELDS), (
        f"AGENTS.md's fork-pointer rule governs {sorted(named)}, while this "
        f"module checks {sorted(_FORK_POINTER_FIELDS)}"
    )

    for field in _FORK_POINTER_FIELDS:
        assert field in _manifest(), (
            f"AGENTS.md rules on manifest.json `{field}`, which the manifest "
            "no longer has"
        )

    closing = _FORK_POINTER_COUNT.search(rule)
    assert closing, "AGENTS.md no longer closes the rule with 'do not point ... back'"
    assert _NUMBER_WORDS[closing["count"]] == len(named), (
        f"AGENTS.md's rule enumerates {len(named)} fields and then calls them "
        f"'the {closing['count']}'"
    )


def test_every_prose_site_names_the_same_upstream_repository() -> None:
    """`README.md` and `AGENTS.md` cannot drift into naming different upstreams.

    Three independent statements of the same fact: the note at the top of the
    README, the README's License section, and AGENTS.md's fork-pointer rule.
    """
    upstream = _upstream_repository()

    agents = _AGENTS_FORK_OF.search(_flat(_AGENTS))
    assert agents, "AGENTS.md no longer states which repository this is a fork of"
    assert agents["slug"] == upstream, (
        f"AGENTS.md calls this a fork of {agents['slug']}, README.md of {upstream}"
    )

    section = " ".join(_section(_read(_README), "License").split())
    assert f"[`{upstream}`](https://github.com/{upstream})" in section, (
        f"README.md §License does not attribute the MIT code to {upstream}"
    )

    thanks = " ".join(_section(_read(_README), "Thanks").split())
    assert f"https://github.com/{upstream}" in thanks, (
        f"README.md §Thanks does not credit {upstream}"
    )


def test_credit_lives_in_exactly_the_files_agents_md_names() -> None:
    """AGENTS.md: credit "belongs in `README.md` and `LICENSE.MIT`, which is where it is".

    Both directions. Every file the sentence names must carry the copyright
    holder `LICENSE.MIT` declares, and no other tracked file outside `tests/`
    may be the one carrying it - so dropping the credit from either named file,
    or moving it somewhere the sentence does not point, fails here.
    """
    sentence = _CREDIT_SENTENCE.search(_flat(_AGENTS))
    assert sentence, "AGENTS.md no longer says where credit for upstream's work lives"

    named = set(_PATH_IN_BACKTICKS.findall(sentence["files"]))
    assert named, "AGENTS.md's credit sentence names no files"

    holder = _mit_copyright()["holder"].strip()
    carrying = {
        path
        for path in _tracked_files()
        if not path.startswith("tests/")
        and holder in (_ROOT / path).read_text(encoding="utf-8", errors="ignore")
    }

    assert named <= carrying, (
        f"AGENTS.md says credit lives in {sorted(named - carrying)}, "
        f"which do not name {holder!r}"
    )
    assert carrying == named, (
        f"upstream's credit also lives in {sorted(carrying - named)}, which "
        "AGENTS.md's sentence does not name"
    )

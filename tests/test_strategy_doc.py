"""The strategy page's ACMM accounting, joined to `.acmm.yml` and the tree.

`docs/strategy.md` says the ACMM evaluation opened eight L6 issues, #418 to
#425, and sorts every one of them into an outcome: documented, waived to Hive,
waived to an existing file, or not planned. Each outcome is a claim about this
repository that a file can confirm or contradict:

- **Documented** means the page the criterion looks for is here, at one of the
  paths the criterion accepts. Renaming `docs/ai-ops-runbook.md` and fixing
  every link to it would keep the link checks green and silently turn the
  criterion back off.
- **Waived** means `.acmm.yml` holds a waiver for that criterion, satisfied by
  Hive or by the file the page names.
- **Not planned** means none of the criterion's files exists and nothing
  waives it.

Until this module existed, only `tests/test_instruction_docs.py` read the page,
to resolve its links and backticked paths, so a waiver could be added to or
dropped from `.acmm.yml`, or an issue moved between outcomes, with the page
left describing the old arrangement.

The criterion IDs and accepted paths below are copied from the body of each
issue, which the Hive ACMM evaluation opened on 2026-10-03. They are what the
evaluation checks, so they are pinned here rather than read from the page.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "strategy.md"
_POLICY = _ROOT / ".acmm.yml"
_SECURITY = _ROOT / "docs" / "SECURITY-AI.md"

# issue -> (criterion ID, paths the criterion accepts). A trailing `/` is a
# directory. Copied from each issue's "Criterion ID" and "What's needed".
_ISSUES: dict[int, tuple[str, tuple[str, ...]]] = {
    418: (
        "acmm:auto-issue-gen",
        (
            ".github/workflows/auto-issues.yml",
            ".github/workflows/auto-issue.yml",
            ".github/workflows/issue-gen.yml",
            ".github/workflows/auto-generate-issues.yml",
            "scripts/generate-issues.mjs",
        ),
    ),
    419: (
        "acmm:multi-agent-orchestration",
        (
            ".github/workflows/dispatcher.yml",
            ".github/workflows/orchestrate.yml",
            "scripts/orchestrate.mjs",
            "docs/multi-agent.md",
            ".claude/dispatcher/",
            "orchestrator/",
        ),
    ),
    420: (
        "acmm:strategic-dashboard",
        (
            "web/src/components/acmm/",
            "docs/strategy.md",
            ".github/workflows/strategy-report.yml",
            "docs/autonomous-work-log.md",
        ),
    ),
    421: (
        "acmm:merge-queue",
        (
            ".github/workflows/merge-queue.yml",
            ".github/merge-queue.yml",
            ".prow.yaml",
            "tide.yaml",
        ),
    ),
    422: (
        "acmm:risk-assessment-config",
        ("risk-config.json", ".claude/risk-config.json", ".github/risk-assessment.yml"),
    ),
    423: (
        "acmm:observability-runbook",
        ("docs/ai-ops-runbook.md", "docs/runbook/", "RUNBOOK.md"),
    ),
    424: (
        "aef:task-traceability",
        (".agent/tasks/", "docs/agent-tasks/", ".github/agent-log/", "agent-tasks.md"),
    ),
    425: (
        "aef:audit-trail",
        (
            ".github/workflows/ai-audit.yml",
            ".github/workflows/agent-audit.yml",
            "scripts/ai-audit-report.mjs",
        ),
    ),
}

_DOCUMENTED = "Documented"
_WAIVED_TO_HIVE = "Waived to Hive"
_WAIVED_TO_FILE = "Waived to an existing file"
_NOT_PLANNED = "Not planned"
_OUTCOMES = (_DOCUMENTED, _WAIVED_TO_HIVE, _WAIVED_TO_FILE, _NOT_PLANNED)

_NUMBER_WORDS = {
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _accounting() -> str:
    """Return the paragraph and bullets that sort the L6 issues into outcomes."""
    text = _DOC.read_text(encoding="utf-8")
    start = text.index("The ACMM evaluation opened")
    end = text.index("A waiver cannot advance", start)
    return text[start:end]


def _outcomes() -> dict[str, str]:
    """Return each outcome bullet's text, keyed by its bold label without a colon."""
    bullets = re.split(r"^- ", _accounting(), flags=re.MULTILINE)[1:]
    outcomes: dict[str, str] = {}
    for bullet in bullets:
        label = re.match(r"\*\*(.+?)\*\*", bullet)
        assert label, f"an outcome bullet has no bold label: {bullet[:60]!r}"
        name = label.group(1).rstrip(":")
        assert name not in outcomes, f"outcome {name!r} is listed twice"
        outcomes[name] = _collapse(bullet[label.end() :])
    return outcomes


def _issues_in(bullet: str) -> list[int]:
    return [int(number) for number in re.findall(r"#(\d+)", bullet)]


def _issues_with(outcome: str) -> list[int]:
    return _issues_in(_outcomes()[outcome])


def _waivers() -> dict[str, dict]:
    document = yaml.safe_load(_POLICY.read_text(encoding="utf-8"))
    return {waiver["id"]: waiver for waiver in document["waivers"]}


def _present(path: str) -> bool:
    target = _ROOT / path.rstrip("/")
    return target.is_dir() if path.endswith("/") else target.is_file()


def test_the_page_sorts_issues_into_the_four_outcomes() -> None:
    """Every bullet is one of the four outcomes, and all four are present.

    Guards the parser as much as the page: if the bullets were not found,
    every set comparison below would compare empty sets and pass.
    """
    assert tuple(_outcomes()) == _OUTCOMES
    assert all(_issues_with(outcome) for outcome in _OUTCOMES)


def test_the_count_and_range_name_the_issues_the_bullets_sort() -> None:
    """The page's "eight L6 issues (#418 to #425)" is the set the bullets sort."""
    opening = _collapse(_accounting())
    claim = re.search(r"opened (\w+) L6 issues \(#(\d+) to #(\d+)\)", opening)
    assert claim, "the page no longer states how many L6 issues were opened"
    count, first, last = claim.groups()

    sorted_issues = [n for outcome in _OUTCOMES for n in _issues_with(outcome)]
    assert len(sorted_issues) == len(set(sorted_issues)), (
        f"an issue is sorted into two outcomes: {sorted(sorted_issues)}"
    )
    assert set(sorted_issues) == set(range(int(first), int(last) + 1))
    assert _NUMBER_WORDS[count] == len(sorted_issues)


def test_every_sorted_issue_is_one_the_evaluation_opened() -> None:
    """The page sorts the issues pinned above, no more and no fewer."""
    sorted_issues = {n for outcome in _OUTCOMES for n in _issues_with(outcome)}
    assert sorted_issues == set(_ISSUES)


@pytest.mark.parametrize("issue", [418, 419, 420, 421, 422, 423, 424, 425])
def test_each_issue_has_the_outcome_the_tree_and_policy_show(issue: int) -> None:
    """The outcome the page gives an issue is the one the repository shows.

    Documented: a file the criterion accepts is here, and nothing waives it.
    Waived to Hive: a waiver satisfied by `hive`, and no accepted file.
    Waived to an existing file: a waiver satisfied by the path the bullet
    names, that path is here, and no accepted file is (a copy would drift).
    Not planned: no accepted file, and no waiver.
    """
    criterion, accepted = _ISSUES[issue]
    outcome = next(o for o in _OUTCOMES if issue in _issues_with(o))
    present = [path for path in accepted if _present(path)]
    waiver = _waivers().get(criterion)

    if outcome == _DOCUMENTED:
        assert present, f"#{issue} is 'documented' but none of {accepted} exists"
        assert waiver is None, (
            f"#{issue} is 'documented' yet .acmm.yml waives {criterion}"
        )
        return

    assert not present, f"#{issue} is '{outcome}' but {present} exists"
    if outcome == _NOT_PLANNED:
        assert waiver is None, (
            f"#{issue} is 'not planned' yet .acmm.yml waives {criterion}"
        )
        return

    assert waiver is not None, (
        f"#{issue} is '{outcome}' but .acmm.yml has no {criterion}"
    )
    if outcome == _WAIVED_TO_HIVE:
        assert waiver["satisfied_by"] == "hive"
    else:
        named = re.findall(r"`([^`]+)`", _outcomes()[_WAIVED_TO_FILE])
        stand_in = [path for path in named if path != ".acmm.yml"]
        assert stand_in == [waiver["satisfied_by"]], (
            f"the page names {stand_in}, .acmm.yml says {waiver['satisfied_by']!r}"
        )
        assert _present(waiver["satisfied_by"])


def test_every_l6_waiver_in_the_policy_is_one_the_page_reports() -> None:
    """A waiver added to `.acmm.yml` for one of these criteria needs a bullet.

    The reverse of the per-issue check: it fails when the policy waives a
    criterion that the page still calls documented or not planned.
    """
    waived_here = {
        _ISSUES[n][0]
        for outcome in (_WAIVED_TO_HIVE, _WAIVED_TO_FILE)
        for n in _issues_with(outcome)
    }
    l6_criteria = {criterion for criterion, _ in _ISSUES.values()}
    assert set(_waivers()) & l6_criteria == waived_here


def test_the_rule_the_page_cites_is_in_the_security_policy() -> None:
    """The page says the policy states no agent merges its own work; it does."""
    assert "states that no agent merges its own work" in _collapse(
        _DOC.read_text(encoding="utf-8")
    )
    assert "No agent merges its own work." in _collapse(
        _SECURITY.read_text(encoding="utf-8")
    )

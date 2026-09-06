"""Tests for scripts/auto_qa_tuner.py.

Not part of the component, so not measured by the coverage gate - but this
script is what decides whether the gate itself should move, and it runs on
every nightly. A tuner that proposes a raise the suite has not earned puts a
threshold in front of changes that cannot pass it; one that never proposes at
all quietly stops being a ratchet.

Each of the five outcomes `evaluate()` can reach gets a test, including the
boundary where `measured` is exactly `headroom` above the gate and the
proposal is suppressed by the second guard rather than the first.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "auto_qa_tuner.py"
_spec = importlib.util.spec_from_file_location("auto_qa_tuner", _SCRIPT)
auto_qa_tuner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auto_qa_tuner)


# The shape of .github/auto-qa-tuning.json's line_coverage gate, with the
# values it actually ships. Tests that care about a specific number override
# it rather than restating the whole structure.
def gate(current=93, ceiling=97, headroom=5, step=1, min_observations=5):
    """Return a gate definition in the policy file's shape."""
    return {
        "source": "coverage.xml",
        "enforced_in": ".github/workflows/coverage-gate.yml",
        "variable": "MIN_COVERAGE",
        "current": current,
        "floor": 85,
        "ceiling": ceiling,
        "policy": {
            "direction": "up_only",
            "headroom": headroom,
            "step": step,
            "min_observations": min_observations,
        },
    }


def write_coverage(path: Path, line_rate: str) -> Path:
    """Write a minimal Cobertura report carrying the given line-rate."""
    path.write_text(
        f'<?xml version="1.0" ?>\n<coverage line-rate="{line_rate}"></coverage>\n',
        encoding="utf-8",
    )
    return path


def write_policy(tmp_path: Path, gate_def: dict) -> Path:
    """Write a policy file containing the given line_coverage gate."""
    policy = tmp_path / "auto-qa-tuning.json"
    policy.write_text(
        json.dumps({"version": 1, "gates": {"line_coverage": gate_def}}),
        encoding="utf-8",
    )
    return policy


def test_measured_coverage_reads_the_line_rate_as_a_percentage(tmp_path):
    """Cobertura stores a rate; the rest of the script talks in percent."""
    report = write_coverage(tmp_path / "coverage.xml", "0.9912")

    assert auto_qa_tuner.measured_coverage(report) == pytest.approx(99.12)


def test_measured_coverage_defaults_to_zero_without_a_line_rate(tmp_path):
    """A report with no line-rate reads as 0%, which lands below any gate."""
    (tmp_path / "coverage.xml").write_text("<coverage></coverage>", encoding="utf-8")

    assert auto_qa_tuner.measured_coverage(tmp_path / "coverage.xml") == 0.0


def test_measured_coverage_refuses_a_missing_report(tmp_path, capsys):
    """No report means the tuner has nothing to say, not a 0% verdict."""
    with pytest.raises(SystemExit) as excinfo:
        auto_qa_tuner.measured_coverage(tmp_path / "absent.xml")

    assert excinfo.value.code == 1
    assert "run pytest with --cov-report=xml first" in capsys.readouterr().err


def test_measured_coverage_refuses_a_malformed_report(tmp_path, capsys):
    """A truncated upload must fail loudly rather than parse as nothing."""
    broken = tmp_path / "coverage.xml"
    broken.write_text('<coverage line-rate="0.99"', encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        auto_qa_tuner.measured_coverage(broken)

    assert excinfo.value.code == 1
    assert "is not valid XML" in capsys.readouterr().err


def test_measured_below_the_gate_proposes_nothing():
    """The gate has already failed the run; tuning it is not the answer."""
    result = auto_qa_tuner.evaluate(88.0, gate(current=93))

    assert result["change"] is False
    assert result["proposed"] == 93
    assert "below the 93% gate" in result["reason"]


def test_a_gate_at_the_ceiling_stays_there():
    """The ceiling stops the ratchet before the untestable last few percent."""
    result = auto_qa_tuner.evaluate(100.0, gate(current=97, ceiling=97))

    assert result["change"] is False
    assert result["proposed"] == 97
    assert "97% ceiling" in result["reason"]


def test_too_little_headroom_proposes_nothing():
    """Four points above a five-point headroom requirement is not enough."""
    result = auto_qa_tuner.evaluate(97.0, gate(current=93, headroom=5))

    assert result["change"] is False
    assert result["proposed"] == 93
    assert "4.00 points" in result["reason"]
    assert "5 points of headroom are" in result["reason"]


def test_exactly_the_headroom_is_still_not_enough_to_raise():
    """98 == 93 + 5 clears the first guard, then loses to the second.

    `min(current + step, ceiling, int(measured - headroom))` is `int(98 - 5)`,
    i.e. 93, so the raise would land back on the current threshold. Without the
    `proposed <= current` guard this case would report a "change" that changes
    nothing.
    """
    result = auto_qa_tuner.evaluate(98.0, gate(current=93, headroom=5))

    assert result["change"] is False
    assert result["proposed"] == 93
    assert "less than 5 points of headroom" in result["reason"]


def test_sustained_headroom_proposes_a_single_step():
    """A raise moves by `step`, not to the measurement."""
    result = auto_qa_tuner.evaluate(99.0, gate(current=93, headroom=5, step=1))

    assert result["change"] is True
    assert result["proposed"] == 94
    assert result["measured"] == 99.0
    assert "Raising to 94%" in result["reason"]
    # A single nightly is one observation, and the policy asks for five.
    assert "5 consecutive nightly runs" in result["reason"]


def test_a_proposal_never_exceeds_the_ceiling():
    """A large step must still stop at the ceiling.

    With the shipped five points of headroom the ceiling can never bind - 100%
    measured caps the proposal at 95 before the ceiling is consulted - so this
    takes the headroom out of the way to reach the clamp itself.
    """
    result = auto_qa_tuner.evaluate(
        100.0, gate(current=93, ceiling=97, headroom=2, step=10)
    )

    assert result["change"] is True
    assert result["proposed"] == 97


def test_a_proposal_never_eats_the_headroom():
    """With 95.4% measured, 94 is the highest gate that keeps 5 points free."""
    result = auto_qa_tuner.evaluate(
        99.4, gate(current=90, ceiling=97, headroom=5, step=10)
    )

    assert result["change"] is True
    assert result["proposed"] == 94


def test_measured_is_rounded_for_reporting_only():
    """The verdict is decided on the raw figure; only the report rounds."""
    result = auto_qa_tuner.evaluate(97.999, gate(current=93, headroom=5))

    # 4.999 points of headroom is short of the five required, so this holds at
    # the headroom guard - even though the reported figures, 98.0 and 93, look
    # like exactly five points apart.
    assert result["measured"] == 98.0
    assert result["change"] is False
    assert "5.00 points" in result["reason"]


def test_terminal_render_reports_hold():
    """The plain format leads with the verdict so a log scan finds it."""
    result = auto_qa_tuner.evaluate(94.0, gate(current=93))
    text = auto_qa_tuner.render(result, gate(current=93), markdown=False)

    assert text.startswith("[HOLD] coverage gate")
    assert "measured: 94.0%" in text
    assert "current:  93%" in text
    assert "proposed: 93%" in text


def test_terminal_render_reports_propose():
    """A proposal is still exit status 0, so the verdict has to be visible."""
    result = auto_qa_tuner.evaluate(99.0, gate(current=93))
    text = auto_qa_tuner.render(result, gate(current=93), markdown=False)

    assert text.startswith("[PROPOSE] coverage gate")
    assert "proposed: 94%" in text


def test_markdown_render_of_a_hold_omits_the_apply_instructions():
    """Nothing to apply, so no instructions for applying it."""
    result = auto_qa_tuner.evaluate(94.0, gate(current=93))
    text = auto_qa_tuner.render(result, gate(current=93), markdown=True)

    assert text.startswith("### Coverage gate: no change proposed")
    assert "To apply" not in text


def test_markdown_render_of_a_proposal_names_the_variable_and_the_file():
    """The summary has to say what to edit; the tuner will not edit it."""
    result = auto_qa_tuner.evaluate(99.0, gate(current=93))
    text = auto_qa_tuner.render(result, gate(current=93), markdown=True)

    assert text.startswith("### Coverage gate: propose raising to 94%")
    assert "`MIN_COVERAGE: 94`" in text
    assert "`.github/workflows/coverage-gate.yml`" in text
    assert "Nothing here does that for you." in text


def run_main(monkeypatch, tmp_path, argv, *, line_rate="0.99", gate_def=None):
    """Invoke main() with a policy and a coverage report on disk."""
    monkeypatch.setattr(
        auto_qa_tuner, "POLICY", write_policy(tmp_path, gate_def or gate())
    )
    report = write_coverage(tmp_path / "coverage.xml", line_rate)
    monkeypatch.setattr(
        "sys.argv", ["auto_qa_tuner.py", "--coverage", str(report), *argv]
    )
    return auto_qa_tuner.main()


def test_main_prints_the_terminal_report_by_default(monkeypatch, tmp_path, capsys):
    """No flags: the human-readable form, and exit 0."""
    rc = run_main(monkeypatch, tmp_path, [])

    assert rc == 0
    assert capsys.readouterr().out.startswith("[PROPOSE] coverage gate")


def test_main_emits_markdown_when_asked(monkeypatch, tmp_path, capsys):
    """This is the form nightly.yml appends to the job summary."""
    rc = run_main(monkeypatch, tmp_path, ["--markdown"])

    assert rc == 0
    assert capsys.readouterr().out.startswith("### Coverage gate: propose raising to")


def test_main_emits_json_when_asked(monkeypatch, tmp_path, capsys):
    """The JSON form is the machine-readable contract, so assert its keys."""
    rc = run_main(monkeypatch, tmp_path, ["--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "measured": 99.0,
        "current": 93,
        "proposed": 94,
        "change": True,
        "reason": payload["reason"],
    }


def test_main_json_takes_precedence_over_markdown(monkeypatch, tmp_path, capsys):
    """Both flags at once must still produce parseable output."""
    rc = run_main(monkeypatch, tmp_path, ["--json", "--markdown"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["change"] is True


def test_main_defaults_the_report_path_to_the_working_directory(
    monkeypatch, tmp_path, capsys
):
    """`python3 scripts/auto_qa_tuner.py` with no --coverage reads ./coverage.xml."""
    monkeypatch.setattr(auto_qa_tuner, "POLICY", write_policy(tmp_path, gate()))
    write_coverage(tmp_path / "coverage.xml", "0.99")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["auto_qa_tuner.py"])

    assert auto_qa_tuner.main() == 0
    assert "[PROPOSE]" in capsys.readouterr().out


def test_main_refuses_a_missing_policy(monkeypatch, tmp_path, capsys):
    """Without the policy there is no gate to reason about."""
    monkeypatch.setattr(auto_qa_tuner, "POLICY", tmp_path / "absent.json")
    monkeypatch.setattr("sys.argv", ["auto_qa_tuner.py"])

    with pytest.raises(SystemExit) as excinfo:
        auto_qa_tuner.main()

    assert excinfo.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_main_holds_when_the_measurement_is_below_the_gate(
    monkeypatch, tmp_path, capsys
):
    """The end-to-end path for the case the gate has already failed."""
    rc = run_main(monkeypatch, tmp_path, ["--json"], line_rate="0.80")

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["change"] is False
    assert payload["proposed"] == payload["current"] == 93


def test_the_shipped_policy_is_readable_and_complete():
    """Every key evaluate() indexes has to exist in the file it ships with.

    evaluate() reads the policy with `[]`, so a renamed or dropped key is a
    KeyError in the nightly rather than a message anyone can act on.
    """
    shipped = json.loads(auto_qa_tuner.POLICY.read_text(encoding="utf-8"))
    gate_def = shipped["gates"]["line_coverage"]

    assert {"current", "ceiling", "policy"} <= gate_def.keys()
    assert {"headroom", "step", "min_observations"} <= gate_def["policy"].keys()
    assert {"variable", "enforced_in"} <= gate_def.keys()
    # render() reads these two out of the gate for the "how to apply" line.
    assert gate_def["variable"] == "MIN_COVERAGE"
    assert gate_def["enforced_in"] == ".github/workflows/coverage-gate.yml"

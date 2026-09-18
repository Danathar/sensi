"""The development container's Home Assistant configuration and editor setup.

`.devcontainer/devcontainer.json` was already read for its build inputs - the
Dockerfile it points at, the context it builds from - but everything the
container actually *runs* was joined to nothing: the Home Assistant
configuration it boots, the port that configuration listens on, the timezone it
is told to use, and the editor settings a contributor inherits. Neither
`.devcontainer/configuration.yaml` nor `.dockerignore` was opened by any test at
all.

Each of those files is a hand-kept copy of a value this repository already
states somewhere machine-readable, and each drift direction fails quietly:

* a top-level key naming an integration Home Assistant no longer ships makes the
  container's core refuse to start, with no test that would have said so;
* a core-configuration key Home Assistant silently discards looks like it is
  configuring something and is not;
* the `logger:` entry is the component's logger name, which is computed from the
  package directory - renaming that directory turns debug logging off without
  changing this file;
* `appPort` publishes one specific container port, and Home Assistant listens on
  a different one only if this configuration says so;
* the editor settings restate `.editorconfig`, which VS Code does not read on
  its own.
"""

import ast
from functools import cache
import json
from pathlib import Path
import re
import subprocess
import sys
import zoneinfo

import pytest
import yaml

import homeassistant.components as ha_components
from homeassistant.components.logger import CONFIG_SCHEMA as LOGGER_CONFIG_SCHEMA
from homeassistant.const import CONF_UNIT_SYSTEM, SERVER_PORT
from homeassistant.core_config import (
    _CONF_UNIT_SYSTEM_IMPERIAL,
    _CONF_UNIT_SYSTEM_METRIC,
    _CONF_UNIT_SYSTEM_US_CUSTOMARY,
    CORE_CONFIG_SCHEMA,
)

_ROOT = Path(__file__).resolve().parents[1]
_DEVCONTAINER_DIR = _ROOT / ".devcontainer"
_DEVCONTAINER = _DEVCONTAINER_DIR / "devcontainer.json"
_HA_CONFIG = _DEVCONTAINER_DIR / "configuration.yaml"
_DOCKERIGNORE = _ROOT / ".dockerignore"
_DOCKERFILE = _ROOT / "Dockerfile"
_EDITORCONFIG = _ROOT / ".editorconfig"
_COMPONENT = _ROOT / "custom_components" / "sensi"
_REQUIREMENTS_TEST = _ROOT / "requirements_test.txt"
_VALIDATE_WORKFLOW = _ROOT / ".github" / "workflows" / "validate.yml"

# Home Assistant ships one directory per integration, each carrying a manifest.
# That directory list is the only authority on which domain names a
# configuration file may use.
_HA_COMPONENT_DIR = Path(ha_components.__file__).resolve().parent

# `.dockerignore` has no way to say "this path is supplied by the image, not by
# the repository", so an entry naming nothing tracked is indistinguishable from
# one that has gone stale. git already records that distinction, and these are
# the entries it cannot: docker always excludes `.git` regardless.
_DOCKERIGNORE_ALWAYS_VALID = {".git"}

# EditorConfig properties that a VS Code workspace setting can contradict. Only
# the properties the committed files actually use are mapped; an unmapped
# property is not silently treated as agreeing.
_EDITORCONFIG_TO_VSCODE = {
    "trim_trailing_whitespace": "files.trimTrailingWhitespace",
    "insert_final_newline": "files.insertFinalNewline",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@cache
def _tracked_files() -> frozenset[str]:
    """Return every path git tracks, as repository-relative POSIX strings."""

    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return frozenset(entry for entry in out.split("\0") if entry)


@cache
def _home_assistant_config() -> dict:
    loaded = yaml.safe_load(_read(_HA_CONFIG))
    assert isinstance(loaded, dict), (
        f"{_HA_CONFIG.name} did not parse into a mapping; Home Assistant would "
        "refuse to start on it"
    )
    return loaded


@cache
def _devcontainer() -> dict:
    return json.loads(_read(_DEVCONTAINER))


def _vscode() -> dict:
    return _devcontainer().get("customizations", {}).get("vscode", {})


@cache
def _component_logger_name() -> str:
    """Return the logger name `const.py` produces, computed from the tree."""

    return ".".join(_COMPONENT.relative_to(_ROOT).parts)


@cache
def _editorconfig_sections() -> list[tuple[str, dict[str, str]]]:
    """Return each `.editorconfig` section as (glob, properties), in order."""

    sections: list[tuple[str, dict[str, str]]] = []
    current: dict[str, str] | None = None
    for raw in _read(_EDITORCONFIG).splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = {}
            sections.append((line[1:-1], current))
            continue
        key, _, value = line.partition("=")
        if current is None:
            # `root = true` and anything else above the first section.
            continue
        current[key.strip()] = value.strip()
    return sections


def _as_bool(value: str) -> bool | None:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    return None


def _editorconfig_contradictions() -> list[str]:
    """Return the settings VS Code would apply against `.editorconfig`.

    A workspace setting is global: it applies to every file in the container.
    `.editorconfig` states different values per glob. Where the two disagree and
    the setting is not restated under a language-scoped override, whichever one
    the editor happens to obey decides - which is the thing worth failing on.
    """

    settings = _vscode().get("settings", {})
    contradictions: list[str] = []
    for prop, setting in _EDITORCONFIG_TO_VSCODE.items():
        if setting not in settings:
            continue
        configured = settings[setting]
        for glob, properties in _editorconfig_sections():
            if prop not in properties:
                continue
            declared = _as_bool(properties[prop])
            assert declared is not None, (
                f"{prop} = {properties[prop]!r} in [{glob}] is not a boolean; "
                "this reader cannot decide whether it contradicts anything"
            )
            if declared != configured:
                contradictions.append(
                    f"[{glob}] {prop} = {properties[prop]} vs "
                    f"{setting} = {json.dumps(configured)}"
                )
    return contradictions


def _has_editorconfig_extension() -> bool:
    return any(
        extension.lower().startswith("editorconfig.")
        for extension in _vscode().get("extensions", [])
    )


# --------------------------------------------------------------------------
# .devcontainer/configuration.yaml - what the container's Home Assistant runs
# --------------------------------------------------------------------------


def test_the_home_assistant_configuration_is_tracked_and_parses() -> None:
    """An unparseable configuration stops the container's core from starting."""

    assert str(_HA_CONFIG.relative_to(_ROOT)) in _tracked_files()
    assert _home_assistant_config(), "the configuration is empty"


@pytest.mark.parametrize("domain", sorted(_home_assistant_config()))
def test_every_configured_domain_is_an_integration_home_assistant_ships(
    domain: str,
) -> None:
    """A domain the pinned core does not ship is a startup error, not a no-op."""

    manifest = _HA_COMPONENT_DIR / domain / "manifest.json"
    assert manifest.is_file(), (
        f"configuration.yaml configures {domain!r}, which the pinned Home "
        "Assistant does not ship as an integration"
    )


def test_the_configuration_is_the_documented_default_config_replacement() -> None:
    """The file's own header says why it lists integrations one at a time."""

    header = _read(_HA_CONFIG).split("automation:")[0]
    # Not a bare `"default_config" in header`: the header also links to the
    # integration's documentation page, whose URL carries the same word.
    assert re.search(r"instead of\s+default_config", header), (
        "the header no longer explains that this list stands in for "
        "default_config, which is the reason each domain is named"
    )
    assert "default_config" not in _home_assistant_config(), (
        "configuration.yaml pulls in default_config as well as the individual "
        "integrations it was written to replace"
    )


def test_the_core_configuration_block_validates() -> None:
    """Home Assistant validates this block before anything else loads."""

    CORE_CONFIG_SCHEMA(_home_assistant_config()["homeassistant"])


def test_no_core_configuration_key_is_silently_discarded() -> None:
    """A key the schema drops configures nothing while appearing to.

    `CORE_CONFIG_SCHEMA` removes retired keys with `vol.Remove` rather than
    rejecting them, so a stale key survives validation and then has no effect.
    """

    committed = _home_assistant_config()["homeassistant"]
    validated = CORE_CONFIG_SCHEMA(committed)
    discarded = sorted(set(committed) - set(validated))
    assert not discarded, (
        f"Home Assistant discards {discarded} from the homeassistant: block - "
        "these keys read as configuration and change nothing"
    )


def test_the_unit_system_is_one_home_assistant_does_not_rewrite() -> None:
    """`imperial` still validates, and the core then renames it on startup.

    The schema accepts all three spellings, so validation is not evidence that
    the committed one is current: `async_process_ha_core_config` rewrites
    `imperial` to `us_customary` afterwards, which makes the committed value a
    description of a release that has moved on rather than a choice.
    """

    committed = _home_assistant_config()["homeassistant"][CONF_UNIT_SYSTEM]
    accepted = {_CONF_UNIT_SYSTEM_METRIC, _CONF_UNIT_SYSTEM_US_CUSTOMARY}
    assert committed in accepted, (
        f"unit_system is {committed!r}; Home Assistant keeps only "
        f"{sorted(accepted)} as written, and rewrites "
        f"{_CONF_UNIT_SYSTEM_IMPERIAL!r} on startup"
    )


def test_the_logger_block_validates_under_the_logger_integration() -> None:
    """The logger integration rejects an unknown level at setup, not at use."""

    config = _home_assistant_config()
    LOGGER_CONFIG_SCHEMA({"logger": config["logger"]})


def test_the_debug_logger_is_this_components_logger_name() -> None:
    """The key is the package path; renaming the package turns debug off."""

    logs = _home_assistant_config()["logger"]["logs"]
    assert _component_logger_name() in logs, (
        f"configuration.yaml raises the level for {sorted(logs)}, none of which "
        f"is this integration's logger {_component_logger_name()!r}"
    )


def test_the_component_logger_really_is_named_after_its_package() -> None:
    """Pin the derivation the assertion above depends on.

    `const.py` builds the logger from `__package__`. If it ever named the logger
    literally, the computed name above would stop being evidence about anything.
    """

    module = ast.parse(_read(_COMPONENT / "const.py"))
    calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "getLogger"
    ]
    assert len(calls) == 1, f"const.py makes {len(calls)} getLogger calls"
    (argument,) = calls[0].args
    assert isinstance(argument, ast.Name) and argument.id == "__package__", (
        "const.py no longer derives the logger name from __package__, so the "
        "name configuration.yaml has to match is no longer computable"
    )


def test_the_integration_is_raised_to_debug() -> None:
    """A container that logs this integration at the default level is pointless."""

    config = _home_assistant_config()
    validated = LOGGER_CONFIG_SCHEMA({"logger": config["logger"]})["logger"]
    assert validated["logs"][_component_logger_name()] < validated["default"], (
        "the integration's log level is not below the container-wide default, "
        "so the reason this configuration names it at all has gone"
    )


def test_the_time_zone_is_a_real_zone() -> None:
    """Home Assistant accepts the string; zoneinfo is what has to resolve it."""

    zone = _home_assistant_config()["homeassistant"]["time_zone"]
    assert zone in zoneinfo.available_timezones(), f"{zone!r} is not an IANA zone"


def test_the_container_and_home_assistant_agree_on_the_time_zone() -> None:
    """Two hand-kept copies: `TZ` stamps logs, `time_zone` stamps states."""

    container = _devcontainer()["containerEnv"]["TZ"]
    core = _home_assistant_config()["homeassistant"]["time_zone"]
    assert container == core, (
        f"the container runs in {container} while Home Assistant is configured "
        f"for {core}; timestamps in the log and in the UI would disagree"
    )


def test_the_published_port_reaches_home_assistants_listener() -> None:
    """`appPort` publishes one port; the core listens on exactly one."""

    published = _devcontainer()["appPort"]
    _, _, container_port = str(published).rpartition(":")
    configured = _home_assistant_config().get("http", {}) or {}
    expected = configured.get("server_port", SERVER_PORT)
    assert int(container_port) == int(expected), (
        f"devcontainer.json publishes container port {container_port} while "
        f"Home Assistant listens on {expected}"
    )


# --------------------------------------------------------------------------
# devcontainer.json - what a contributor's editor is told to do
# --------------------------------------------------------------------------


def test_the_python_formatter_is_an_extension_the_container_installs() -> None:
    """A default formatter that is not installed leaves files unformatted."""

    settings = _vscode().get("settings", {})
    formatter = settings["[python]"]["editor.defaultFormatter"]
    installed = {extension.lower() for extension in _vscode()["extensions"]}
    assert formatter.lower() in installed, (
        f"[python] formats with {formatter}, which is not in the extensions "
        "this devcontainer installs"
    )


def test_the_editor_formats_with_the_tool_ci_checks() -> None:
    """Formatting with anything else makes the format gate a surprise."""

    formatter = _vscode()["settings"]["[python]"]["editor.defaultFormatter"]
    _, _, tool = formatter.rpartition(".")

    workflow = yaml.safe_load(_read(_VALIDATE_WORKFLOW))
    bodies = [
        step["run"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "run" in step
    ]
    formatting = [body for body in bodies if "format" in body]
    assert formatting, "validate.yml no longer runs a format check"
    assert all(body.split()[0] == tool for body in formatting), (
        f"the editor formats with {tool!r} while validate.yml checks the "
        f"formatting with {[body.split()[0] for body in formatting]}"
    )
    assert re.search(rf"^{re.escape(tool)}==", _read(_REQUIREMENTS_TEST), re.M), (
        f"{tool} is the editor's formatter and CI's, but is not pinned in "
        "requirements_test.txt"
    )


def test_the_configured_pytest_arguments_are_flags_the_pinned_plugins_define() -> None:
    """An argument no installed plugin defines makes every editor run error.

    The arguments are resolved against a real pytest started the way the editor
    starts one, so dropping the plugin that defines a flag fails here instead of
    in whichever contributor next presses Run Tests.
    """

    arguments = _vscode()["settings"]["python.testing.pytestArgs"]
    assert arguments, "the editor runs pytest with no arguments at all"

    help_text = subprocess.run(
        [sys.executable, "-m", "pytest", "--help"],
        cwd=_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout

    for argument in arguments:
        name, _, _value = argument.partition("=")
        assert re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", help_text), (
            f"the editor runs pytest with {argument!r}, which no plugin in "
            "requirements_test.txt defines; every editor-launched run would "
            "fail with 'unrecognized arguments'"
        )


def test_the_editorconfig_really_does_contradict_the_workspace_settings() -> None:
    """Guard the check below against passing because it compares nothing.

    Markdown's `trim_trailing_whitespace = false` is deliberate - two trailing
    spaces are a line break - and the workspace setting trims globally. That
    disagreement is exactly why an EditorConfig-aware editor is required, so it
    is asserted rather than assumed.
    """

    contradictions = _editorconfig_contradictions()
    assert contradictions, (
        "no workspace setting contradicts .editorconfig any more; the "
        "requirement below is no longer evidence of anything and should be "
        "re-derived"
    )


def test_the_devcontainer_obeys_editorconfig() -> None:
    """VS Code ignores `.editorconfig` unless an extension reads it.

    `.editorconfig`'s own header says it exists so that an editor without the
    ruff extension still produces conforming files. Inside this devcontainer
    that is only true when something is installed to apply it.
    """

    assert _has_editorconfig_extension(), (
        "no EditorConfig extension is installed, so VS Code applies its own "
        "settings instead of .editorconfig: "
        + "; ".join(_editorconfig_contradictions())
    )


def test_every_configured_extension_is_named_publisher_dot_extension() -> None:
    """An id VS Code cannot resolve is skipped silently at container build."""

    for extension in _vscode()["extensions"]:
        assert re.fullmatch(r"[\w-]+\.[\w-]+", extension), (
            f"{extension!r} is not a marketplace id; the devcontainer build "
            "skips it without failing"
        )


# --------------------------------------------------------------------------
# .dockerignore - what the build context does not carry
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        line.strip()
        for line in _read(_DOCKERIGNORE).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ],
)
def test_every_dockerignore_entry_names_something_that_exists(entry: str) -> None:
    """A stale entry and a deliberate one are indistinguishable by reading."""

    if entry in _DOCKERIGNORE_ALWAYS_VALID:
        return

    candidate = entry.removeprefix("**/")
    tracked = _tracked_files()
    if candidate in tracked or any(
        path.startswith(f"{candidate}/") for path in tracked
    ):
        return

    # Not tracked - then it has to be one of the paths .gitignore records as
    # supplied by the devcontainer image, or the entry excludes nothing.
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", f"{candidate}/"],
        cwd=_ROOT,
        check=False,
    )
    assert ignored.returncode == 0, (
        f".dockerignore excludes {entry!r}, which is neither tracked nor "
        "gitignored; it excludes nothing"
    )


def test_the_dockerignore_hides_nothing_the_dockerfile_builds_in() -> None:
    """The exclusions only cost build-context transfer while nothing is copied.

    The moment the Dockerfile gains a `COPY`, an entry here can silently leave
    the copied path out of the image, so the check is written to fail for
    review rather than to keep passing.
    """

    instructions = re.findall(
        r"^\s*(?:COPY|ADD)\s+(.*)$", _read(_DOCKERFILE), flags=re.MULTILINE
    )
    assert not instructions, (
        "the Dockerfile now copies "
        f"{instructions} into the image; .dockerignore decides what those "
        "instructions can see and this test has to start checking it"
    )

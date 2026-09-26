"""Tests for Sensi switch component."""

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from custom_components.sensi.client import ActionResponse
from custom_components.sensi.const import (
    CONFIG_AUX_HEATING,
    CONFIG_FAN_SUPPORT,
    DEFAULT_CONFIG_FAN_SUPPORT,
    FAN_CIRCULATE_DUTY_CYCLE_DEFAULT,
)
from custom_components.sensi.data import OperatingMode
from custom_components.sensi.event import SettingEventName
from custom_components.sensi.switch import (
    SWITCH_TYPES,
    SensiAuxHeatSwitch,
    SensiCapabilityEntityDescription,
    SensiCapabilitySettingSwitch,
    SensiCirculatingFanSwitch,
    SensiFanSupportSwitch,
    SensiHumidificationSwitch,
    _mode_to_restore,
    async_setup_entry,
)
from homeassistant.config_entries import ConfigEntries
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import UNDEFINED

_COMPONENT = Path(__file__).parents[1] / "custom_components" / "sensi"
STRINGS_FILES = (
    _COMPONENT / "strings.json",
    _COMPONENT / "translations" / "en.json",
)


def create_humidity_description() -> SensiCapabilityEntityDescription:
    """Create SensiCapabilityEntityDescription."""
    return SensiCapabilityEntityDescription(
        key="display_humidity",
        setting=SettingEventName.DISPLAY_HUMIDITY,
        name="Display Humidity",
        icon="mdi:water-percent",
    )


async def test_setup_platform(
    hass: HomeAssistant,
    mock_coordinator,
    mock_device,
    mock_device_with_humidification,
) -> None:
    """Test platform setup."""

    mock_coordinator.get_devices = MagicMock(
        return_value=[mock_device, mock_device_with_humidification]
    )

    async_add_entities = MagicMock()
    await async_setup_entry(hass, mock_coordinator.config_entry, async_add_entities)

    assert async_add_entities.called

    # 7 = 4 from SWITCH_TYPES + SensiFanSupportSwitch + SensiAuxHeatSwitch + SensiCirculatingFanSwitch
    # 8 = 4 from SWITCH_TYPES + SensiFanSupportSwitch + SensiAuxHeatSwitch + SensiCirculatingFanSwitch + SensiHumidificationSwitch
    assert len(async_add_entities.call_args[0][0]) == 15


def test_capability_entity_description_creation() -> None:
    """Test creating a SensiCapabilityEntityDescription."""
    desc = create_humidity_description()
    assert desc.key == "display_humidity"
    assert desc.setting == SettingEventName.DISPLAY_HUMIDITY
    assert desc.name == "Display Humidity"
    assert desc.icon == "mdi:water-percent"
    assert desc.entity_category == EntityCategory.CONFIG


class TestSwitchTypes:
    """Test cases for SWITCH_TYPES configuration."""

    @pytest.mark.parametrize(
        ("key", "setting"),
        [
            ("display_humidity", SettingEventName.DISPLAY_HUMIDITY),
            ("continuous_backlight", SettingEventName.CONTINUOUS_BACKLIGHT),
            ("display_time", SettingEventName.DISPLAY_TIME),
            ("keypad_lockout", SettingEventName.KEYPAD_LOCKOUT),
        ],
    )
    def test_switch_type_exists(self, key, setting):
        """Test switch entity value type."""
        switches = [s for s in SWITCH_TYPES if s.key == key]
        assert len(switches) == 1
        assert switches[0].setting == setting

    def test_switch_types_with_icons(self) -> None:
        """Test that switches have appropriate icons."""
        icons_expected = {
            "display_humidity": "mdi:water-percent",
            "continuous_backlight": "mdi:wall-sconce-round",
            "display_time": "mdi:clock",
            "keypad_lockout": "mdi:lock",
        }
        for key, expected_icon in icons_expected.items():
            switch = next(s for s in SWITCH_TYPES if s.key == key)
            assert switch.icon == expected_icon

    def test_switch_types_are_named_through_translations(self) -> None:
        """Each switch names itself by `translation_key`, never a literal name.

        The key doubles as the translation key, so strings.json can be
        checked against SWITCH_TYPES by key alone.
        """
        for switch in SWITCH_TYPES:
            assert switch.translation_key == switch.key
            assert switch.name is UNDEFINED

    @pytest.mark.parametrize("path", STRINGS_FILES, ids=lambda p: p.name)
    def test_every_switch_translation_key_has_a_name(self, path: Path) -> None:
        """A translation_key without a string shows up as a blank entity name."""
        names = json.loads(path.read_text(encoding="utf-8"))["entity"]["switch"]

        for switch in SWITCH_TYPES:
            assert names[switch.translation_key]["name"], switch.key
        assert names["circulating_fan"]["name"] == "Circulating Fan"


class TestSensiCapabilitySettingSwitch:
    """Test cases for SensiCapabilitySettingSwitch."""

    def test_capability_setting_switch_initialization(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test SensiCapabilitySettingSwitch initialization."""

        description = create_humidity_description()

        switch = SensiCapabilitySettingSwitch(
            hass, mock_device, description, mock_coordinator.config_entry
        )

        assert switch._device == mock_device  # noqa: SLF001
        assert switch.entity_description == description
        assert switch.coordinator == mock_coordinator

    def test_capability_setting_switch_is_on(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test is_on property returns correct state."""

        mock_device.state.display_humidity = True
        description = create_humidity_description()

        switch = SensiCapabilitySettingSwitch(
            hass, mock_device, description, mock_coordinator.config_entry
        )

        assert switch.is_on is True

    def test_capability_setting_switch_is_off(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test is_on property when switch is off."""

        mock_device.state.display_humidity = False
        description = create_humidity_description()

        switch = SensiCapabilitySettingSwitch(
            hass, mock_device, description, mock_coordinator.config_entry
        )

        assert switch.is_on is False

    def test_capability_setting_switch_display_time(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test display_time switch."""

        mock_device.state.display_time = True

        description = SensiCapabilityEntityDescription(
            key="display_time",
            setting=SettingEventName.DISPLAY_TIME,
            name="Display Time",
        )

        switch = SensiCapabilitySettingSwitch(
            hass, mock_device, description, mock_coordinator.config_entry
        )

        assert switch.is_on is True

    def test_capability_setting_switch_keypad_lockout(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test keypad_lockout switch."""

        mock_device.state.keypad_lockout = False

        description = SensiCapabilityEntityDescription(
            key="keypad_lockout",
            setting=SettingEventName.KEYPAD_LOCKOUT,
            name="Keypad Lockout",
        )

        switch = SensiCapabilitySettingSwitch(
            hass, mock_device, description, mock_coordinator.config_entry
        )

        assert switch.is_on is False

    def test_capability_setting_switch_continuous_backlight(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test continuous_backlight switch."""

        mock_device.state.continuous_backlight = True

        description = SensiCapabilityEntityDescription(
            key="continuous_backlight",
            setting=SettingEventName.CONTINUOUS_BACKLIGHT,
            name="Continuous Backlight",
        )

        switch = SensiCapabilitySettingSwitch(
            hass, mock_device, description, mock_coordinator.config_entry
        )

        assert switch.is_on is True

    async def test_capability_setting_switch_continuous_backlight_update(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test update of continuous_backlight switch."""

        mock_device.state.continuous_backlight = True

        description = SensiCapabilityEntityDescription(
            key="continuous_backlight",
            setting=SettingEventName.CONTINUOUS_BACKLIGHT,
            name="Continuous Backlight",
        )

        switch = SensiCapabilitySettingSwitch(
            hass, mock_device, description, mock_coordinator.config_entry
        )

        with (
            patch.object(
                mock_coordinator.client, "_async_invoke_setter"
            ) as mock_async_set_bool_setting,
            patch.object(switch, "async_write_ha_state") as mock_async_write_ha_state,
        ):
            mock_async_set_bool_setting.return_value = ActionResponse(None, {})

            # First turn off and then back on
            await switch.async_turn_off()
            mock_async_write_ha_state.assert_called_once()
            assert switch.is_on is False

            mock_async_write_ha_state.reset_mock()
            await switch.async_turn_on()
            mock_async_write_ha_state.assert_called_once()
            assert switch.is_on is True

    async def test_capability_setting_switch_names_the_setting_in_the_error(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """The name now comes from translations, so the error names the key."""

        description = next(s for s in SWITCH_TYPES if s.key == "keypad_lockout")
        switch = SensiCapabilitySettingSwitch(
            hass, mock_device, description, mock_coordinator.config_entry
        )

        with patch.object(
            mock_coordinator.client, "async_set_bool_setting"
        ) as mock_async_set_bool_setting:
            mock_async_set_bool_setting.return_value = ActionResponse(
                "ThermostatOffline", None
            )

            with pytest.raises(
                HomeAssistantError, match="Unable to set keypad lockout to True"
            ):
                await switch.async_turn_on()


class TestSensiCirculatingFanSwitch:
    """The circulating fan switch ported from upstream v2.2.0 (iprak/sensi#165)."""

    def test_circulating_fan_switch_initialization(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """The key is upstream's, so a registry entry survives a move."""

        switch = SensiCirculatingFanSwitch(
            hass, mock_device, mock_coordinator.config_entry
        )

        assert switch._device == mock_device  # noqa: SLF001
        assert switch.coordinator == mock_coordinator
        assert switch.entity_description.key == "circulating_fan"
        assert switch.entity_description.translation_key == "circulating_fan"
        assert switch.entity_description.entity_category == EntityCategory.CONFIG
        assert switch.entity_description.icon == "mdi:fan"
        assert switch.entity_id == "switch.sensi_living_room_circulating_fan"
        assert switch.unique_id == f"{mock_device.identifier}_circulating_fan"

    @pytest.mark.parametrize("enabled", [True, False])
    def test_circulating_fan_switch_is_on(
        self, hass: HomeAssistant, mock_device, mock_coordinator, enabled
    ) -> None:
        """is_on reflects the circulating fan state."""

        mock_device.state.circulating_fan.enabled = enabled
        switch = SensiCirculatingFanSwitch(
            hass, mock_device, mock_coordinator.config_entry
        )

        assert switch.is_on is enabled

    @pytest.mark.parametrize("capable", [True, False])
    def test_circulating_fan_switch_available_when_capable(
        self, hass: HomeAssistant, mock_device, mock_coordinator, capable
    ) -> None:
        """A thermostat without a circulating fan shows the switch unavailable."""

        mock_device.capabilities.circulating_fan.capable = capable
        switch = SensiCirculatingFanSwitch(
            hass, mock_device, mock_coordinator.config_entry
        )

        assert switch.available is capable

    async def test_circulating_fan_switch_update(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Both directions send the thermostat's own duty cycle back."""

        mock_device.state.circulating_fan.duty_cycle = 30
        switch = SensiCirculatingFanSwitch(
            hass, mock_device, mock_coordinator.config_entry
        )

        with (
            patch.object(
                mock_coordinator.client, "async_set_circulating_fan_mode"
            ) as mock_set_circulating_fan_mode,
            patch.object(switch, "async_write_ha_state") as mock_async_write_ha_state,
            patch.object(
                mock_coordinator, "async_update_listeners"
            ) as mock_async_update_listeners,
        ):
            mock_set_circulating_fan_mode.return_value = ActionResponse(None, {})

            await switch.async_turn_off()
            await switch.async_turn_on()

            mock_set_circulating_fan_mode.assert_has_calls(
                [
                    call(mock_device, False, 30),
                    call(mock_device, True, 30),
                ]
            )
            assert mock_set_circulating_fan_mode.call_count == 2
            assert mock_async_write_ha_state.call_count == 2
            assert mock_async_update_listeners.call_count == 2

    async def test_circulating_fan_switch_without_a_duty_cycle_uses_the_default(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """A thermostat that reports no duty cycle gets the default, never 0."""

        mock_device.state.circulating_fan.duty_cycle = 0
        switch = SensiCirculatingFanSwitch(
            hass, mock_device, mock_coordinator.config_entry
        )

        with (
            patch.object(
                mock_coordinator.client, "async_set_circulating_fan_mode"
            ) as mock_set_circulating_fan_mode,
            patch.object(switch, "async_write_ha_state"),
            patch.object(mock_coordinator, "async_update_listeners"),
        ):
            mock_set_circulating_fan_mode.return_value = ActionResponse(None, {})

            await switch.async_turn_on()

            mock_set_circulating_fan_mode.assert_called_once_with(
                mock_device, True, FAN_CIRCULATE_DUTY_CYCLE_DEFAULT
            )

    async def test_circulating_fan_switch_error_is_raised(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """A refusal from the thermostat surfaces and leaves the state alone."""

        mock_device.state.circulating_fan.enabled = False
        switch = SensiCirculatingFanSwitch(
            hass, mock_device, mock_coordinator.config_entry
        )

        with (
            patch.object(
                mock_coordinator.client, "async_set_circulating_fan_mode"
            ) as mock_set_circulating_fan_mode,
            patch.object(switch, "async_write_ha_state") as mock_async_write_ha_state,
        ):
            mock_set_circulating_fan_mode.return_value = ActionResponse(
                "ThermostatOffline", None
            )

            with pytest.raises(
                HomeAssistantError, match="Unable to set circulating fan to True"
            ):
                await switch.async_turn_on()

            mock_async_write_ha_state.assert_not_called()
            assert switch.is_on is False


class TestSensiAuxHeatSwitch:
    """Test cases for SensiAuxHeatSwitch."""

    def test_aux_heat_switch_initialization(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test SensiAuxHeatSwitch initialization."""

        switch = SensiAuxHeatSwitch(hass, mock_device, mock_coordinator.config_entry)

        assert switch._device == mock_device  # noqa: SLF001
        assert switch.coordinator == mock_coordinator
        assert switch.entity_description.key == CONFIG_AUX_HEATING

    @pytest.mark.parametrize(("expected"), [(False), (True)])
    def test_aux_heat_switch_available_when_capable(
        self, hass: HomeAssistant, mock_device, mock_coordinator, expected
    ) -> None:
        """Test available property when aux heating is capable."""

        mock_device.capabilities.operating_mode_settings.aux = expected
        switch = SensiAuxHeatSwitch(hass, mock_device, mock_coordinator.config_entry)

        assert switch.available is expected

    @pytest.mark.parametrize(
        ("operating_mode", "expected"),
        [
            (OperatingMode.HEAT, False),
            (OperatingMode.COOL, False),
            (OperatingMode.OFF, False),
            (OperatingMode.AUX, True),
        ],
    )
    def test_aux_heat_switch_is_off_when_heat_mode(
        self,
        hass: HomeAssistant,
        mock_device,
        operating_mode,
        mock_coordinator,
        expected,
    ) -> None:
        """Test is_on property when operating mode is HEAT (not AUX)."""

        mock_device.state.operating_mode = operating_mode
        switch = SensiAuxHeatSwitch(hass, mock_device, mock_coordinator.config_entry)

        assert switch.is_on == expected

    async def test_aux_heat_switch_uses_previous_mode(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test that previous operating mode is used."""

        mock_device.state.operating_mode = OperatingMode.HEAT
        switch = SensiAuxHeatSwitch(hass, mock_device, mock_coordinator.config_entry)

        with (
            patch.object(switch, "async_write_ha_state") as mock_async_write_ha_state,
            patch.object(
                mock_coordinator, "async_update_listeners"
            ) as mock_async_update_listeners,
            patch.object(
                mock_coordinator.client, "async_set_operating_mode"
            ) as mock_async_set_operating_mode,
        ):
            mock_async_set_operating_mode.return_value = ActionResponse(None, {})
            initial_operating_mode = mock_device.state.operating_mode

            expected_calls = [
                call(mock_device, OperatingMode.AUX),
                call(mock_device, initial_operating_mode),
            ]

            await switch.async_turn_on()  # This will save off OperatingMode.HEAT
            await switch.async_turn_off()  # This will use the saved value

            mock_async_set_operating_mode.assert_has_calls(expected_calls)

            assert mock_async_write_ha_state.call_count == 2
            assert mock_async_update_listeners.call_count == 2


class TestSensiFanSupportSwitch:
    """Test cases for SensiFanSupportSwitch."""

    def test_fan_support_switch_initialization(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test SensiFanSupportSwitch initialization."""

        switch = SensiFanSupportSwitch(hass, mock_device, mock_coordinator.config_entry)

        assert switch._device == mock_device  # noqa: SLF001
        assert switch.coordinator == mock_coordinator
        assert switch.entity_description.key == CONFIG_FAN_SUPPORT
        assert switch.entity_description.entity_category == EntityCategory.CONFIG
        assert switch.entity_description.icon == "mdi:fan"
        assert switch.is_on is DEFAULT_CONFIG_FAN_SUPPORT

    async def test_fan_support_switch_update(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test update of fan support switch."""

        switch = SensiFanSupportSwitch(hass, mock_device, mock_coordinator.config_entry)

        # Update hass to support set_config_option() calls
        hass.config_entries = ConfigEntries(hass, {})
        switch.hass = hass

        with (
            patch.object(hass.config_entries, "async_update_entry"),
            patch.object(switch, "async_write_ha_state") as mock_async_write_ha_state,
        ):
            # First turn off and then back on
            await switch.async_turn_off()
            mock_async_write_ha_state.assert_called_once()
            assert switch.is_on is False

            mock_async_write_ha_state.reset_mock()
            await switch.async_turn_on()
            mock_async_write_ha_state.assert_called_once()
            assert switch.is_on is True


class TestSensiHumidificationSwitch:
    """Test cases for SensiHumidificationSwitch."""

    def test_humidification_switch_initialization(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """Test SensiHumidificationSwitch initialization."""

        switch = SensiHumidificationSwitch(
            hass, mock_device, mock_coordinator.config_entry
        )

        assert switch._device == mock_device  # noqa: SLF001
        assert switch.coordinator == mock_coordinator
        assert switch.entity_description.entity_category == EntityCategory.CONFIG
        assert switch.entity_description.icon == "mdi:air-humidifier"
        assert switch.entity_description.name == "Humidification"
        # The key is capitalised, so the unique_id keeps the capital and the
        # entity_id is slugified. Both are what existing installs registered.
        assert switch.unique_id == f"{mock_device.identifier}_Humidification"
        assert switch.entity_id == "switch.sensi_living_room_humidification"

    @pytest.mark.parametrize(
        ("expected"),
        [(True), (False)],
    )
    def test_humidification_switch_when_disabled(
        self, hass: HomeAssistant, mock_device, mock_coordinator, expected
    ) -> None:
        """Test is_on property when humidification is disabled."""

        mock_device.state.humidity_control.humidification.enabled = expected
        switch = SensiHumidificationSwitch(
            hass, mock_device, mock_coordinator.config_entry
        )

        assert switch.is_on is expected

    async def test_humidification_switch_update(
        self, hass: HomeAssistant, mock_device_with_humidification, mock_coordinator
    ) -> None:
        """Test update of humidification switch."""

        switch = SensiHumidificationSwitch(
            hass, mock_device_with_humidification, mock_coordinator.config_entry
        )
        with (
            patch.object(
                mock_coordinator.client, "_async_invoke_setter"
            ) as mock_async_update_humidification,
            patch.object(switch, "async_write_ha_state") as mock_async_write_ha_state,
            patch.object(
                mock_coordinator, "async_update_listeners"
            ) as mock_async_update_listeners,
        ):
            mock_async_update_humidification.return_value = ActionResponse(None, {})

            # First turn off and then back on
            await switch.async_turn_off()
            mock_async_write_ha_state.assert_called_once()
            mock_async_update_listeners.assert_called_once()
            assert switch.is_on is False

            mock_async_write_ha_state.reset_mock()
            mock_async_update_listeners.reset_mock()

            await switch.async_turn_on()
            mock_async_write_ha_state.assert_called_once()
            mock_async_update_listeners.assert_called_once()
            assert switch.is_on is True


class TestModeToRestore:
    """Test cases for the mode restored when aux heating is turned off."""

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            (OperatingMode.HEAT, OperatingMode.HEAT),
            (OperatingMode.COOL, OperatingMode.COOL),
            (OperatingMode.OFF, OperatingMode.OFF),
            (OperatingMode.AUTO, OperatingMode.AUTO),
            # AUX would leave the switch permanently on, this happens when the
            # thermostat is already aux heating as the entity is created.
            (OperatingMode.AUX, OperatingMode.HEAT),
            (None, OperatingMode.HEAT),
        ],
    )
    def test_mode_to_restore(self, mode, expected) -> None:
        """Test the mode used to turn aux heating off."""
        assert _mode_to_restore(mode) == expected

    async def test_aux_switch_can_be_turned_off_when_started_in_aux(
        self, hass: HomeAssistant, mock_device, mock_coordinator
    ) -> None:
        """A thermostat already in AUX must still be able to turn aux off."""

        mock_device.state.operating_mode = OperatingMode.AUX
        switch = SensiAuxHeatSwitch(hass, mock_device, mock_coordinator.config_entry)

        with (
            patch.object(switch, "async_write_ha_state"),
            patch.object(
                mock_coordinator.client, "async_set_operating_mode"
            ) as mock_set_operating_mode,
        ):
            mock_set_operating_mode.return_value = ActionResponse(None, {})

            await switch.async_turn_off()

            mock_set_operating_mode.assert_called_once_with(
                mock_device, OperatingMode.HEAT
            )


class TestCapabilitySettingNames:
    """One setting is named four times, and the names are joined only by convention.

    A `SWITCH_TYPES` entry's `key` is read as an attribute of `Capabilities`
    (whether to create the switch) and of `State` (`is_on`). Its `setting` is
    the event sent to the backend, and `async_set_bool_setting` records a
    success by dropping the event's `set_` prefix and writing the attribute of
    that name back onto `State`. None of those four names is derived from
    another, so they have to agree for every entry.
    """

    @pytest.mark.parametrize("description", SWITCH_TYPES, ids=lambda d: d.key)
    def test_the_event_names_the_key(
        self, description: SensiCapabilityEntityDescription
    ) -> None:
        """The attribute the client writes on success is the one `is_on` reads."""
        assert description.setting.value == f"set_{description.key}"

    @pytest.mark.parametrize("description", SWITCH_TYPES, ids=lambda d: d.key)
    def test_the_key_is_a_state_and_capability_attribute(
        self, description: SensiCapabilityEntityDescription, mock_device
    ) -> None:
        """`getattr` would raise at setup, or read a stale value, on a missing name.

        Checked against a real parsed device so an attribute that only exists
        on a mock cannot pass.
        """
        assert isinstance(getattr(mock_device.state, description.key), bool)
        assert isinstance(getattr(mock_device.capabilities, description.key), bool)

    @pytest.mark.parametrize("description", SWITCH_TYPES, ids=lambda d: d.key)
    async def test_a_write_through_the_real_client_moves_is_on(
        self,
        hass: HomeAssistant,
        mock_device,
        mock_coordinator,
        description: SensiCapabilityEntityDescription,
    ) -> None:
        """Turning a switch on and off changes what it reports, for every entry.

        Only the transport is patched: `async_set_bool_setting` runs for real,
        so a key it does not write back would leave `is_on` unchanged, and an
        attribute name it made up would appear on `State` as a new attribute.
        """
        switch = SensiCapabilitySettingSwitch(
            hass, mock_device, description, mock_coordinator.config_entry
        )
        attributes_before = set(vars(mock_device.state))

        with (
            patch.object(
                mock_coordinator.client,
                "_async_invoke_setter",
                return_value=ActionResponse(None, {}),
            ) as mock_invoke_setter,
            patch.object(switch, "async_write_ha_state"),
        ):
            await switch.async_turn_off()
            assert switch.is_on is False

            await switch.async_turn_on()
            assert switch.is_on is True

        assert mock_invoke_setter.call_args.args[0] == description.setting.value
        assert set(vars(mock_device.state)) == attributes_before

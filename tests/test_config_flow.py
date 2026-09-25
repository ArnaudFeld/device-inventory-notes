"""Tests for the config flow.

The integration cannot be imported without Home Assistant on sys.path, so the
HA modules are faked. Everything the flow itself owns is real: voluptuous does
the schema assembly, default prefill and PREVENT_EXTRA enforcement, and the
generator helpers (order_prefill, normalize_order, mirror_conflicts_with_export)
are the shipped ones.

The selector stubs deliberately pass values through unchanged. Selector
coercion is Home Assistant's own behaviour, and re-implementing it here would
only test the stub. What these tests pin down is the flow's own logic.
"""

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import voluptuous as vol
except ImportError:  # pragma: no cover
    raise unittest.SkipTest(
        "voluptuous is not installed; run: pip install voluptuous"
    ) from None


class _Selector:
    """Stand-in for a HA selector: records its config, validates nothing."""

    def __init__(self, config=None):
        self.config = config

    def __call__(self, value):
        return value

    def __repr__(self):
        return f"{type(self).__name__}({self.config!r})"


class _DictStub(dict):
    """Selectors take TypedDicts; a dict that takes kwargs is close enough."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def _install_ha_stubs():
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.ServiceCall = type("ServiceCall", (), {})
    core.SupportsResponse = type("SupportsResponse", (), {})
    core.callback = lambda func: func

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigFlow:
        def __init__(self):
            self.hass = None
            self.handler = None

        def __init_subclass__(cls, **kwargs):
            # config_flow declares `class ...(ConfigFlow, domain=DOMAIN)`.
            super().__init_subclass__()

        def async_show_form(
            self, *, step_id, data_schema=None, errors=None,
            description_placeholders=None, last_step=None,
        ):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders,
            }

        def async_create_entry(self, *, title, data, **kwargs):
            return {"type": "create_entry", "title": title, "data": data}

    class OptionsFlow:
        def __init__(self):
            self.hass = None
            self.handler = None
            self.config_entry = None

        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

        def async_show_form(
            self, *, step_id, data_schema=None, errors=None,
            description_placeholders=None, last_step=None,
        ):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders,
            }

        def async_create_entry(self, *, title=None, data=None, **kwargs):
            return {"type": "create_entry", "title": title, "data": data}

    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = OptionsFlow
    config_entries.ConfigEntry = type("ConfigEntry", (), {})

    selector = types.ModuleType("homeassistant.helpers.selector")
    for name in (
        "BooleanSelector", "TimeSelector", "NumberSelector",
        "EntitySelector", "DeviceSelector",
    ):
        setattr(selector, name, type(name, (_Selector,), {}))
    selector.SelectSelector = type("SelectSelector", (_Selector,), {})
    selector.TextSelector = type("TextSelector", (_Selector,), {})
    selector.SelectOptionDict = _DictStub
    selector.SelectSelectorConfig = _DictStub
    selector.TextSelectorConfig = _DictStub
    selector.NumberSelectorConfig = _DictStub

    class _SelectSelectorMode:
        LIST = "list"
        DROPDOWN = "dropdown"

    selector.SelectSelectorMode = _SelectSelectorMode

    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    device_registry.async_get = lambda hass: getattr(hass, "device_registry", None)

    class DeviceEntryType:
        SERVICE = "service"

    device_registry.DeviceEntryType = DeviceEntryType
    device_registry.DeviceEntry = SimpleNamespace

    for name in ("area_registry", "entity_registry", "floor_registry"):
        module = types.ModuleType(f"homeassistant.helpers.{name}")
        module.async_get = lambda hass: None
        sys.modules[f"homeassistant.helpers.{name}"] = module
        setattr(helpers, name, module)

    helpers.config_entries = config_entries
    helpers.selector = selector
    helpers.device_registry = device_registry
    homeassistant.core = core
    homeassistant.helpers = helpers
    homeassistant.config_entries = config_entries

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.helpers.selector"] = selector
    sys.modules["homeassistant.helpers.device_registry"] = device_registry

    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = []
    package = types.ModuleType("custom_components.device_inventory_notes")
    package.__path__ = [
        str(Path(__file__).parents[1] / "custom_components" / "device_inventory_notes")
    ]
    sys.modules["custom_components"] = custom_components
    sys.modules["custom_components.device_inventory_notes"] = package


_install_ha_stubs()

config_flow = importlib.import_module(
    "custom_components.device_inventory_notes.config_flow"
)
const = importlib.import_module("custom_components.device_inventory_notes.const")
generator = importlib.import_module(
    "custom_components.device_inventory_notes.generator"
)

CONF_EXPORT_DIR = const.CONF_EXPORT_DIR
CONF_EXTRA_DOMAINS = const.CONF_EXTRA_DOMAINS
CONF_EXTRA_MAP = const.CONF_EXTRA_MAP
CONF_EXTRA_PROTOCOL = const.CONF_EXTRA_PROTOCOL
CONF_EXTRA_REMOVE = const.CONF_EXTRA_REMOVE
CONF_FIELDS = const.CONF_FIELDS
CONF_FIELD_ORDER = const.CONF_FIELD_ORDER
CONF_LAYOUT = const.CONF_LAYOUT
CONF_OBSIDIAN_BASE = const.CONF_OBSIDIAN_BASE
DEFAULT_FIELDS = const.DEFAULT_FIELDS
DEFAULT_LAYOUT = const.DEFAULT_LAYOUT
LAYOUT_PROTOCOL = const.LAYOUT_PROTOCOL
MIRROR_DIRNAME = generator.MIRROR_DIRNAME


def _schema_defaults(schema):
    """The defaults voluptuous fills in for an empty submission.

    Running the schema is the honest way to read them: it is the same path a
    submitted form takes, so a default vol could not fill in would show up
    here instead of passing silently.
    """
    return schema({})


def _fake_hass(config_dir, devices=(), entries=(), language="de"):
    return SimpleNamespace(
        config=SimpleNamespace(config_dir=str(config_dir), language=language),
        config_entries=SimpleNamespace(
            async_entries=lambda: list(entries)
        ),
        device_registry=SimpleNamespace(devices=list(devices)),
    )


def _device(
    ident,
    name="Sensor",
    name_by_user=None,
    default_name=None,
    manufacturer="ACME",
    model="X1",
    via_device_id=None,
    entry_type=None,
    config_entry_id=None,
):
    return SimpleNamespace(
        id=ident[1] if ident else "dev",
        identifiers=(ident,) if ident else (),
        name=name,
        name_by_user=name_by_user,
        default_name=default_name,
        manufacturer=manufacturer,
        model=model,
        via_device_id=via_device_id,
        entry_type=entry_type,
        config_entry_id=config_entry_id,
    )


class PreventExtraTest(unittest.TestCase):
    """Every step schema must reject keys it does not declare.

    The step functions collect user input into a single options dict, so an
    unexpected key would be written straight into the config entry.
    """

    def _assert_rejects_extra(self, schema, payload):
        with self.assertRaises(vol.Invalid):
            schema(payload)

    def test_layout_step_rejects_unknown_key(self):
        self._assert_rejects_extra(
            config_flow._schema_layout({}),
            {CONF_LAYOUT: LAYOUT_PROTOCOL, "not_a_setting": 1},
        )

    def test_fields_step_rejects_unknown_key(self):
        self._assert_rejects_extra(
            config_flow._schema_fields({}),
            {CONF_FIELDS: list(DEFAULT_FIELDS), "layout": "protocol"},
        )

    def test_order_step_rejects_unknown_key(self):
        self._assert_rejects_extra(
            config_flow._schema_order({}),
            {CONF_FIELD_ORDER: "notiz", "fields": ["a"]},
        )

    def test_extra_step_rejects_unknown_key(self):
        schema = config_flow._schema_extra({}, {"shelly": 3})
        self._assert_rejects_extra(
            schema,
            {CONF_EXTRA_DOMAINS: ["shelly"], "export_dir": "/share"},
        )

    def test_settings_step_rejects_unknown_key(self):
        self._assert_rejects_extra(
            config_flow._schema_settings({}),
            {CONF_EXPORT_DIR: "/share/x", CONF_OBSIDIAN_BASE: "a", "junk": True},
        )

    def test_declared_keys_still_validate(self):
        """Guard against a schema that rejects everything, not just extras."""
        config_flow._schema_layout({})({CONF_LAYOUT: LAYOUT_PROTOCOL})
        config_flow._schema_fields({})({CONF_FIELDS: list(DEFAULT_FIELDS)})
        config_flow._schema_order({})({CONF_FIELD_ORDER: "notiz"})
        config_flow._schema_settings({})(
            {
                CONF_EXPORT_DIR: "/share/device_inventory_notes",
                CONF_OBSIDIAN_BASE: "Actors",
            }
        )
        config_flow._schema_extra({}, {"shelly": 1})({CONF_EXTRA_PROTOCOL: "mqtt"})


class SchemaDefaultTest(unittest.TestCase):
    """Defaults must come from the stored options, not from the defaults."""

    def test_layout_default_falls_back(self):
        self.assertEqual(
            _schema_defaults(config_flow._schema_layout({}))[CONF_LAYOUT],
            DEFAULT_LAYOUT,
        )

    def test_layout_default_uses_stored_value(self):
        self.assertEqual(
            _schema_defaults(
                config_flow._schema_layout({CONF_LAYOUT: "area"})
            )[CONF_LAYOUT],
            "area",
        )

    def test_fields_default_uses_stored_value(self):
        self.assertEqual(
            _schema_defaults(
                config_flow._schema_fields({CONF_FIELDS: ["notiz", "tags"]})
            )[CONF_FIELDS],
            ["notiz", "tags"],
        )

    def test_fields_default_falls_back_to_default_fields(self):
        self.assertEqual(
            _schema_defaults(config_flow._schema_fields({}))[CONF_FIELDS],
            list(DEFAULT_FIELDS),
        )

    def test_order_default_is_the_prefill(self):
        current = {CONF_FIELDS: ["notiz", "protokoll", "tags"]}
        self.assertEqual(
            _schema_defaults(config_flow._schema_order(current))[CONF_FIELD_ORDER],
            generator.order_prefill(current),
        )

    def test_settings_defaults_use_stored_values(self):
        defaults = _schema_defaults(
            config_flow._schema_settings(
                {CONF_EXPORT_DIR: "/share/custom", CONF_OBSIDIAN_BASE: "Actors"}
            )
        )
        self.assertEqual(defaults[CONF_EXPORT_DIR], "/share/custom")
        self.assertEqual(defaults[CONF_OBSIDIAN_BASE], "Actors")

    def test_placeholder_links_localised(self):
        de = config_flow._placeholder_links("de")
        en = config_flow._placeholder_links("en")
        self.assertIn("Notizen-Übersicht", de["notes_link"])
        self.assertIn("Notes overview", en["notes_link"])
        self.assertIn("ZIP-Datei", de["zip_link"])
        self.assertIn("ZIP file", en["zip_link"])
        # The filename is percent-encoded, not raw.
        self.assertNotIn("01-Übersicht Aktoren.md", de["notes_link"])


class MirrorGuardTest(unittest.TestCase):
    """The www mirror deletes its own tree before rebuilding.

    An export directory inside that tree would be destroyed by the mirror that
    is supposed to copy it, so the flow has to refuse it.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _errors(self, export_dir):
        hass = _fake_hass(self.config_dir)
        return config_flow._settings_errors(
            hass, {CONF_EXPORT_DIR: export_dir}
        )

    def test_rejects_the_mirror_directory_itself(self):
        target = Path(self.config_dir) / "www" / MIRROR_DIRNAME
        self.assertEqual(self._errors(str(target)), {"base": "export_dir_in_www"})

    def test_rejects_a_subdirectory_of_the_mirror(self):
        target = Path(self.config_dir) / "www" / MIRROR_DIRNAME / "Zigbee"
        self.assertEqual(self._errors(str(target)), {"base": "export_dir_in_www"})

    def test_rejects_a_relative_path_into_the_mirror(self):
        target = Path("www") / MIRROR_DIRNAME
        self.assertEqual(self._errors(str(target)), {"base": "export_dir_in_www"})

    def test_accepts_a_sibling_with_a_shared_prefix(self):
        """'/config/www/device_inventory_notes_backup' is outside the tree.

        A naive startswith() check would reject this one.
        """
        target = Path(self.config_dir) / "www" / f"{MIRROR_DIRNAME}_backup"
        self.assertEqual(self._errors(str(target)), {})

    def test_accepts_an_unrelated_export_directory(self):
        self.assertEqual(self._errors("/share/device_inventory_notes"), {})

    def test_accepts_an_empty_export_dir(self):
        self.assertEqual(self._errors(""), {})


class CandidateDomainsTest(unittest.TestCase):
    """The step that suggests protocol mappings must mirror the filter chain.

    The domain names below are deliberately absent from PROTOCOL_MAP and
    SERVICE_DOMAINS, so a device carrying one is a genuine candidate.
    """

    def _domains(self, devices, entries=()):
        hass = _fake_hass("/config", devices, entries)
        return config_flow._candidate_domains(hass)

    def test_counts_unknown_domains(self):
        devices = [
            _device(("teleinfo", "a")),
            _device(("teleinfo", "b")),
            _device(("tasmota", "c")),
        ]
        self.assertEqual(self._domains(devices), {"teleinfo": 2, "tasmota": 1})

    def test_skips_service_devices(self):
        devices = [
            _device(("tasmota", "a"), entry_type="service"),
            _device(("teleinfo", "b"), entry_type=None),
        ]
        self.assertEqual(self._domains(devices), {"teleinfo": 1})

    def test_skips_children_of_another_device(self):
        parent = _device(("tasmota", "parent"))
        child = _device(("tasmota", "child"), via_device_id="parent")
        self.assertEqual(self._domains([parent, child]), {"tasmota": 1})

    def test_skips_unnamed_devices(self):
        devices = [
            _device(("tasmota", "a"), name="", name_by_user=None, default_name=None),
        ]
        self.assertEqual(self._domains(devices), {})

    def test_skips_devices_without_manufacturer_and_model(self):
        devices = [_device(("tasmota", "a"), manufacturer=None, model=None)]
        self.assertEqual(self._domains(devices), {})

    def test_skips_domains_that_already_resolve(self):
        self.assertEqual(self._domains([_device(("zha", "a"))]), {})

    def test_skips_service_domains_from_identifiers(self):
        self.assertEqual(self._domains([_device(("glances", "a"))]), {})

    def test_skips_the_zigbee2mqtt_bridge(self):
        devices = [
            _device(("mqtt", "zigbee2mqtt_bridge")),
            _device(("mqtt", "zigbee2mqtt_lamp_1")),
        ]
        self.assertEqual(self._domains(devices), {})

    def test_identifier_and_entry_domain_are_both_counted(self):
        """A device can be reachable under two names; both are worth offering."""
        entry = SimpleNamespace(entry_id="entry1", domain="tasmota")
        devices = [_device(("myident", "a"), config_entry_id="entry1")]
        self.assertEqual(
            self._domains(devices, [entry]), {"tasmota": 1, "myident": 1}
        )

    def test_device_without_a_domain_is_skipped(self):
        devices = [_device(None, name="Orphan")]
        self.assertEqual(self._domains(devices), {})

    def test_sorted_by_count_then_name(self):
        devices = [
            _device(("tasmota", "a")),
            _device(("myhub", "b")),
            _device(("myhub", "c")),
        ]
        self.assertEqual(list(self._domains(devices)), ["myhub", "tasmota"])


class OrderRoundTripTest(unittest.TestCase):
    """A stored custom order must survive reopening the options flow."""

    def _flow(self, stored=None):
        flow = config_flow.DeviceInventoryNotesOptionsFlow()
        flow.hass = _fake_hass("/config")
        flow.config_entry = SimpleNamespace(data={}, options=dict(stored or {}))
        return flow

    # CONF_FIELDS holds the labels shown in the selector, CONF_FIELD_ORDER the
    # internal frontmatter keys. DEFAULT_FIELDS is always part of the selection,
    # so the prefill always offers the default fields in addition.

    def test_custom_order_survives_reopening(self):
        """The bug this guards: reopening the flow reset the order to default."""
        stored = {
            CONF_FIELDS: ["Notiz", "Lagerort"],
            CONF_FIELD_ORDER: ["lagerort", "notiz"],
        }
        flow = self._flow(stored)
        _run(flow.async_step_init())
        _run(flow.async_step_fields())
        prefill = _schema_defaults(
            _run(flow.async_step_order())["data_schema"]
        )[CONF_FIELD_ORDER]
        keys, unknown = generator.normalize_order(prefill)
        self.assertEqual(keys[:2], ["lagerort", "notiz"])
        self.assertEqual(unknown, [])

    def test_a_deselected_known_field_is_dropped_from_the_prefill(self):
        """'lagerort' is neither selected nor a default field, so it goes."""
        stored = {
            CONF_FIELDS: ["Notiz"],
            CONF_FIELD_ORDER: ["lagerort", "notiz"],
        }
        flow = self._flow(stored)
        _run(flow.async_step_init())
        _run(flow.async_step_fields())
        prefill = _schema_defaults(
            _run(flow.async_step_order())["data_schema"]
        )[CONF_FIELD_ORDER]
        keys, _ = generator.normalize_order(prefill)
        self.assertEqual(keys[0], "notiz")
        self.assertNotIn("lagerort", keys)

    def test_a_hand_added_field_stays_in_the_prefill(self):
        """A field the integration does not know must not silently vanish."""
        stored = {
            CONF_FIELDS: ["Notiz", "mein_feld"],
            CONF_FIELD_ORDER: ["notiz", "mein_feld"],
        }
        flow = self._flow(stored)
        _run(flow.async_step_init())
        _run(flow.async_step_fields())
        prefill = _schema_defaults(
            _run(flow.async_step_order())["data_schema"]
        )[CONF_FIELD_ORDER]
        keys, unknown = generator.normalize_order(prefill)
        self.assertEqual(keys[:2], ["notiz", "mein_feld"])
        self.assertEqual(unknown, ["mein_feld"])

    def test_a_field_selected_but_not_ordered_is_appended(self):
        stored = {
            CONF_FIELDS: ["Notiz", "Lagerort"],
            CONF_FIELD_ORDER: ["notiz"],
        }
        flow = self._flow(stored)
        _run(flow.async_step_init())
        _run(flow.async_step_fields())
        prefill = _schema_defaults(
            _run(flow.async_step_order())["data_schema"]
        )[CONF_FIELD_ORDER]
        keys, _ = generator.normalize_order(prefill)
        self.assertEqual(keys[0], "notiz")
        self.assertIn("lagerort", keys)
        self.assertLess(keys.index("notiz"), keys.index("lagerort"))

    def test_unknown_line_does_not_block_saving(self):
        """A hand-added field has no known label; it must not block the flow.

        Refusing to save would make the field impossible to keep.
        """
        flow = self._flow({CONF_FIELD_ORDER: ["notiz", "handgemacht"]})
        _run(flow.async_step_init())
        _run(flow.async_step_fields())
        _run(flow.async_step_order({CONF_FIELD_ORDER: "notiz\nhandgemacht"}))
        self.assertEqual(
            flow._flow_data[CONF_FIELD_ORDER], ["notiz", "handgemacht"]
        )

    def test_submitted_order_is_normalised_to_keys(self):
        """The form shows labels; what gets stored has to be keys."""
        flow = self._flow({})
        _run(flow.async_step_init())
        _run(flow.async_step_fields())
        _run(
            flow.async_step_order(
                {CONF_FIELD_ORDER: "Lagerort\nNotiz"}
            )
        )
        self.assertEqual(
            flow._flow_data[CONF_FIELD_ORDER], ["lagerort", "notiz"]
        )


class SettingsStepTest(unittest.TestCase):
    def _flow(self, stored=None):
        flow = config_flow.DeviceInventoryNotesOptionsFlow()
        flow.hass = _fake_hass("/config")
        flow.config_entry = SimpleNamespace(data={}, options=dict(stored or {}))
        return flow

    def test_export_dir_inside_www_is_refused(self):
        flow = self._flow({})
        result = _run(
            flow.async_step_settings(
                {CONF_EXPORT_DIR: "/config/www/device_inventory_notes"}
            )
        )
        self.assertEqual(result["errors"], {"base": "export_dir_in_www"})

    def test_settings_are_saved_when_valid(self):
        flow = self._flow({})
        result = _run(
            flow.async_step_settings({CONF_EXPORT_DIR: "/share/inventory"})
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][CONF_EXPORT_DIR], "/share/inventory")

    def test_extra_step_is_skipped_when_nothing_to_suggest(self):
        flow = self._flow({})
        result = _run(flow.async_step_extra())
        self.assertEqual(result["step_id"], "settings")

    def test_extra_step_is_shown_when_candidates_exist(self):
        flow = self._flow({})
        flow.hass = _fake_hass(
            "/config", devices=[_device(("tasmota", "a"))]
        )
        result = _run(flow.async_step_extra())
        self.assertEqual(result["step_id"], "extra")

    def test_extra_domains_are_added_to_the_map(self):
        flow = self._flow({})
        _run(
            flow.async_step_extra(
                {
                    CONF_EXTRA_DOMAINS: ["shelly"],
                    CONF_EXTRA_PROTOCOL: "mqtt",
                }
            )
        )
        self.assertEqual(flow._flow_data[CONF_EXTRA_MAP], {"shelly": "mqtt"})

    def test_extra_domains_can_be_removed(self):
        flow = self._flow({CONF_EXTRA_MAP: {"shelly": "mqtt"}})
        _run(flow.async_step_extra({CONF_EXTRA_REMOVE: ["shelly"]}))
        self.assertEqual(flow._flow_data[CONF_EXTRA_MAP], {})

    def test_adding_and_removing_in_one_submission(self):
        flow = self._flow({CONF_EXTRA_MAP: {"old": "mqtt"}})
        _run(
            flow.async_step_extra(
                {
                    CONF_EXTRA_DOMAINS: ["new"],
                    CONF_EXTRA_REMOVE: ["old"],
                    CONF_EXTRA_PROTOCOL: "http",
                }
            )
        )
        self.assertEqual(flow._flow_data[CONF_EXTRA_MAP], {"new": "http"})


class ConfigFlowSequenceTest(unittest.TestCase):
    def _flow(self):
        flow = config_flow.DeviceInventoryNotesConfigFlow()
        flow.hass = _fake_hass("/config")
        return flow

    def test_full_walk_reaches_create_entry(self):
        flow = self._flow()
        self.assertEqual(_run(flow.async_step_user())["step_id"], "user")
        self.assertEqual(
            _run(flow.async_step_user({CONF_LAYOUT: LAYOUT_PROTOCOL}))["step_id"],
            "fields",
        )
        self.assertEqual(
            _run(flow.async_step_fields({CONF_FIELDS: ["notiz", "protokoll"]}))[
                "step_id"
            ],
            "order",
        )
        self.assertEqual(
            _run(flow.async_step_order({CONF_FIELD_ORDER: "notiz\nprotokoll"}))[
                "step_id"
            ],
            "settings",
        )
        result = _run(
            flow.async_step_settings(
                {
                    CONF_EXPORT_DIR: "/share/inventory",
                    CONF_OBSIDIAN_BASE: "Actors",
                }
            )
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][CONF_FIELD_ORDER], ["notiz", "protokoll"])
        self.assertEqual(result["data"][CONF_EXPORT_DIR], "/share/inventory")

    def test_settings_error_keeps_the_flow_open(self):
        flow = self._flow()
        _run(flow.async_step_user())
        _run(flow.async_step_user({CONF_LAYOUT: LAYOUT_PROTOCOL}))
        _run(flow.async_step_fields({CONF_FIELDS: ["notiz"]}))
        _run(flow.async_step_order({CONF_FIELD_ORDER: "notiz"}))
        result = _run(
            flow.async_step_settings(
                {CONF_EXPORT_DIR: "/config/www/device_inventory_notes"}
            )
        )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "export_dir_in_www"})

    def test_options_flow_is_reachable_from_the_config_flow(self):
        flow = self._flow()
        options = flow.async_get_options_flow(object())
        self.assertIsInstance(
            options, config_flow.DeviceInventoryNotesOptionsFlow
        )


def _run(coro):
    """Drive one flow step to completion."""
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()

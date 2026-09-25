import importlib
import sys
import tempfile
import types
import unittest
from datetime import datetime as DateTime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml


def _load_generator():
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    helper_modules = {}
    for name in (
        "area_registry",
        "device_registry",
        "entity_registry",
        "floor_registry",
    ):
        module = types.ModuleType(f"homeassistant.helpers.{name}")
        helper_modules[name] = module
        sys.modules[f"homeassistant.helpers.{name}"] = module
    helpers.__dict__.update(helper_modules)
    homeassistant.core = core
    homeassistant.helpers = helpers
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = []
    package = types.ModuleType("custom_components.device_inventory_notes")
    package.__path__ = [str(Path(__file__).parents[1] / "custom_components" / "device_inventory_notes")]
    sys.modules["custom_components"] = custom_components
    sys.modules["custom_components.device_inventory_notes"] = package
    return importlib.import_module("custom_components.device_inventory_notes.generator")


generator_module = _load_generator()
const_module = importlib.import_module("custom_components.device_inventory_notes.const")
DeviceNoteGenerator = generator_module.DeviceNoteGenerator
FIELD_ORDER = const_module.FIELD_ORDER
HAND_FIELDS = const_module.HAND_FIELDS
LAYOUT_PROTOCOL = const_module.LAYOUT_PROTOCOL
MERGE_MODE_MERGE = const_module.MERGE_MODE_MERGE
CONF_FIELDS = const_module.CONF_FIELDS
CONF_FIELD_ORDER = const_module.CONF_FIELD_ORDER
SELECTABLE_FIELDS = const_module.SELECTABLE_FIELDS
parse_note = generator_module.parse_note
serialize_note = generator_module.serialize_note


class FixedDateTime:
    @classmethod
    def now(cls):
        return DateTime(2026, 9, 25, 12, 0, 0)


class _SingleEntryDevice(SimpleNamespace):
    """Device stub that explodes when the deprecated property is read."""

    @property
    def config_entries(self):
        raise AssertionError(
            "DeviceEntry.config_entries ist seit 2026.8 abgeschafft – "
            "DeviceEntry.config_entry_id verwenden"
        )


class DeviceNoteGeneratorTests(unittest.TestCase):
    def test_constructor_reads_device_type_map(self):
        hass = SimpleNamespace(config=SimpleNamespace(config_dir="/config"))
        generator = DeviceNoteGenerator(
            hass,
            {
                "export_dir": "device_inventory_notes",
                "layout": LAYOUT_PROTOCOL,
                "merge_mode": MERGE_MODE_MERGE,
                "device_type_map": "esp32-c3: BLE-Proxy",
            },
        )

        self.assertEqual(generator.custom_device_type_map, {"esp32-c3": "BLE-Proxy"})

    def _generator(self, root: Path) -> DeviceNoteGenerator:
        generator = object.__new__(DeviceNoteGenerator)
        generator.merge_mode = MERGE_MODE_MERGE
        generator.fields = set(FIELD_ORDER) | set(HAND_FIELDS) | {"name", "ha_device_id"}
        generator.field_order = FIELD_ORDER
        generator.custom_type_map = {}
        generator.custom_device_type_map = {}
        generator.layout = LAYOUT_PROTOCOL
        generator.obsidian_base = "Aktoren"
        return generator

    def _device(
        self,
        device_id: str = "device-1",
        name: str = "Actor",
        config_entry_id: str | None = None,
        identifiers: tuple = (),
        model: str = "Model 1",
    ):
        return _SingleEntryDevice(
            id=device_id,
            name_by_user=None,
            name=name,
            default_name=None,
            manufacturer="ACME",
            model=model,
            via_device_id=None,
            config_entry_id=config_entry_id,
            identifiers=identifiers,
            area_id=None,
            sw_version=None,
            hw_version=None,
            serial_number=None,
            connections=(),
            configuration_url=None,
        )

    def _write(
        self,
        generator,
        root,
        device,
        name,
        domains,
        by_device_id=None,
        previous_state=None,
    ):
        with patch.object(generator_module, "datetime", FixedDateTime):
            return generator._write_note(
                generator_module.GenerateReport(),
                root,
                device,
                name,
                "Zigbee",
                None,
                domains,
                {},
                {},
                {},
                {},
                None,
                {},
                by_device_id or {},
                {},
                False,
                previous_state,
            )

    def _existing_note(self, generator, root, fields, body="Body"):
        path = root / "Zigbee" / "Actor.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        emit_empty = frozenset(key for key in HAND_FIELDS if key in generator.fields)
        path.write_text(
            serialize_note(fields, FIELD_ORDER, frozenset(emit_empty)) + "\n" + body + "\n",
            encoding="utf-8",
        )
        return path

    def _generated_fields(self, **overrides):
        fields = {
            "name": "Actor",
            "typ": "Lampe",
            "hersteller": "ACME",
            "modell": "Model 1",
            "protokoll": "Zigbee",
            "übersicht": "[[01-Übersicht Zigbee]]",
            "friendly_name": "Actor",
            "ha_device_id": "device-1",
            "entity_count": "1",
            "note_type": "actor",
        }
        fields.update(overrides)
        return fields

    def test_new_note_contains_actor_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            self._write(generator, root, self._device(), "Actor", {"light"})

            text = (root / "Zigbee" / "Actor.md").read_text(encoding="utf-8")
            fields, _body = parse_note(text)

            self.assertEqual(fields["note_type"], "actor")
            self.assertEqual(fields["entity_type"], "light")
            self.assertEqual(fields["updated"], "2026-09-25")
            frontmatter = text.split("---", 2)[1]
            self.assertEqual(yaml.safe_load(frontmatter)["note_type"], "actor")
            self.assertNotIn("status:", frontmatter)

    def test_corrected_typ_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            fields = self._generated_fields(typ="Benutzerkorrektur", updated="2026-09-20")
            self._existing_note(generator, root, fields)
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": root / "Zigbee" / "Actor.md"})

            updated, _body = parse_note((root / "Zigbee" / "Actor.md").read_text(encoding="utf-8"))

            self.assertEqual(updated["typ"], "Benutzerkorrektur")
            self.assertEqual(updated["updated"], "2026-09-20")

    def test_empty_typ_is_filled_on_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            fields = {
                "name": "Actor",
                "typ": "",
                "ha_device_id": "device-1",
                "updated": "2026-09-20",
                "note_type": "actor",
            }
            self._existing_note(generator, root, fields)
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": root / "Zigbee" / "Actor.md"})

            updated, _body = parse_note((root / "Zigbee" / "Actor.md").read_text(encoding="utf-8"))

            self.assertEqual(updated["typ"], "Lampe")
            self.assertEqual(updated["updated"], "2026-09-25")

    def test_missing_typ_is_filled_on_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            fields = self._generated_fields(updated="2026-09-20", entity_type="light")
            del fields["typ"]
            self._existing_note(generator, root, fields)
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": root / "Zigbee" / "Actor.md"})

            updated, _body = parse_note((root / "Zigbee" / "Actor.md").read_text(encoding="utf-8"))

            self.assertEqual(updated["typ"], "Lampe")
            self.assertEqual(updated["updated"], "2026-09-25")

    def test_existing_technical_and_manual_fields_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            generator.fields.difference_update({"bereich", "firmware", "lagerort", "notiz"})
            generator.field_order = tuple(
                key for key in FIELD_ORDER if key in generator.fields
            )
            fields = {
                "name": "Actor",
                "typ": "Benutzerkorrektur",
                "ha_device_id": "device-1",
                "updated": "2026-09-20",
                "note_type": "actor",
                "bereich": "Wohnzimmer",
                "firmware": "manuell gepflegt",
                "lagerort": "Regal 2",
                "notiz": "Nicht überschreiben",
            }
            self._existing_note(generator, root, fields)
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": root / "Zigbee" / "Actor.md"})

            updated, _body = parse_note((root / "Zigbee" / "Actor.md").read_text(encoding="utf-8"))

            self.assertEqual(updated["bereich"], "Wohnzimmer")
            self.assertEqual(updated["firmware"], "manuell gepflegt")
            self.assertEqual(updated["lagerort"], "Regal 2")
            self.assertEqual(updated["notiz"], "Nicht überschreiben")

    def test_old_note_receives_note_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            fields = {"name": "Actor", "ha_device_id": "device-1", "updated": "2026-09-20"}
            self._existing_note(generator, root, fields)
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": root / "Zigbee" / "Actor.md"})

            updated, _body = parse_note((root / "Zigbee" / "Actor.md").read_text(encoding="utf-8"))

            self.assertEqual(updated["note_type"], "actor")
            self.assertEqual(updated["entity_type"], "light")

    def test_unchanged_content_keeps_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            fields = self._generated_fields(updated="2026-09-20", entity_type="light")
            self._existing_note(generator, root, fields)
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": root / "Zigbee" / "Actor.md"})

            updated, _body = parse_note((root / "Zigbee" / "Actor.md").read_text(encoding="utf-8"))

            self.assertEqual(updated["updated"], "2026-09-20")

    def test_actual_content_change_updates_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            fields = self._generated_fields(typ="", updated="2026-09-20", entity_type="light")
            self._existing_note(generator, root, fields)
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": root / "Zigbee" / "Actor.md"})

            updated, _body = parse_note((root / "Zigbee" / "Actor.md").read_text(encoding="utf-8"))

            self.assertEqual(updated["updated"], "2026-09-25")

    def test_body_change_updates_date_from_persisted_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            fields = self._generated_fields(updated="2026-09-20", entity_type="light")
            path = self._existing_note(generator, root, fields, body="Neuer Notiztext")
            relative_path = str(path.relative_to(root))

            self._write(
                generator,
                root,
                self._device(),
                "Actor",
                {"light"},
                {"device-1": path},
                {relative_path: {"body": "Alter Notiztext"}},
            )

            updated, body = parse_note(path.read_text(encoding="utf-8"))
            snapshot = generator._snapshot_notes(root)
            self.assertEqual(updated["updated"], "2026-09-25")
            self.assertEqual(body, "Neuer Notiztext")
            self.assertEqual(snapshot[relative_path]["body"], "Neuer Notiztext")

    def test_existing_individual_uebersicht_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            fields = self._generated_fields(updated="2026-09-20", entity_type="light")
            fields["übersicht"] = "[[Eigene Übersicht]]"
            path = self._existing_note(generator, root, fields)
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": path})

            updated, _body = parse_note(path.read_text(encoding="utf-8"))

            self.assertEqual(updated["übersicht"], "[[Eigene Übersicht]]")
            self.assertEqual(updated["updated"], "2026-09-20")

    def test_existing_status_is_preserved_in_legacy_note(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            fields = {
                "name": "Actor",
                "ha_device_id": "device-1",
                "status": "aktiv",
                "updated": "2026-09-20",
            }
            path = root / "Zigbee" / "Actor.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                serialize_note(fields, FIELD_ORDER + ("status",), frozenset())
                + "\nBody\n",
                encoding="utf-8",
            )
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": path})

            updated, _body = parse_note(path.read_text(encoding="utf-8"))

            self.assertEqual(updated["status"], "aktiv")

    def test_rename_keeps_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            old_path = root / "Zigbee" / "Actor.md"
            fields = self._generated_fields(updated="2026-09-20")
            self._existing_note(generator, root, fields)
            self._write(generator, root, self._device(), "Renamed", {"light"}, {"device-1": old_path})

            updated, _body = parse_note((root / "Zigbee" / "Renamed.md").read_text(encoding="utf-8"))

            self.assertEqual(updated["updated"], "2026-09-20")
            self.assertEqual(updated["name"], "Renamed")

    def test_ambiguous_classification_omits_entity_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            self._write(generator, root, self._device(), "Actor", {"light", "switch"})

            text = (root / "Zigbee" / "Actor.md").read_text(encoding="utf-8")

            self.assertNotIn("entity_type:", text)

    def test_note_uses_valid_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            self._write(generator, root, self._device(), "Actor", {"light"})

            text = (root / "Zigbee" / "Actor.md").read_text(encoding="utf-8")
            frontmatter = text.split("---", 2)[1]
            parsed = yaml.safe_load(frontmatter)

            self.assertIsInstance(parsed, dict)
            self.assertEqual(parsed["note_type"], "actor")
            self.assertEqual(parsed["entity_type"], "light")
            self.assertEqual(parsed["updated"], "2026-09-25")

    def test_body_preserves_outer_whitespace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            path = root / "Zigbee" / "Actor.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            fields = {"name": "Actor", "ha_device_id": "device-1", "updated": "2026-09-20"}
            frontmatter = serialize_note(fields, FIELD_ORDER, frozenset())
            body = "\nEigener Text\n\n- bleibt erhalten\n\n"
            path.write_text(frontmatter + "\n" + body, encoding="utf-8")
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": path})

            text = path.read_text(encoding="utf-8")

            self.assertEqual(text.split("---\n", 2)[2], body)

    def test_body_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            fields = {"name": "Actor", "ha_device_id": "device-1", "updated": "2026-09-20"}
            self._existing_note(generator, root, fields, body="Eigener Text\n\n- bleibt erhalten")
            self._write(generator, root, self._device(), "Actor", {"light"}, {"device-1": root / "Zigbee" / "Actor.md"})

            _fields, body = parse_note((root / "Zigbee" / "Actor.md").read_text(encoding="utf-8"))

            self.assertEqual(body, "Eigener Text\n\n- bleibt erhalten")

    def test_overviews_do_not_contain_device_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            generator._write_overviews(root)

            for path in root.rglob("*.md"):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("note_type:", text)
                self.assertNotIn("entity_type:", text)
                self.assertNotIn("updated:", text)
                self.assertNotIn("status:", text)

    def test_resolve_protocol_uses_the_config_entry_id(self):
        device = self._device(config_entry_id="entry-zha")

        protocol, ieee = generator_module.DeviceNoteGenerator._resolve_protocol(
            device, {"entry-zha": "zha"}
        )

        self.assertEqual(protocol, "Zigbee")
        self.assertIsNone(ieee)

    def test_resolve_protocol_ignores_a_device_without_config_entry(self):
        device = self._device(config_entry_id=None, identifiers=(("zha", "00:11:22:33:44:55:66:77"),))

        protocol, ieee = generator_module.DeviceNoteGenerator._resolve_protocol(
            device, {"entry-zha": "zha"}
        )

        self.assertEqual(protocol, "Zigbee")
        self.assertEqual(ieee, "00:11:22:33:44:55:66:77")

    def test_resolve_protocol_skips_tracked_fritz_clients(self):
        tracked = self._device(
            config_entry_id="entry-fritz",
            model="FRITZ!Box Tracked device",
            identifiers=(("fritz", "tracked-client"),),
        )
        product = self._device(
            device_id="device-2",
            config_entry_id="entry-fritz",
            model="FRITZ!DECT 300",
        )
        entry_domain = {"entry-fritz": "fritz"}

        self.assertEqual(
            generator_module.DeviceNoteGenerator._resolve_protocol(tracked, entry_domain),
            (None, None),
        )
        self.assertEqual(
            generator_module.DeviceNoteGenerator._resolve_protocol(product, entry_domain),
            ("DECT", None),
        )

    def test_computed_fields_read_the_integration_from_the_config_entry_id(self):
        generator = self._generator(Path("/config"))
        device = self._device(config_entry_id="entry-z2m")

        computed = generator._computed_fields(
            device,
            "Actor",
            "Zigbee",
            None,
            {"light"},
            {},
            {},
            {},
            {"entry-z2m": "Zigbee2MQTT"},
            None,
        )

        self.assertEqual(computed["Integration"], "Zigbee2MQTT")

    def test_computed_fields_omit_integration_without_config_entry(self):
        generator = self._generator(Path("/config"))
        device = self._device(config_entry_id=None)

        computed = generator._computed_fields(
            device, "Actor", "Zigbee", None, {"light"}, {}, {}, {}, {}, None
        )

        self.assertNotIn("Integration", computed)


class DeprecatedApiTests(unittest.TestCase):
    """No code path may read DeviceEntry.config_entries any more.

    Every device stub in this suite is a _SingleEntryDevice, so this test is
    the belt to _device()'s braces: it exercises the paths that the note
    writer and the protocol resolver share.
    """

    def test_write_note_never_reads_config_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = DeviceNoteGeneratorTests()
            generator = helper._generator(root)
            device = helper._device(config_entry_id="entry-zha")
            helper._write(generator, root, device, "Actor", {"light"})
            generator._computed_fields(
                device, "Actor", "Zigbee", None, {"light"}, {}, {}, {}, {}, None
            )

    def test_resolve_protocol_never_reads_config_entries(self):
        device = DeviceNoteGeneratorTests()._device(config_entry_id="entry-zha")

        generator_module.DeviceNoteGenerator._resolve_protocol(device, {})


class MirrorTests(unittest.TestCase):
    """_mirror_to_www must never delete the tree it mirrors from."""

    def _generator(self, config_dir: Path):
        generator = object.__new__(DeviceNoteGenerator)
        generator.hass = SimpleNamespace(
            config=SimpleNamespace(config_dir=str(config_dir))
        )
        return generator

    def _seed(self, root: Path) -> Path:
        note = root / "Zigbee" / "Actor.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text('---\nname: "Actor"\n---\n', encoding="utf-8")
        return note

    def test_mirror_refuses_to_delete_the_export_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            root = config_dir / "www" / "device_inventory_notes"
            note = self._seed(root)
            report = generator_module.GenerateReport(created=[str(note)])

            self._generator(config_dir)._mirror_to_www(root, report)

            self.assertTrue(note.exists(), "Export-Verzeichnis wurde gelöscht")
            self.assertTrue(report.errors)

    def test_mirror_refuses_to_delete_a_nested_export_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            root = config_dir / "www" / "device_inventory_notes" / "export"
            note = self._seed(root)
            report = generator_module.GenerateReport(created=[str(note)])

            self._generator(config_dir)._mirror_to_www(root, report)

            self.assertTrue(note.exists(), "Export-Verzeichnis wurde gelöscht")
            self.assertTrue(report.errors)

    def test_mirror_copies_notes_and_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            root = config_dir / "export"
            note = self._seed(root)
            report = generator_module.GenerateReport(created=[str(note)])

            zip_url, dir_url = self._generator(config_dir)._mirror_to_www(root, report)

            self.assertEqual(zip_url, "/local/device_inventory_notes.zip")
            self.assertEqual(dir_url, "/local/device_inventory_notes/")
            mirrored = config_dir / "www" / "device_inventory_notes" / "Zigbee" / "Actor.md"
            self.assertTrue(mirrored.exists())
            self.assertEqual(report.errors, [])


class MirrorConflictTests(unittest.TestCase):
    """mirror_conflicts_with_export is the check shared by generator and flow."""

    def test_identical_paths_conflict(self):
        self.assertTrue(
            generator_module.mirror_conflicts_with_export(
                "/config", Path("/config/www/device_inventory_notes")
            )
        )

    def test_nested_path_conflicts(self):
        self.assertTrue(
            generator_module.mirror_conflicts_with_export(
                "/config", Path("/config/www/device_inventory_notes/export")
            )
        )

    def test_sibling_path_does_not_conflict(self):
        self.assertFalse(
            generator_module.mirror_conflicts_with_export(
                "/config", Path("/config/www/device_inventory_notes_export")
            )
        )

    def test_external_path_does_not_conflict(self):
        self.assertFalse(
            generator_module.mirror_conflicts_with_export(
                "/config", Path("/share/device_inventory_notes")
            )
        )

    def test_relative_export_dir_is_resolved_against_the_config_dir(self):
        self.assertFalse(
            generator_module.mirror_conflicts_with_export(
                "/config", Path("device_inventory_notes")
            )
        )
        self.assertTrue(
            generator_module.mirror_conflicts_with_export(
                "/config", Path("www/device_inventory_notes")
            )
        )


class FieldOrderTests(unittest.TestCase):
    """The order prefill must survive reopening the options flow."""

    DEFAULTS = [
        "Typ",
        "Hersteller",
        "Modell",
        "Protokoll",
        "Übersicht",
        "Friendly Name",
        "IEEE Address",
    ]
    STORED = [
        "notiz",
        "typ",
        "hersteller",
        "modell",
        "protokoll",
        "übersicht",
        "friendly_name",
        "ieee_address",
    ]

    def _current(self, fields=None, order=None):
        return {
            CONF_FIELDS: self.DEFAULTS + ["Notiz"] if fields is None else fields,
            CONF_FIELD_ORDER: self.STORED if order is None else order,
        }

    def test_prefill_starts_from_the_stored_order(self):
        current = self._current(order=list(reversed(self.STORED)))

        prefill = generator_module.order_prefill(current).splitlines()

        self.assertEqual(
            prefill,
            ["IEEE Address", "Friendly Name", "Übersicht", "Protokoll",
             "Modell", "Hersteller", "Typ", "Notiz"],
        )

    def test_prefill_appends_newly_selected_fields_in_default_order(self):
        current = self._current(fields=self.DEFAULTS + ["Notiz", "Bereich"])

        prefill = generator_module.order_prefill(current).splitlines()

        self.assertEqual(prefill[0], "Notiz")
        self.assertEqual(prefill[-1], "Bereich")

    def test_prefill_keeps_hand_added_fields(self):
        current = self._current(order=["notiz", "sonderfeld"] + self.STORED)

        prefill = generator_module.order_prefill(current).splitlines()

        self.assertIn("sonderfeld", prefill)

    def test_prefill_drops_known_fields_that_are_no_longer_selected(self):
        current = self._current(order=["notiz", "kaufdatum"] + self.STORED)

        prefill = generator_module.order_prefill(current).splitlines()

        self.assertNotIn("Kaufdatum", prefill)
        self.assertEqual(prefill[0], "Notiz")

    def test_prefill_without_a_stored_order_uses_the_default_order(self):
        current = self._current(order=[])

        prefill = generator_module.order_prefill(current).splitlines()

        self.assertEqual(prefill, self.DEFAULTS + ["Notiz"])

    def test_normalize_resolves_labels_and_dedupes(self):
        keys, unknown = generator_module.normalize_order("Typ\nhersteller\nTyp\n")

        self.assertEqual(keys, ["typ", "hersteller"])
        self.assertEqual(unknown, [])

    def test_normalize_reports_unknown_lines_instead_of_dropping_them(self):
        keys, unknown = generator_module.normalize_order("Typ\nSonderfeld\nhersteller")

        self.assertEqual(keys, ["typ", "Sonderfeld", "hersteller"])
        self.assertEqual(unknown, ["Sonderfeld"])

    def test_normalize_ignores_empty_lines(self):
        keys, unknown = generator_module.normalize_order("\n  \nTyp\n")

        self.assertEqual(keys, ["typ"])
        self.assertEqual(unknown, [])


class FieldSelectionTests(unittest.TestCase):
    """The fields selector must stay saveable when options hold a stale value."""

    def test_options_start_with_every_selectable_field(self):
        options = generator_module.selectable_field_options([])

        self.assertEqual(options[: len(SELECTABLE_FIELDS)], list(SELECTABLE_FIELDS))

    def test_options_keep_stored_values_that_are_not_selectable(self):
        options = generator_module.selectable_field_options(["Typ", "sonderfeld"])

        self.assertIn("sonderfeld", options)
        self.assertEqual(len(options), len(SELECTABLE_FIELDS) + 1)
        self.assertEqual(len(set(options)), len(options))


if __name__ == "__main__":
    unittest.main()

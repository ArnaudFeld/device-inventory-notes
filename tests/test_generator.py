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
parse_note = generator_module.parse_note
serialize_note = generator_module.serialize_note


class FixedDateTime:
    @classmethod
    def now(cls):
        return DateTime(2026, 9, 25, 12, 0, 0)


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

    def _device(self, device_id: str = "device-1", name: str = "Actor"):
        return SimpleNamespace(
            id=device_id,
            name_by_user=None,
            name=name,
            default_name=None,
            manufacturer="ACME",
            model="Model 1",
            via_device_id=None,
            config_entries=set(),
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


if __name__ == "__main__":
    unittest.main()

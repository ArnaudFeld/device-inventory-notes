import hashlib
import importlib
import sys
import tempfile
import types
import zipfile
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
OVERVIEW_ROOT_FILENAME = const_module.OVERVIEW_ROOT_FILENAME
LEGACY_INDEX_FILENAME = const_module.LEGACY_INDEX_FILENAME
MIRROR_DIRNAME = generator_module.MIRROR_DIRNAME
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
            index = generator._write_overviews(root)

            self.assertTrue(index.exists(), "Übersicht wurde nicht geschrieben")
            self.assertEqual(index.name, OVERVIEW_ROOT_FILENAME)
            for path in root.rglob("*.md"):
                text = path.read_text(encoding="utf-8")
                # note_type is deliberate here: the pages are collections.
                self.assertNotIn("entity_type:", text)
                self.assertNotIn("updated:", text)
                self.assertNotIn("status:", text)

    def test_overviews_are_written_for_every_protocol_even_when_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)

            generator._write_overviews(root)

            expected = {
                root / OVERVIEW_ROOT_FILENAME,
                *(
                    root / proto / f"01-Übersicht {proto}.md"
                    for proto in const_module.OVERVIEW_PROTOCOLS
                ),
            }
            self.assertEqual(set(root.rglob("*.md")), expected)
            for path in expected:
                self.assertIn("```dataview", path.read_text(encoding="utf-8"))

    def test_overviews_are_marked_as_collections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)

            generator._write_overviews(root)

            pages = [root / OVERVIEW_ROOT_FILENAME] + [
                root / proto / f"01-Übersicht {proto}.md"
                for proto in const_module.OVERVIEW_PROTOCOLS
            ]
            self.assertEqual(len(pages), 7)
            for path in pages:
                frontmatter = path.read_text(encoding="utf-8").split("---")[1]
                self.assertEqual(
                    frontmatter.strip().splitlines()[0],
                    "note_type: collection",
                    f"{path.name} startet nicht mit note_type: collection",
                )
                self.assertIn("note_type: collection", frontmatter)
                self.assertIn("aliases: []", frontmatter)

    def test_note_type_comes_before_aliases_in_the_overviews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)

            generator._write_overviews(root)

            for path in root.rglob("*.md"):
                frontmatter = path.read_text(encoding="utf-8").split("---")[1]
                self.assertLess(
                    frontmatter.index("note_type:"),
                    frontmatter.index("aliases:"),
                    f"{path.name}: note_type steht nicht vor aliases",
                )

    def test_typ_falls_back_to_the_protocol_without_a_device_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            # Neither _match_device_type nor _infer_type_custom can help:
            # no override lines, no entity domains at all.
            self.assertEqual(generator.custom_device_type_map, {})
            self._write(generator, root, self._device(), "Actor", set())

            fields, _body = parse_note(
                (root / "Zigbee" / "Actor.md").read_text(encoding="utf-8")
            )

            self.assertEqual(fields["typ"], "Zigbee")

    def test_derivable_device_kind_survives_the_protocol_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self._generator(root)
            self._write(generator, root, self._device(), "Actor", {"light"})

            fields, _body = parse_note(
                (root / "Zigbee" / "Actor.md").read_text(encoding="utf-8")
            )

            self.assertEqual(fields["typ"], "Lampe")
            self.assertNotEqual(fields["typ"], "Zigbee")

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

            with self.assertLogs(generator_module._LOGGER, level="ERROR"):
                self._generator(config_dir)._mirror_to_www(root, report)

            self.assertTrue(note.exists(), "Export-Verzeichnis wurde gelöscht")
            self.assertTrue(report.errors)

    def test_mirror_refuses_to_delete_a_nested_export_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            root = config_dir / "www" / "device_inventory_notes" / "export"
            note = self._seed(root)
            report = generator_module.GenerateReport(created=[str(note)])

            with self.assertLogs(generator_module._LOGGER, level="ERROR"):
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

    def test_zip_and_folder_contain_the_same_files(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            root = config_dir / "export"
            self._seed(root)
            legacy = root / LEGACY_INDEX_FILENAME
            legacy.write_text("alter Index", encoding="utf-8")
            report = generator_module.GenerateReport(created=[str(legacy)])

            self._generator(config_dir)._mirror_to_www(root, report)

            mirrored = config_dir / "www" / MIRROR_DIRNAME
            in_folder = {
                str(path.relative_to(mirrored)) for path in mirrored.rglob("*.md")
            }
            with zipfile.ZipFile(
                config_dir / "www" / f"{MIRROR_DIRNAME}.zip"
            ) as archive:
                in_zip = set(archive.namelist())
            self.assertEqual(in_zip, in_folder)
            self.assertNotIn(LEGACY_INDEX_FILENAME, in_zip)


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


class OrphanTests(unittest.TestCase):
    """A note is only deleted when neither its device id nor its ieee is known."""

    def _orphan(self, notes, known_ids=frozenset(), known_ieees=frozenset()):
        return generator_module.DeviceNoteGenerator._orphan_paths(
            notes, set(known_ids), set(known_ieees)
        )

    def test_note_of_a_known_device_is_kept(self):
        notes = {"Zigbee/A.md": {"device_id": "d1", "ieee": ""}}

        self.assertEqual(self._orphan(notes, {"d1"}), [])

    def test_note_without_any_identity_is_kept(self):
        notes = {"Notizen/hand.md": {"device_id": "", "ieee": ""}}

        self.assertEqual(self._orphan(notes, {"d1"}), [])

    def test_note_with_unknown_device_id_and_known_ieee_is_kept(self):
        notes = {"Zigbee/A.md": {"device_id": "old-id", "ieee": "00:11"}}

        self.assertEqual(self._orphan(notes, {"d1"}, {"00:11"}), [])

    def test_note_with_only_a_known_ieee_is_kept(self):
        notes = {"Zigbee/A.md": {"device_id": "", "ieee": "00:11"}}

        self.assertEqual(self._orphan(notes, {"d1"}, {"00:11"}), [])

    def test_note_with_unknown_device_id_and_unknown_ieee_is_deleted(self):
        notes = {"Zigbee/A.md": {"device_id": "gone", "ieee": "00:11"}}

        self.assertEqual(self._orphan(notes, {"d1"}, {"00:22"}), ["Zigbee/A.md"])

    def test_note_with_unknown_ieee_only_is_deleted(self):
        notes = {"Zigbee/A.md": {"device_id": "", "ieee": "00:11"}}

        self.assertEqual(self._orphan(notes, {"d1"}, {"00:22"}), ["Zigbee/A.md"])

    def test_delete_does_not_report_an_already_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = generator_module.GenerateReport()
            notes = {"Zigbee/A.md": {"device_id": "gone", "ieee": ""}}

            deleted = generator_module.DeviceNoteGenerator._delete_orphans(
                object.__new__(DeviceNoteGenerator), root, report, notes, set(), set()
            )

            self.assertEqual(deleted, [])
            self.assertEqual(report.errors, [])

    def test_delete_removes_a_real_orphan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            note = root / "Zigbee" / "A.md"
            note.parent.mkdir(parents=True)
            note.write_text("---\nname: \"A\"\n---\n", encoding="utf-8")
            report = generator_module.GenerateReport()
            notes = {"Zigbee/A.md": {"device_id": "gone", "ieee": ""}}

            deleted = generator_module.DeviceNoteGenerator._delete_orphans(
                object.__new__(DeviceNoteGenerator), root, report, notes, set(), set()
            )

            self.assertFalse(note.exists())
            self.assertEqual(deleted, [str(note)])
            self.assertEqual(report.removed_stale, [str(note)])

    def test_renames_move_the_index_entry_with_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            notes = {"Zigbee/Old.md": {"device_id": "d1", "ieee": "00:11"}}
            renamed = [
                (str(root / "Zigbee" / "Old.md"), str(root / "Zigbee" / "New.md"))
            ]

            generator_module.reindex_renamed_notes(notes, renamed, root)

            self.assertEqual(
                notes, {"Zigbee/New.md": {"device_id": "d1", "ieee": "00:11"}}
            )

    def test_renames_keep_a_stale_index_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            notes = {"Zigbee/Old.md": {"device_id": "d1", "ieee": ""}}
            renamed = [
                (str(root / "Zigbee" / "Old.md"), str(root / "Zigbee" / "New.md"))
            ]

            generator_module.reindex_renamed_notes(notes, renamed, root)

            self.assertNotIn("Zigbee/Old.md", notes)


class ChangelogTests(unittest.TestCase):
    """The snapshot diff and the changelog it feeds."""

    def _generator(self):
        return object.__new__(DeviceNoteGenerator)

    def _note(self, root: Path, rel: str, body: str = "") -> Path:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\nname: "Actor"\n---\n{body}', encoding="utf-8")
        return path

    def _state(self, path: Path, name: str, body: str, ieee: str = "") -> Path:
        self._note(path, name, body)
        return {
            name: {
                "device_id": "d1",
                "ieee": ieee,
                "hash": hashlib.sha256(
                    f'---\nname: "Actor"\n---\n{body}'.encode()
                ).hexdigest(),
                "body": body,
            }
        }

    def test_diff_reports_created_updated_and_removed(self):
        old = {"a.md": {"device_id": "d1", "ieee": "", "hash": "1"},
               "b.md": {"device_id": "d2", "ieee": "", "hash": "2"}}
        new = {"a.md": {"device_id": "d1", "ieee": "", "hash": "9"},
               "c.md": {"device_id": "d3", "ieee": "", "hash": "3"}}

        created, updated, renamed, removed = generator_module.DeviceNoteGenerator._diff_states(old, new)

        self.assertEqual(created, ["c.md"])
        self.assertEqual(updated, ["a.md"])
        self.assertEqual(renamed, [])
        self.assertEqual(removed, ["b.md"])

    def test_diff_detects_a_rename_by_device_id(self):
        old = {"Zigbee/Old.md": {"device_id": "d1", "ieee": "", "hash": "1"}}
        new = {"Zigbee/New.md": {"device_id": "d1", "ieee": "", "hash": "1"}}

        created, updated, renamed, removed = generator_module.DeviceNoteGenerator._diff_states(old, new)

        self.assertEqual(renamed, [("Zigbee/Old.md", "Zigbee/New.md")])
        self.assertEqual((created, updated, removed), ([], [], []))

    def test_diff_detects_a_rename_by_ieee_when_the_device_id_changed(self):
        old = {"Zigbee/Old.md": {"device_id": "", "ieee": "00:11", "hash": "1"}}
        new = {"Zigbee/New.md": {"device_id": "", "ieee": "00:11", "hash": "1"}}

        _created, _updated, renamed, _removed = generator_module.DeviceNoteGenerator._diff_states(old, new)

        self.assertEqual(renamed, [("Zigbee/Old.md", "Zigbee/New.md")])

    def test_a_note_without_identity_is_not_reported_as_changed(self):
        entry = {"device_id": "", "ieee": "", "hash": "1"}
        old = {"hand.md": dict(entry)}
        new = {"hand.md": dict(entry)}

        result = generator_module.DeviceNoteGenerator._diff_states(old, new)

        self.assertEqual(result, ([], [], [], []))

    def test_first_run_writes_a_baseline_without_a_changelog_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._note(root, "Zigbee/A.md")
            report = generator_module.GenerateReport()

            self._generator()._write_changelog(root, report)

            self.assertTrue((root / const_module.CHANGELOG_FILENAME).exists() is False)
            self.assertEqual(
                list(generator_module.DeviceNoteGenerator._load_last_state(
                    self._generator(), root
                )),
                ["Zigbee/A.md"],
            )
            self.assertEqual(report.changed_created, [])

    def test_a_changed_note_is_reported_in_the_changelog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._generator()._save_last_state(
                root, self._state(root, "Zigbee/A.md", "alt")
            )
            self._note(root, "Zigbee/A.md", "neu")
            report = generator_module.GenerateReport()

            self._generator()._write_changelog(root, report)

            self.assertEqual(report.changed_updated, ["Zigbee/A.md"])
            self.assertIn("Geändert (1)", (root / const_module.CHANGELOG_FILENAME).read_text(encoding="utf-8"))

    def test_an_unchanged_tree_writes_no_changelog_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = "gleich"
            self._generator()._save_last_state(
                root, self._state(root, "Zigbee/A.md", body)
            )
            report = generator_module.GenerateReport()

            self._generator()._write_changelog(root, report)

            self.assertFalse((root / const_module.CHANGELOG_FILENAME).exists())
            self.assertEqual(report.changed_updated, [])

    def test_overviews_and_the_changelog_itself_are_not_snapshotted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._note(root, "Zigbee/A.md")
            self._note(root, const_module.OVERVIEW_ROOT_FILENAME)
            self._note(root, const_module.CHANGELOG_FILENAME)

            snapshot = self._generator()._snapshot_notes(root)

            self.assertEqual(list(snapshot), ["Zigbee/A.md"])


class WriteSafetyTests(unittest.TestCase):
    """Notes are the user's own files. A half-finished write must not survive.

    The generator rewrites every note on each run, so a process that dies
    mid-write would leave truncated notes behind. Writing through a temporary
    file in the same directory and then replacing keeps the old content until
    the new content is complete on disk.
    """

    def test_a_failed_write_leaves_the_previous_note_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Actor.md"
            target.write_text("OLD", encoding="utf-8")

            with patch.object(Path, "write_text", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    generator_module._write_text_atomic(target, "NEW")

            self.assertEqual(target.read_text(encoding="utf-8"), "OLD")

    def test_no_temporary_file_is_left_behind_after_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Actor.md"
            target.write_text("OLD", encoding="utf-8")

            with patch.object(Path, "write_text", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    generator_module._write_text_atomic(target, "NEW")

            self.assertEqual([p.name for p in Path(directory).iterdir()], ["Actor.md"])

    def test_a_successful_write_replaces_the_content(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Actor.md"
            generator_module._write_text_atomic(target, "NEW")
            self.assertEqual(target.read_text(encoding="utf-8"), "NEW")
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["Actor.md"])

    def test_a_failed_write_during_a_rename_keeps_the_old_note(self):
        """The rename must not happen first.

        Moving the note and then writing it leaves the new path empty when the
        process dies in between, and the old path is already gone.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = DeviceNoteGeneratorTests()
            generator = helper._generator(root)
            old = root / "Zigbee" / "Old.md"
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_text('---\nname: "Old"\nnotiz: "Handarbeit"\n---\n', encoding="utf-8")
            device = helper._device(config_entry_id="entry-zha", name="New")

            with patch.object(
                generator_module, "_write_text_atomic", side_effect=OSError("boom")
            ):
                with self.assertRaises(OSError):
                    helper._write(
                        generator, root, device, "New", {"light"},
                        by_device_id={device.id: old},
                    )

            self.assertTrue(old.exists(), "alte Notiz wurde vor dem Schreiben entfernt")
            self.assertIn("Handarbeit", old.read_text(encoding="utf-8"))
            self.assertFalse((root / "Zigbee" / "New.md").exists())

    def test_a_successful_rename_moves_the_content_to_the_new_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = DeviceNoteGeneratorTests()
            generator = helper._generator(root)
            old = root / "Zigbee" / "Old.md"
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_text('---\nname: "Old"\nnotiz: "Handarbeit"\n---\n', encoding="utf-8")
            device = helper._device(config_entry_id="entry-zha", name="New")

            helper._write(
                generator, root, device, "New", {"light"},
                by_device_id={device.id: old},
            )

            new = root / "Zigbee" / "New.md"
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            self.assertIn("Handarbeit", new.read_text(encoding="utf-8"))


class NonScalarFrontmatterTests(unittest.TestCase):
    """A hand-written YAML list must not turn into a Python repr.

    str() on a list produces "['a', 'b']", which was written back into the
    user's note. The value has to keep its YAML spelling.
    """

    def test_a_list_stays_yaml_flow_style(self):
        fields, _ = generator_module.parse_note(
            '---\naliases: [Lampe, Wohnzimmer]\n---\n'
        )
        self.assertEqual(fields["aliases"], "[Lampe, Wohnzimmer]")
        self.assertNotIn("'", fields["aliases"])

    def test_a_mapping_stays_yaml_flow_style(self):
        fields, _ = generator_module.parse_note('---\nzuordnung: {a: 1}\n---\n')
        self.assertEqual(fields["zuordnung"], "{a: 1}")

    def test_booleans_keep_their_yaml_spelling(self):
        fields, _ = generator_module.parse_note(
            '---\nkaufdatum: true\ngarantie_bis: false\n---\n'
        )
        self.assertEqual(fields["kaufdatum"], "true")
        self.assertEqual(fields["garantie_bis"], "false")

    def test_numbers_and_strings_are_untouched(self):
        fields, _ = generator_module.parse_note(
            '---\nmenge: 3\npreis: 12.5\nnotiz: "Text"\n---\n'
        )
        self.assertEqual(fields["menge"], "3")
        self.assertEqual(fields["preis"], "12.5")
        self.assertEqual(fields["notiz"], "Text")

    def test_a_hand_written_list_survives_a_run_unchanged(self):
        """End to end: the note on disk keeps the value the user typed."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = DeviceNoteGeneratorTests()
            generator = helper._generator(root)
            device = helper._device(config_entry_id="entry-zha")

            helper._write(generator, root, device, "Actor", {"light"})
            note = root / "Zigbee" / "Actor.md"
            note.write_text(
                note.read_text(encoding="utf-8").replace(
                    'notiz: ""', 'notiz: ""\naliases: [Lampe, Wohnzimmer]'
                ),
                encoding="utf-8",
            )

            helper._write(generator, root, device, "Actor", {"light"})

            second = note.read_text(encoding="utf-8")
            self.assertIn('[Lampe, Wohnzimmer]', second)
            self.assertNotIn("'[Lampe", second)
            third = note.read_text(encoding="utf-8")
            helper._write(generator, root, device, "Actor", {"light"})
            self.assertEqual(note.read_text(encoding="utf-8"), third)


if __name__ == "__main__":
    unittest.main()

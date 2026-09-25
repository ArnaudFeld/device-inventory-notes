import tempfile
import unittest
from pathlib import Path

import test_generator


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.helpers = test_generator.DeviceNoteGeneratorTests()

    def _write_existing(self, root, fields, domains):
        generator = self.helpers._generator(root)
        path = self.helpers._existing_note(generator, root, fields)
        self.helpers._write(
            generator,
            root,
            self.helpers._device(),
            "Actor",
            domains,
            {"device-1": path},
        )
        return generator_module_fields(path)

    def test_updated_actor_note_normalizes_note_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fields = self.helpers._generated_fields(
                note_type="device",
                entity_type="light",
                updated="2026-09-20",
            )
            updated = self._write_existing(root, fields, {"light"})

            self.assertEqual(updated["note_type"], "actor")
            self.assertEqual(updated["updated"], "2026-09-20")

    def test_existing_entity_type_follows_current_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fields = self.helpers._generated_fields(
                note_type="actor",
                entity_type="switch",
                updated="2026-09-20",
            )
            updated = self._write_existing(root, fields, {"light"})

            self.assertEqual(updated["entity_type"], "light")
            self.assertEqual(updated["updated"], "2026-09-20")

    def test_uncertain_classification_removes_existing_entity_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fields = self.helpers._generated_fields(
                note_type="actor",
                entity_type="light",
                updated="2026-09-20",
            )
            updated = self._write_existing(root, fields, {"light", "switch"})

            self.assertNotIn("entity_type", updated)
            self.assertEqual(updated["updated"], "2026-09-20")

    def test_new_note_without_entity_classification_omits_entity_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = self.helpers._generator(root)
            self.helpers._write(generator, root, self.helpers._device(), "Actor", set())

            updated, _body = test_generator.parse_note(
                (root / "Zigbee" / "Actor.md").read_text(encoding="utf-8")
            )

            self.assertNotIn("entity_type", updated)
            self.assertEqual(updated["updated"], "2026-09-25")

    def test_legacy_metadata_migration_does_not_create_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fields = self.helpers._generated_fields()
            del fields["note_type"]
            fields.pop("entity_type", None)
            updated = self._write_existing(root, fields, {"light"})

            self.assertEqual(updated["note_type"], "actor")
            self.assertEqual(updated["entity_type"], "light")
            self.assertNotIn("updated", updated)


def generator_module_fields(path):
    fields, _body = test_generator.parse_note(path.read_text(encoding="utf-8"))
    return fields


if __name__ == "__main__":
    unittest.main()

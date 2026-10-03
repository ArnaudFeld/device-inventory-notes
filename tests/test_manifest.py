"""Release gate for the packaging metadata.

These are the files Home Assistant and HACS read before they ever import a
module: manifest.json, hacs.json, services.yaml and the translations. A typo
there is invisible to the unit tests and only shows up in someone else's
frontend, so the rules are asserted here instead.

The checks encode what Home Assistant's own manifest documentation requires,
plus the two facts about this repository that are easy to break: the domain has
to match the directory name, and the declared minimum Home Assistant version
has to cover the manifest keys in use. Nothing in this module imports the
integration, so it runs on a bare interpreter.
"""

import json
import re
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).parents[1]
COMPONENT_DIR = REPO / "custom_components" / "device_inventory_notes"
MANIFEST_PATH = COMPONENT_DIR / "manifest.json"
HACS_PATH = REPO / "hacs.json"
SERVICES_PATH = COMPONENT_DIR / "services.yaml"

# From the Home Assistant integration manifest documentation.
INTEGRATION_TYPES = frozenset(
    {"entity", "device", "hardware", "helper", "hub", "service", "system", "virtual"}
)
IOT_CLASSES = frozenset(
    {
        "assumed_state",
        "calculated",
        "cloud_polling",
        "cloud_push",
        "local_polling",
        "local_push",
    }
)
REQUIRED_KEYS = (
    "domain",
    "name",
    "version",
    "documentation",
    "codeowners",
    "integration_type",
    "requirements",
)
# Accepted by AwesomeVersion; custom integrations must carry a version.
VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")
HA_MIN_VERSION_RE = re.compile(r"^\d{4}\.\d+(\.\d+)?$")


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _version_tuple(value):
    return tuple(int(part) for part in value.split("."))


manifest = _load_json(MANIFEST_PATH)
hacs = _load_json(HACS_PATH)


class ManifestTest(unittest.TestCase):
    def test_required_keys_are_present(self):
        for key in REQUIRED_KEYS:
            self.assertIn(key, manifest, f"manifest.json fehlt '{key}'")

    def test_domain_matches_the_directory_name(self):
        """Home Assistant keys the config flow off the domain; a mismatch
        means the flow is registered under a name nobody can reach."""
        self.assertEqual(manifest["domain"], COMPONENT_DIR.name)

    def test_domain_is_lowercase(self):
        self.assertEqual(manifest["domain"], manifest["domain"].lower())

    def test_version_is_present_and_valid(self):
        self.assertIn("version", manifest)
        self.assertRegex(manifest["version"], VERSION_RE)

    def test_integration_type_is_known(self):
        self.assertIn(manifest["integration_type"], INTEGRATION_TYPES)

    def test_iot_class_is_known(self):
        if "iot_class" in manifest:
            self.assertIn(manifest["iot_class"], IOT_CLASSES)

    def test_documentation_is_an_https_url(self):
        self.assertTrue(
            manifest["documentation"].startswith("https://"),
            "documentation muss eine https-URL sein",
        )

    def test_issue_tracker_is_an_https_url(self):
        self.assertTrue(
            manifest["issue_tracker"].startswith("https://"),
            "issue_tracker muss eine https-URL sein",
        )

    def test_documentation_and_issue_tracker_share_one_repository(self):
        """Two different repos in one manifest is almost always a leftover."""
        doc = manifest["documentation"].rstrip("/")
        tracker = manifest["issue_tracker"].rstrip("/").removesuffix("/issues")
        self.assertEqual(doc, tracker)

    def test_codeowners_are_github_handles(self):
        self.assertTrue(manifest["codeowners"], "codeowners darf nicht leer sein")
        for owner in manifest["codeowners"]:
            self.assertRegex(owner, r"^@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")

    def test_requirements_is_a_list(self):
        self.assertIsInstance(manifest["requirements"], list)

    def test_config_flow_flag_matches_the_file(self):
        self.assertIn("config_flow", manifest)
        exists = (COMPONENT_DIR / "config_flow.py").is_file()
        self.assertEqual(
            bool(manifest["config_flow"]),
            exists,
            "config_flow.py und das Manifest-Flag config_flow widersprechen sich",
        )

    def test_no_yaml_setup_block_leftovers(self):
        """A custom_components integration must not ask for YAML setup."""
        self.assertNotIn("dependencies_yaml", manifest)
        self.assertNotIn("after_dependencies_yaml", manifest)


class SingleConfigEntryVersionTest(unittest.TestCase):
    """single_config_entry landed in Home Assistant 2024.3.

    Claiming support for an older version than the manifest key needs produces
    an unknown-key warning on those releases, so the two must agree.
    """

    MINIMUM_HA_VERSION = "2024.3"

    def test_hacs_declares_a_version(self):
        self.assertIn("homeassistant", hacs)
        self.assertRegex(hacs["homeassistant"], HA_MIN_VERSION_RE)

    def test_declared_minimum_covers_the_manifest_keys(self):
        declared = _version_tuple(hacs["homeassistant"])
        required = _version_tuple(self.MINIMUM_HA_VERSION)
        self.assertGreaterEqual(
            declared[:2],
            required[:2],
            "hacs.json nennt eine zu alte Home-Assistant-Version fuer "
            "single_config_entry (ab 2024.3 verfuegbar)",
        )


class HacsTest(unittest.TestCase):
    def test_name_is_present(self):
        self.assertIn("name", hacs)
        self.assertTrue(hacs["name"].strip())

    def test_custom_components_layout_is_not_declared_as_content_in_root(self):
        """The component lives in custom_components/<domain>.

        zip_release or content_in_root would move the folder and HACS would
        install it where Home Assistant does not look for it.
        """
        self.assertFalse(hacs.get("zip_release", False))
        self.assertFalse(hacs.get("content_in_root", False))


class ServicesTest(unittest.TestCase):
    """services.yaml is what renders the action picker in the UI."""

    def setUp(self):
        self.services = yaml.safe_load(SERVICES_PATH.read_text(encoding="utf-8"))

    def test_scan_service_is_described(self):
        const = (COMPONENT_DIR / "const.py").read_text(encoding="utf-8")
        match = re.search(r'SERVICE_SCAN:\s*Final\s*=\s*"([^"]+)"', const)
        self.assertIsNotNone(match, "SERVICE_SCAN nicht in const.py gefunden")
        self.assertIn(match.group(1), self.services)

    def test_every_service_has_a_name_and_description(self):
        for service, schema in self.services.items():
            self.assertIn("name", schema, f"{service} hat keinen Namen")
            self.assertIn("description", schema, f"{service} hat keine Beschreibung")

    def test_documented_fields_exist_in_the_service_schema(self):
        """A field in services.yaml that the service ignores is a trap."""
        init = (COMPONENT_DIR / "__init__.py").read_text(encoding="utf-8")
        for service, schema in self.services.items():
            for field in schema.get("fields", {}):
                self.assertIn(
                    field,
                    init,
                    f"services.yaml nennt '{field}' fuer {service}, "
                    "der Code tut es nicht",
                )


class TranslationsTest(unittest.TestCase):
    def setUp(self):
        self.translations = {
            lang: _load_json(COMPONENT_DIR / "translations" / f"{lang}.json")
            for lang in ("de", "en")
        }

    def test_both_languages_exist(self):
        for lang in self.translations:
            self.assertTrue(
                (COMPONENT_DIR / "translations" / f"{lang}.json").is_file(), lang
            )

    def test_english_is_complete(self):
        """Without en.json the flow falls back to untranslated keys."""
        self.assertIn("config", self.translations["en"])

    def test_german_covers_every_english_key(self):
        """A missing German key shows the English string, not an error.

        Worth catching in review rather than in the UI.
        """
        def keys(mapping, prefix=""):
            found = set()
            for key, value in mapping.items():
                path = f"{prefix}{key}"
                found.add(path)
                if isinstance(value, dict):
                    found |= keys(value, f"{path}.")
            return found

        en = keys(self.translations["en"])
        de = keys(self.translations["de"])
        self.assertEqual(
            en - de,
            set(),
            f"fehlende deutsche Schluessel: {sorted(en - de)}",
        )


class ReadmeMatchesCodeTest(unittest.TestCase):
    """The READMEs name fields the generator writes.

    A stale README is invisible to the test suite and to Home Assistant. Both
    have already drifted once: the docs still claimed an empty type field after
    the fallback to the protocol name landed.
    """

    READMES = ("README.md", "README.en.md")

    def _const(self):
        return (COMPONENT_DIR / "const.py").read_text(encoding="utf-8")

    def test_hand_fields_are_documented(self):
        """Every never-overwritten field belongs in both READMEs."""
        hand = set(re.findall(r"^\s*HAND_FIELDS.*?\(([^)]*)\)", self._const(), re.M))
        declared = set()
        for group in hand:
            declared.update(re.findall(r'"([a-z_]+)"', group))
        self.assertTrue(declared, "HAND_FIELDS in const.py nicht gefunden")

        for name in self.READMES:
            text = (REPO / name).read_text(encoding="utf-8")
            for field in declared:
                with self.subTest(readme=name, field=field):
                    self.assertIn(
                        f"`{field}`",
                        text,
                        f"{name} nennt das Handfeld '{field}' nicht, "
                        "obwohl der Generator es nie überschreibt",
                    )

    def test_each_readme_links_to_the_other_language(self):
        """README.md must offer English and README.en.md must offer German.

        The switcher is a one-line link at the top, so each file has to name
        the other one, not itself.
        """
        for name, other in (("README.md", "README.en.md"),
                            ("README.en.md", "README.md")):
            text = (REPO / name).read_text(encoding="utf-8")
            with self.subTest(readme=name):
                self.assertIn(f"]({other})", text)

    def test_referenced_images_exist(self):
        """A README with a broken image link looks broken on the HACS page."""
        for name in self.READMES:
            text = (REPO / name).read_text(encoding="utf-8")
            for ref in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
                if ref.startswith(("http://", "https://")):
                    continue
                with self.subTest(readme=name, image=ref):
                    self.assertTrue((REPO / ref).is_file(), f"{ref} fehlt")

    def test_installation_names_the_repository(self):
        """Both READMEs have to carry the HACS repository slug."""
        for name in self.READMES:
            text = (REPO / name).read_text(encoding="utf-8")
            with self.subTest(readme=name):
                self.assertIn("ArnaudFeld/device-inventory-notes", text)


class BrandAssetsTest(unittest.TestCase):
    """Home Assistant shows these in the integration list."""

    def test_icons_and_logo_exist(self):
        for name in ("icon.png", "icon@2x.png", "logo.png", "logo@2x.png"):
            path = COMPONENT_DIR / "brand" / name
            self.assertTrue(path.is_file(), f"brand/{name} fehlt")
            self.assertGreater(path.stat().st_size, 0, f"brand/{name} ist leer")


if __name__ == "__main__":
    unittest.main()
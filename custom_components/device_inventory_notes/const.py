"""Constants for the Device Inventory Notes integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "device_inventory_notes"

SERVICE_SCAN: Final = "scan_and_generate"

CONF_EXPORT_DIR: Final = "export_dir"
CONF_LAYOUT: Final = "layout"
CONF_MERGE_MODE: Final = "merge_mode"
CONF_AUTO_UPDATE: Final = "auto_update"
CONF_IGNORED_DEVICES: Final = "ignored_devices"
CONF_FORCE_INCLUDE: Final = "force_include"
CONF_FIELDS: Final = "fields"
CONF_FIELD_ORDER: Final = "field_order"
CONF_OBSIDIAN_BASE: Final = "obsidian_base"
CONF_ONLY_AREAS: Final = "only_areas"
CONF_TYPE_MAP: Final = "type_map"

LAYOUT_PROTOCOL: Final = "protocol"
LAYOUT_AREA: Final = "area"

MERGE_MODE_MERGE: Final = "merge"
MERGE_MODE_CREATE_ONLY: Final = "create_only"

DEFAULT_EXPORT_DIR: Final = "device_inventory_notes"
DEFAULT_LAYOUT: Final = LAYOUT_PROTOCOL
DEFAULT_MERGE_MODE: Final = MERGE_MODE_MERGE
DEFAULT_AUTO_UPDATE: Final = True
# Obsidian vault path under which the protocol folders live; the dataview
# queries in the generated overview pages use it as their FROM target.
DEFAULT_OBSIDIAN_BASE: Final = "02 Home Assistant/Aktoren"

# Fields that HA can fill. Everything else in a note file is a "hand field"
# (lagerort, menge, kaufdatum, ...) and must NEVER be overwritten.
HA_FIELDS: Final = frozenset(
    {
        "name",
        "typ",
        "hersteller",
        "modell",
        "protokoll",
        "friendly_name",
        "ieee_address",
        "ha_device_id",
        "firmware",
        "hardware_revision",
        "seriennummer",
        "mac",
        "bereich",
        "etage",
        "verbunden_über",
        "Integration",
        "entity_count",
        "config_url",
    }
)

# Values HA could recompute, but the user may have corrected (e.g. typ on a
# device whose entities are ambiguous). Only filled when the target has no
# value yet.
GENERATED_ON_CREATE_FIELDS: Final = frozenset({"typ"})

# Hand-maintained fields. They are written as empty placeholders when
# selected, and are never overwritten when present in an existing note.
HAND_FIELDS: Final = frozenset(
    {
        "lagerort",
        "menge",
        "kaufdatum",
        "preis",
        "gekauft_bei",
        "garantie_bis",
        "notiz",
        "transport",
    }
)

# Display label (as shown in the config flow) -> internal frontmatter key.
# SELECTABLE_FIELDS and DEFAULT_FIELDS use the labels; the generator and the
# config flow resolve them through this map.
FIELD_LABEL_TO_KEY: Final[dict[str, str]] = {
    "Hersteller": "hersteller",
    "Modell": "modell",
    "Protokoll": "protokoll",
    "Typ": "typ",
    "Friendly Name": "friendly_name",
    "IEEE Address": "ieee_address",
    "Firmware": "firmware",
    "Hardware Revision": "hardware_revision",
    "Seriennummer": "seriennummer",
    "MAC": "mac",
    "Bereich": "bereich",
    "Etage": "etage",
    "Verbunden Über": "verbunden_über",
    "Integration": "Integration",
    "Entity Count": "entity_count",
    "Config URL": "config_url",
    "Lagerort": "lagerort",
    "Menge": "menge",
    "Kaufdatum": "kaufdatum",
    "Preis": "preis",
    "Gekauft Bei": "gekauft_bei",
    "Garantie Bis": "garantie_bis",
    "Notiz": "notiz",
    "Transport": "transport",
}

# Inverse mapping (internal key -> display label) for pre-filling the order step.
KEY_TO_LABEL: Final[dict[str, str]] = {v: k for k, v in FIELD_LABEL_TO_KEY.items()}

# Fields the user can select in the options flow (name and ha_device_id are
# mandatory and always written). Values match FIELD_LABEL_TO_KEY so that the
# defaults can be pre-selected in the flow.
SELECTABLE_FIELDS: Final = tuple(FIELD_LABEL_TO_KEY)

DEFAULT_FIELDS: Final = (
    "Hersteller",
    "Modell",
    "Protokoll",
    "Typ",
    "Friendly Name",
    "IEEE Address",
)

# Field order mirrors the existing note templates.
FIELD_ORDER: Final = (
    "typ",
    "hersteller",
    "modell",
    "protokoll",
    "transport",
    "friendly_name",
    "name",
    "ieee_address",
    "ha_device_id",
    "firmware",
    "hardware_revision",
    "seriennummer",
    "mac",
    "verbunden_über",
    "bereich",
    "etage",
    "Integration",
    "entity_count",
    "config_url",
    "lagerort",
    "menge",
    "kaufdatum",
    "preis",
    "gekauft_bei",
    "garantie_bis",
    "notiz",
)

# Overviews and metadata files living in the export root.
OVERVIEW_ROOT_FILENAME: Final = "01-Übersicht Aktoren.md"
OVERVIEW_FILENAME_TEMPLATE: Final = "01-Übersicht {proto}.md"
LEGACY_INDEX_FILENAME: Final = "00 - Geräte-Übersicht.md"

# Change-log between runs: persisted last-run snapshot + generated diff.
LAST_STATE_FILENAME: Final = ".din_last_state.json"
CHANGELOG_FILENAME: Final = "CHANGELOG.md"
CHANGELOG_MAX_ENTRIES: Final = 20

# Protocols that get an overview page, always (also for empty folders).
OVERVIEW_PROTOCOLS: Final = (
    "Bluetooth",
    "DECT",
    "HomematicIP",
    "Matter",
    "WiFi",
    "Zigbee",
)

# Emoji per protocol used for the Kategorien links in the root overview.
OVERVIEW_EMOJIS: Final[dict[str, str]] = {
    "Bluetooth": "🦷",
    "DECT": "☎️",
    "HomematicIP": "🏠",
    "Matter": "🔮",
    "WiFi": "📶",
    "Zigbee": "🐝",
}

# integration domain -> protokoll value used in notes
PROTOCOL_MAP: Final[dict[str, str]] = {
    "androidtv_remote": "WiFi",
    "apple_tv": "WiFi",
    "bluetooth": "Bluetooth",
    "brother": "WiFi",
    "bthome": "Bluetooth",
    "cast": "WiFi",
    "dreame_vacuum": "WiFi",
    "esphome": "WiFi",
    "fritz": "DECT",
    "fritzbox": "DECT",
    "govee_ble": "Bluetooth",
    "homematic": "Homematic",
    "homematicip_local": "HomematicIP",
    "ipp": "WiFi",
    "matter": "Matter",
    "mobile_app": "WiFi",
    "mopeka": "Bluetooth",
    "oralb": "Bluetooth",
    "sensirion_ble": "Bluetooth",
    "shelly": "WiFi",
    "soundtouchplus": "WiFi",
    "switchbot": "Bluetooth",
    "thermopro": "Bluetooth",
    "tuya": "WiFi",
    "twinkly": "WiFi",
    "xiaomi_ble": "Bluetooth",
    "zha": "Zigbee",
}

# Integrations that only expose service/bridge devices (their entities live
# elsewhere). Devices whose identifiers are all service domains are skipped.
SERVICE_DOMAINS: Final = frozenset(
    {
        "spook",
        "browser_mod",
        "ha_mcp_tools",
        "ha_washdata",
        "pi_hole_v6",
        "technitiumdns",
        "jellyha",
        "alarmo",
        "glances",
    }
)

# Entity domains (in priority order) -> typ suggestion for CREATE only.
TYPE_MAP: Final[dict[str, str]] = {
    "climate": "Klima",
    "cover": "Rolladen",
    "fan": "Lüfter",
    "light": "Lampe",
    "lock": "Schloss",
    "media_player": "Lautsprecher",
    "switch": "Steckdose",
}

DEFAULT_TYPE: Final = "Sensor"

# Extra protocol mapping configured by the user in the options flow.
CONF_EXTRA_MAP: Final = "extra_map"
CONF_EXTRA_DOMAINS: Final = "extra_domains"
CONF_EXTRA_REMOVE: Final = "extra_remove"
CONF_EXTRA_PROTOCOL: Final = "extra_protocol"

PROTOCOL_UNKNOWN: Final = "Unbekannt"

EXTRA_PROTOCOL_OPTIONS: Final = (
    "WiFi",
    "Bluetooth",
    "Zigbee",
    "DECT",
    "Matter",
    "Homematic",
    "HomematicIP",
    PROTOCOL_UNKNOWN,
)

# Initial inventory run after a HA restart/config entry setup (seconds),
# only when auto_update is enabled.
INITIAL_RUN_DELAY: Final = 30.0

DEBOUNCE_SECONDS: Final = 10.0

EVENT_DEVICE_REGISTRY_UPDATED: Final = "device_registry_updated"
EVENT_ENTITY_REGISTRY_UPDATED: Final = "entity_registry_updated"
EVENT_AREA_REGISTRY_UPDATED: Final = "area_registry_updated"
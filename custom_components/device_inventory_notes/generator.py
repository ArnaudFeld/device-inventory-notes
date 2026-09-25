"""Generate Obsidian-compatible device note files from the Home Assistant registries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as flr

from .const import (
    ACTOR_METADATA_FIELDS,
    CHANGELOG_FILENAME,
    CHANGELOG_MAX_ENTRIES,
    CONF_DEVICE_TYPE_MAP,
    CONF_EXPORT_DIR,
    CONF_EXTRA_MAP,
    CONF_FIELD_ORDER,
    CONF_FIELDS,
    CONF_FORCE_INCLUDE,
    CONF_IGNORED_DEVICES,
    CONF_LAYOUT,
    CONF_MERGE_MODE,
    CONF_OBSIDIAN_BASE,
    CONF_ONLY_AREAS,
    CONF_TYPE_MAP,
    DEFAULT_EXPORT_DIR,
    DEFAULT_FIELDS,
    DEFAULT_OBSIDIAN_BASE,
    FIELD_LABEL_TO_KEY,
    FIELD_ORDER,
    GENERATED_ON_CREATE_FIELDS,
    HAND_FIELDS,
    KEY_TO_LABEL,
    LAST_STATE_FILENAME,
    LAYOUT_AREA,
    LEGACY_INDEX_FILENAME,
    MERGE_MODE_CREATE_ONLY,
    OVERVIEW_EMOJIS,
    OVERVIEW_FILENAME_TEMPLATE,
    OVERVIEW_PROTOCOLS,
    OVERVIEW_ROOT_FILENAME,
    PROTOCOL_MAP,
    SELECTABLE_FIELDS,
    SERVICE_DOMAINS,
    TYPE_MAP,
)

_LOGGER = logging.getLogger(__name__)

_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|#%{}]+')
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")

ZIGBEE2MQTT_BRIDGE = "zigbee2mqtt_bridge"

MIRROR_DIRNAME = "device_inventory_notes"

# The FRITZ! integration creates one device per tracked network client with a
# generic model. Only genuine FRITZ!DECT/Fon products are DECT; tracked clients
# (WLAN/LAN) are not DECT and must be skipped.
_FRITZ_DECT_MODEL_MARKERS = ("dect", "fon")

# Fields that used to be HA-managed but no longer exist. Existing notes get
# these removed on the next update.
RETIRED_HA_FIELDS: frozenset[str] = frozenset()

# Matter integration unique_id markers for the transport diagnostics entities.
# These entities are disabled by the integration but live in the entity
# registry, so their unique_id reveals the node's transport.
_MATTER_TRANSPORT_MARKERS: tuple[tuple[str, str], ...] = (
    ("ThreadDiagnostics", "Thread"),
    ("WiFiDiagnostics", "WiFi"),
)

_NOTE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NOTE_RENAME_FIELDS = frozenset(
    {"name", "friendly_name", "übersicht", "updated", "note_type", "entity_type"}
)


def device_display_name(device) -> str:
    """Display name as the user sees it (name_by_user wins)."""
    name = device.name_by_user or device.name or ""
    if not name:
        name = device.default_name or ""
    return name.strip()


def safe_filename(name: str) -> str:
    """Sanitize a device name for use as a file name (keeps umlauts)."""
    cleaned = _CONTROL_CHARS.sub(" ", name)
    cleaned = _FILENAME_UNSAFE.sub("-", cleaned).strip(" .")
    return cleaned or "Gerät"


def _is_generated_overview(path: Path) -> bool:
    """True for the auto-generated dataview overview pages and the legacy index."""
    name = path.name
    if name == LEGACY_INDEX_FILENAME or name == OVERVIEW_ROOT_FILENAME:
        return True
    return name in {
        OVERVIEW_FILENAME_TEMPLATE.format(proto=proto) for proto in OVERVIEW_PROTOCOLS
    }


def _filter_values(raw: str) -> list[str]:
    return [
        line.strip().lower()
        for line in (raw or "").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def parse_type_map(raw: str) -> dict[str, str]:
    """Parse multiline "domain: label" lines into a mapping.

    Lines starting with '#' and empty lines are ignored. The domain is
    lower-cased; the label keeps its case. Later lines win for a repeated
    domain.
    """
    mapping: dict[str, str] = {}
    for line in (raw or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        domain, _, label = stripped.partition(":")
        domain = domain.strip().lower()
        label = label.strip()
        if domain and label:
            mapping[domain] = label
    return mapping


def selected_field_keys(fields) -> set[str]:
    """Internal frontmatter keys for a list of config-flow field labels.

    The default fields are always part of the selection, mirroring what the
    options form pre-selects, so the field order keeps offering them.
    """
    selected = {FIELD_LABEL_TO_KEY.get(field, field) for field in DEFAULT_FIELDS}
    selected |= {FIELD_LABEL_TO_KEY.get(field, field) for field in (fields or [])}
    return selected


def selectable_field_options(stored) -> list[str]:
    """Values for the fields selector: the selectable ones plus stale entries.

    A stored value that is not in SELECTABLE_FIELDS (a hand-added field, or a
    label from a version that no longer exists) has to stay in the option list,
    otherwise the selector rejects the whole submitted form and the options
    flow can no longer be saved at all.
    """
    extra = list(
        dict.fromkeys(
            field for field in (stored or []) if field not in SELECTABLE_FIELDS
        )
    )
    return [*SELECTABLE_FIELDS, *extra]


def order_prefill(current: dict) -> str:
    """Suggested field order for the options form, one label per line.

    Starts from the stored field_order so a custom order survives reopening
    the flow, then appends newly selected fields in FIELD_ORDER. Hand-added
    fields with no FIELD_LABEL_TO_KEY entry are kept, known fields that are no
    longer selected are dropped.
    """
    selected = selected_field_keys(current.get(CONF_FIELDS))
    known = set(FIELD_LABEL_TO_KEY.values())
    stored = current.get(CONF_FIELD_ORDER) or []
    ordered = list(
        dict.fromkeys(key for key in stored if key in selected or key not in known)
    )
    ordered += [key for key in FIELD_ORDER if key in selected and key not in ordered]
    ordered += sorted(key for key in selected if key not in ordered)
    return "\n".join(KEY_TO_LABEL.get(key, key) for key in ordered)


def normalize_order(value: str) -> tuple[list[str], list[str]]:
    """Split the order form text into (internal keys, unrecognised lines).

    Labels and raw keys are both accepted, and duplicates collapse. A line that
    matches no known field is kept as a key so a hand-added field survives, and
    its original text is returned separately so the flow can tell the user the
    line has no effect.
    """
    keys: list[str] = []
    unknown: list[str] = []
    known = set(FIELD_LABEL_TO_KEY.values())
    for line in (value or "").splitlines():
        entry = line.strip()
        if not entry:
            continue
        key = FIELD_LABEL_TO_KEY.get(entry, entry)
        if key not in keys:
            keys.append(key)
        if key not in known and entry not in unknown:
            unknown.append(entry)
    return keys, unknown


def matches_filter(raw: str, device, name: str) -> bool:
    """True if the raw multiline list contains the device id or a name fragment."""
    values = _filter_values(raw)
    if not values:
        return False
    haystack = name.lower()
    return any(
        device.id.lower() == value or value in haystack for value in values
    )


def parse_note(text: str) -> tuple[dict[str, str], str] | None:
    """Split a note into (frontmatter fields, body). None if no valid frontmatter."""
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    end = 1
    while end < len(lines) and lines[end].strip() != "---":
        end += 1
    if end >= len(lines):
        return None
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    fields = {str(k): str(v) for k, v in data.items() if v is not None}
    body = "\n".join(lines[end + 1 :])
    return fields, body


def _quote(value: str) -> str:
    value = _CONTROL_CHARS.sub(" ", value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def serialize_note(
    fields: dict[str, str], order: tuple[str, ...], emit_empty: frozenset[str] = frozenset()
) -> str:
    """Serialize frontmatter in the given field order, skipping empty values.

    Keys in emit_empty are written even when empty ("" placeholders for
    hand-maintained fields).
    """
    lines = ["---"]
    for key in order:
        value = fields.get(key)
        if not value and key not in emit_empty:
            continue
        lines.append(f"{key}: {_quote(str(value))}")
    lines.append("---")
    return "\n".join(lines)


def _first_connection(device, conn_type: str) -> str | None:
    """Return the first connection value of the given type, e.g. mac."""
    for ctype, value in device.connections:
        if ctype == conn_type and value:
            return value
    return None


def mirror_dir(config_dir: str | Path) -> Path:
    """Folder the www mirror is written to, and cleared before every rebuild."""
    return Path(config_dir) / "www" / MIRROR_DIRNAME


def mirror_conflicts_with_export(config_dir: str | Path, export_dir: str | Path) -> bool:
    """True when the www mirror would delete the tree it copies from.

    The mirror is rebuilt by removing <config>/www/device_inventory_notes
    first. An export directory that is that folder, or a subfolder of it,
    would be destroyed by its own mirror, so the caller has to skip the
    mirror instead. Relative export directories are resolved against the
    config dir, the same way DeviceNoteGenerator.export_root does.
    """
    root = Path(export_dir)
    if not root.is_absolute():
        root = Path(config_dir) / root
    www_resolved = mirror_dir(config_dir).resolve()
    root_resolved = root.resolve()
    return root_resolved == www_resolved or root_resolved.is_relative_to(www_resolved)


def _is_fritz_dect_device(dev, domain: str | None = None) -> bool:
    """True only for genuine FRITZ! DECT products.

    Two different FRITZ! integrations exist:
    - "fritzbox"  -> FRITZ! Smart Home (FRITZ!DECT, HAN-FUN, FRITZ!Fon):
      everything it exposes is a genuine DECT/RF device, regardless of model.
    - "fritz"     -> the router integration. It creates a device for every
      tracked network client (model "FRITZ!Box Tracked device") and for the
      router/repeaters themselves. None of those are DECT; only actual DECT
      product models count (models containing "dect"/"fon").
    """
    if domain == "fritzbox":
        return True
    model = (dev.model or "").lower()
    return any(marker in model for marker in _FRITZ_DECT_MODEL_MARKERS)


@dataclass
class GenerateReport:
    dry_run: bool = False
    device_filter: str | None = None
    total_scanned: int = 0
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    skipped_infra: list[str] = field(default_factory=list)
    skipped_ignored: list[str] = field(default_factory=list)
    skipped_service: list[str] = field(default_factory=list)
    skipped_unidentified: list[str] = field(default_factory=list)
    skipped_area: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    no_protocol_count: int = 0
    removed_stale: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    index_file: str = ""
    download_zip: str = ""
    download_dir: str = ""
    orphaned: list[str] = field(default_factory=list)
    # Changes since the last run, derived from the persisted snapshot.
    changed_created: list[str] = field(default_factory=list)
    changed_updated: list[str] = field(default_factory=list)
    changed_renamed: list[tuple[str, str]] = field(default_factory=list)
    changed_removed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "device_filter": self.device_filter,
            "total_scanned": self.total_scanned,
            "created": self.created,
            "updated": self.updated,
            "renamed": [f"{old} -> {new}" for old, new in self.renamed],
            "skipped_infra": self.skipped_infra,
            "skipped_ignored": self.skipped_ignored,
            "skipped_service": self.skipped_service,
            "skipped_unidentified": self.skipped_unidentified,
            "skipped_area": self.skipped_area,
            "skipped_existing": self.skipped_existing,
            "skipped_no_protocol_count": self.no_protocol_count,
            "removed_stale": self.removed_stale,
            "errors": self.errors,
            "index_file": self.index_file,
            "download_zip": self.download_zip,
            "download_dir": self.download_dir,
            "orphaned": self.orphaned,
            "changed_created": self.changed_created,
            "changed_updated": self.changed_updated,
            "changed_renamed": [f"{old} -> {new}" for old, new in self.changed_renamed],
            "changed_removed": self.changed_removed,
        }


class DeviceNoteGenerator:
    """Turn the device/entity registries into note files."""

    def __init__(self, hass: HomeAssistant, options: dict) -> None:
        self.hass = hass
        self.options = options
        raw_export_dir = options.get(CONF_EXPORT_DIR) or DEFAULT_EXPORT_DIR
        # Tolerate surrounding whitespace; absolute paths (e.g. /share/...)
        # are used as-is, relative ones resolve below against the config dir.
        self.export_dir = raw_export_dir.strip() or DEFAULT_EXPORT_DIR
        self.obsidian_base = options.get(CONF_OBSIDIAN_BASE) or DEFAULT_OBSIDIAN_BASE
        self.layout = options.get(CONF_LAYOUT)
        self.merge_mode = options.get(CONF_MERGE_MODE)
        self.ignored = options.get(CONF_IGNORED_DEVICES, "")
        self.force_include = options.get(CONF_FORCE_INCLUDE, "")
        self.only_areas = _filter_values(options.get(CONF_ONLY_AREAS, ""))
        self.extra_map = dict(options.get(CONF_EXTRA_MAP) or {})
        # User-configured type labels override/extend TYPE_MAP.
        self.custom_type_map = parse_type_map(options.get(CONF_TYPE_MAP, ""))
        # User-configured per-device type labels ("fragment: label" lines,
        # matched against device name, id or model). Wins over domain rules.
        self.custom_device_type_map = parse_type_map(
            options.get(CONF_DEVICE_TYPE_MAP, "")
        )
        # Always start with DEFAULT_FIELDS, then add any user-selected fields.
        # The config flow stores display labels; resolve them to the internal
        # frontmatter keys. Unknown entries pass through unchanged.
        selected = {FIELD_LABEL_TO_KEY.get(field, field) for field in DEFAULT_FIELDS}
        user_fields = options.get(CONF_FIELDS)
        if user_fields:
            selected |= {
                FIELD_LABEL_TO_KEY.get(field, field) for field in user_fields
            }
        self.fields: set[str] = selected | {"name", "ha_device_id"} | ACTOR_METADATA_FIELDS

        # Field order for the note frontmatter. The config flow stores a
        # user-defined order (internal keys); any selected field not listed
        # keeps its default FIELD_ORDER position. Falls back to FIELD_ORDER.
        user_order = options.get(CONF_FIELD_ORDER) or []
        if user_order:
            ordered = [key for key in user_order if key in self.fields]
            self.field_order: tuple[str, ...] = tuple(ordered) + tuple(
                key for key in FIELD_ORDER if key in self.fields and key not in ordered
            )
        else:
            self.field_order = FIELD_ORDER
        # "notiz" is always the last field, regardless of any user order.
        if "notiz" in self.fields:
            self.field_order = tuple(
                key for key in self.field_order if key != "notiz"
            ) + ("notiz",)

    def export_root(self) -> Path:
        path = self.export_dir
        if not Path(path).is_absolute():
            path = str(Path(self.hass.config.config_dir) / path)
        return Path(path)

    async def generate(
        self, dry_run: bool = False, device_id: str | None = None
    ) -> GenerateReport:
        report = GenerateReport(dry_run=dry_run, device_filter=device_id)
        root = self.export_root()
        _LOGGER.info("Export-Verzeichnis: %s", root)
        if not dry_run:
            await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)

        dreg = dr.async_get(self.hass)
        ereg = er.async_get(self.hass)
        areg = ar.async_get(self.hass)
        floors = {floor.floor_id: floor.name for floor in flr.async_get(self.hass).floors.values()}

        entries = list(self.hass.config_entries.async_entries())
        entry_domain = {entry.entry_id: entry.domain for entry in entries}
        entry_title = {entry.entry_id: entry.title for entry in entries}

        # Devices that are referenced as via_device by others = infrastructure
        # (coordinator, bridge, strip main unit, receiver) -> skipped.
        children_by_parent: dict[str, list[str]] = {}
        for dev in dreg.devices:
            if dev.via_device_id:
                children_by_parent.setdefault(dev.via_device_id, []).append(dev.id)

        area_by_id = {area.id: area.name for area in areg.areas.values()}
        area_obj = {area.id: area for area in areg.areas.values()}

        domains_by_device: dict[str, set[str]] = {}
        for entity in ereg.entities.values():
            if entity.device_id:
                domains_by_device.setdefault(entity.device_id, set()).add(entity.domain)

        transport_by_device = self._detect_transport(ereg)

        # Single full-tree read per run: identity index backing stale-note
        # and orphan lookups, so those no longer scan the tree per device.
        note_index = await asyncio.to_thread(self._index_note_ids, root)
        previous_state = await asyncio.to_thread(self._load_last_state, root)
        by_device_id: dict[str, Path] = {}
        by_ieee: dict[str, Path] = {}
        for rel, info in note_index.items():
            note_path = root / rel
            if info["device_id"]:
                by_device_id.setdefault(info["device_id"], note_path)
            if info["ieee"]:
                by_ieee.setdefault(info["ieee"], note_path)

        devices = sorted(dreg.devices, key=lambda item: item.id)
        known_device_ids = {dev.id for dev in devices}
        if device_id:
            devices = [dev for dev in devices if dev.id == device_id]
            if not devices:
                report.errors.append(f"Unbekannte Geräte-ID: {device_id}")
                return report
        known_ieees: set[str] = set()
        # Devices explicitly ignored by the user: their notes are treated as
        # removable, otherwise an ignored note could never disappear.
        ignored_device_ids: set[str] = set()

        report.total_scanned = len(devices)
        for dev in devices:
            name = device_display_name(dev)
            if not name:
                report.errors.append(f"{dev.id}: Gerät ohne Namen")
                continue
            if matches_filter(self.force_include, dev, name):
                pass
            elif dev.entry_type == dr.DeviceEntryType.SERVICE:
                report.skipped_service.append(f"{name} ({dev.id})")
                continue
            elif any(
                ident and ident[0] in SERVICE_DOMAINS for ident in dev.identifiers
            ):
                report.skipped_service.append(f"{name} ({dev.id})")
                continue
            elif matches_filter(self.ignored, dev, name):
                report.skipped_ignored.append(f"{name} ({dev.id})")
                ignored_device_ids.add(dev.id)
                continue
            elif children_by_parent.get(dev.id):
                report.skipped_infra.append(f"{name} ({dev.id})")
                continue
            elif not dev.manufacturer and not dev.model:
                report.skipped_unidentified.append(f"{name} ({dev.id})")
                continue
            elif self.only_areas and not self._matches_area_filter(dev, area_by_id):
                report.skipped_area.append(f"{name} ({dev.id})")
                continue

            protocol, ieee = self._resolve_protocol(dev, entry_domain, self.extra_map)
            if protocol is None:
                report.no_protocol_count += 1
                stale = by_device_id.get(dev.id)
                if stale is not None and not dry_run:
                    try:
                        stale.unlink()
                        report.removed_stale.append(str(stale))
                    except OSError as exc:
                        report.errors.append(f"{name}: {exc}")
                continue
            if ieee:
                known_ieees.add(ieee)

            try:
                action, path, old_path = await asyncio.to_thread(
                    self._write_note,
                    report,
                    root,
                    dev,
                    name,
                    protocol,
                    ieee,
                    domains_by_device.get(dev.id, set()),
                    area_by_id,
                    area_obj,
                    floors,
                    entry_title,
                    dreg,
                    transport_by_device,
                     by_device_id,
                     by_ieee,
                     dry_run,
                     previous_state,
                 )

            except OSError as exc:
                report.errors.append(f"{name}: {exc}")
                continue

            if action == "created":
                report.created.append(str(path))
            elif action == "updated":
                report.updated.append(str(path))
            elif action == "renamed":
                report.renamed.append((str(old_path), str(path)))
            elif action == "existing":
                report.skipped_existing.append(str(path))

        # Notes of ignored devices are removable as well: the device stays
        # registered, so the plain orphan check would keep them forever.
        known_device_ids -= ignored_device_ids
        # Single-device scans never touch orphans: with only one known
        # device in the loop, every other note would look orphaned.
        if device_id is None:
            if dry_run:
                report.orphaned = [
                    str(root / rel)
                    for rel in self._orphan_paths(
                        note_index, known_device_ids, known_ieees
                    )
                ]
            else:
                report.orphaned = await asyncio.to_thread(
                    self._delete_orphans,
                    root,
                    report,
                    note_index,
                    known_device_ids,
                    known_ieees,
                )
        if not dry_run:
            index_path = await asyncio.to_thread(self._write_overviews, root)
            report.index_file = str(index_path)
            await asyncio.to_thread(self._write_changelog, root, report)
            report.download_zip, report.download_dir = await asyncio.to_thread(
                self._mirror_to_www, root, report
            )

        return report

    def _snapshot_notes(self, root: Path) -> dict[str, dict]:
        """Relative path -> {device_id, ieee, hash} for every generated note.

        Overviews and the changelog are excluded. The hash covers the full
        file content, so hand-edits to frontmatter or body also register as
        an update.
        """
        notes: dict[str, dict] = {}
        for path in sorted(root.glob("**/*.md")):
            if _is_generated_overview(path) or path.name == CHANGELOG_FILENAME:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parsed = parse_note(text)
            if not parsed:
                continue
            fields, body = parsed
            notes[str(path.relative_to(root))] = {
                "device_id": fields.get("ha_device_id", ""),
                "ieee": fields.get("ieee_address", ""),
                "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "body": body,
            }
        return notes

    def _load_last_state(self, root: Path) -> dict[str, dict]:
        state_path = root / LAST_STATE_FILENAME
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save_last_state(self, root: Path, state: dict[str, dict]) -> None:
        state_path = root / LAST_STATE_FILENAME
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _diff_states(
        old: dict[str, dict], new: dict[str, dict]
    ) -> tuple[list[str], list[str], list[tuple[str, str]], list[str]]:
        """Compare two snapshots: (created, updated, renamed, removed).

        Renames are detected via a stable identity (ha_device_id, falling
        back to ieee_address); content changes on a renamed note count as
        a rename only.
        """
        created: list[str] = []
        updated: list[str] = []
        renamed: list[tuple[str, str]] = []
        removed: list[str] = []

        old_by_id: dict[str, str] = {}
        for path, info in old.items():
            dev_id = info.get("device_id") or info.get("ieee") or ""
            if dev_id:
                old_by_id.setdefault(dev_id, path)
        new_by_id: dict[str, str] = {}
        for path, info in new.items():
            dev_id = info.get("device_id") or info.get("ieee") or ""
            if dev_id:
                new_by_id.setdefault(dev_id, path)

        renamed_old: set[str] = set()
        renamed_new: set[str] = set()
        for dev_id, old_path in old_by_id.items():
            new_path = new_by_id.get(dev_id)
            if new_path and new_path != old_path:
                renamed.append((old_path, new_path))
                renamed_old.add(old_path)
                renamed_new.add(new_path)

        for path, info in new.items():
            if path in old:
                if old[path].get("hash") != info.get("hash"):
                    updated.append(path)
            elif path not in renamed_new:
                created.append(path)

        for path in old:
            if path not in new and path not in renamed_old:
                removed.append(path)

        return created, updated, renamed, removed

    def _write_changelog(self, root: Path, report: GenerateReport) -> None:
        """Persist the new snapshot and append a dated entry to CHANGELOG.md.

        The changelog only grows when something actually changed; the file
        keeps the most recent CHANGELOG_MAX_ENTRIES sections. Overviews are
        ignored by the orphan scan, so the changelog is never deleted.
        """
        new_state = self._snapshot_notes(root)
        old_state = self._load_last_state(root)
        if not old_state:
            # First run (no previous snapshot): establish a baseline without
            # reporting every existing note as new.
            self._save_last_state(root, new_state)
            return
        created, updated, renamed, removed = self._diff_states(old_state, new_state)
        report.changed_created = created
        report.changed_updated = updated
        report.changed_renamed = renamed
        report.changed_removed = removed

        if not (created or updated or renamed or removed):
            # Snapshot already matches the tree; no need to rewrite it.
            return
        self._save_last_state(root, new_state)

        changelog_path = root / CHANGELOG_FILENAME
        old_sections: list[str] = []
        if changelog_path.exists():
            try:
                text = changelog_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for section in re.split(r"\n(?=## )", text):
                if section.startswith("## "):
                    old_sections.append(section.rstrip("\n"))

        lines = [f"## {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
        if created:
            lines.append(f"**Neu ({len(created)}):**")
            for path in created:
                lines.append(f"- {path}")
        if renamed:
            lines.append(f"**Umbenannt ({len(renamed)}):**")
            for old_path, new_path in renamed:
                lines.append(f"↔ {old_path} → {new_path}")
        if updated:
            lines.append(f"**Geändert ({len(updated)}):**")
            for path in updated:
                lines.append(f"- {path}")
        if removed:
            lines.append(f"**Entfernt ({len(removed)}):**")
            for path in removed:
                lines.append(f"- {path}")
        section = "\n".join(lines)

        sections = [section] + old_sections[: CHANGELOG_MAX_ENTRIES - 1]
        # No "# CHANGELOG" heading: Obsidian shows the file name as inline
        # title already, a heading would display the word twice.
        changelog_path.write_text(
            "\n\n".join(sections) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _resolve_protocol(
        dev, entry_domain: dict[str, str], extra_map: dict[str, str] | None = None
    ) -> tuple[str | None, str | None]:
        """protocol value + optional ieee address for the device.

        User-configured mappings from the options flow (extra_map) win over
        the built-in PROTOCOL_MAP. A device belongs to exactly one config
        entry, so the owning integration is read once; if it does not yield a
        protocol the identifiers are tried next.
        """
        extra_map = extra_map or {}
        entry_id = dev.config_entry_id
        domain = entry_domain.get(entry_id) if entry_id else None
        if domain in extra_map:
            return extra_map[domain], None
        if domain in PROTOCOL_MAP and not (
            domain in {"fritz", "fritzbox"} and not _is_fritz_dect_device(dev, domain)
        ):
            return PROTOCOL_MAP[domain], None
        for identifier in dev.identifiers:
            domain = identifier[0]
            value = identifier[1] if len(identifier) > 1 else None
            if domain == "mqtt" and value == ZIGBEE2MQTT_BRIDGE:
                return "Zigbee", None
            if domain == "mqtt" and isinstance(value, str) and value.startswith("zigbee2mqtt_"):
                return "Zigbee", value[len("zigbee2mqtt_") :]
            if domain in extra_map:
                return extra_map[domain], None
            if domain in PROTOCOL_MAP:
                if domain in {"fritz", "fritzbox"} and not _is_fritz_dect_device(dev, domain):
                    continue
                ieee = value if domain == "zha" else None
                return PROTOCOL_MAP[domain], ieee
        return None, None

    @staticmethod
    def _detect_transport(ereg) -> dict[str, str]:
        """Map device_id -> runtime transport for Matter devices.

        The Matter integration registers diagnostics entities (disabled by the
        integration, so absent from the state machine) whose unique_id contains
        a transport marker (e.g. "ThreadDiagnosticsChannel-53-0" for Thread,
        "WiFiDiagnosticsRssi-54-4" for WiFi). Bluetooth only appears during
        commissioning and is never a runtime transport, so it stays empty.
        """
        transport_by_device: dict[str, str] = {}
        for entity in ereg.entities.values():
            if entity.platform != "matter" or not entity.unique_id:
                continue
            if not entity.device_id:
                continue
            for marker, transport in _MATTER_TRANSPORT_MARKERS:
                if marker in entity.unique_id:
                    transport_by_device.setdefault(entity.device_id, transport)
                    break
        return transport_by_device

    @staticmethod
    def _infer_type(domains: set[str]) -> str | None:
        if not domains:
            return None
        for domain, label in TYPE_MAP.items():
            if domain in domains:
                return label
        if domains.issubset(
            {"sensor", "binary_sensor", "event", "number", "select", "text", "button", "update"}
        ):
            return "Sensor"
        return None

    def _infer_type_custom(self, domains: set[str]) -> str | None:
        """Type suggestion honoring user-configured labels.

        User mappings win over TYPE_MAP; anything else falls back to the
        built-in inference (TYPE_MAP first, then the Sensor fallback).
        """
        if not domains:
            return None
        for domain, label in self.custom_type_map.items():
            if domain in domains:
                return label
        return self._infer_type(domains)

    @staticmethod
    def _infer_entity_type(domains: set[str]) -> str | None:
        normalized = {
            domain.strip().lower() for domain in domains if domain and domain.strip()
        }
        if len(normalized) != 1:
            return None
        return next(iter(normalized))

    def _match_device_type(
        self, device_id: str, name: str, model: str | None
    ) -> str | None:
        """Match user-configured device override lines ("fragment: label").

        An exact device_id match wins first; otherwise the first line whose
        fragment occurs (case-insensitive) in the device name or model wins.
        Returns None when nothing matches. Only used to fill an empty typ,
        so hand-maintained values are never overwritten.
        """
        if not self.custom_device_type_map:
            return None
        lowered_id = (device_id or "").lower()
        for fragment, label in self.custom_device_type_map.items():
            if fragment and fragment == lowered_id:
                return label
        name_lower = (name or "").lower()
        model_lower = (model or "").lower()
        for fragment, label in self.custom_device_type_map.items():
            if fragment and (fragment in name_lower or fragment in model_lower):
                return label
        return None

    def _matches_area_filter(self, dev, area_by_id: dict[str, str]) -> bool:
        """True if the device area matches at least one configured filter value.

        Matches are case-insensitive substring checks against the area name.
        Devices without an area never match (only_areas is non-empty here).
        """
        area = area_by_id.get(dev.area_id)
        if not area:
            return False
        area_lower = area.lower()
        return any(fragment in area_lower for fragment in self.only_areas)

    def _folder_name(self, dev, protocol: str, area_by_id: dict[str, str]) -> str:
        if self.layout == LAYOUT_AREA:
            return area_by_id.get(dev.area_id) or "Ohne Bereich"
        return protocol

    def _index_note_ids(self, root: Path) -> dict[str, dict]:
        """relpath -> {device_id, ieee} for every note in the export tree.

        Single full-tree read per run backing stale-note and orphan lookups.
        Overviews carry no device identity and are excluded; notes survive a
        layout switch (protocol folders <-> area folders) since matching is
        by identity, not by path.
        """
        notes: dict[str, dict] = {}
        if not root.is_dir():
            return notes
        for path in sorted(root.glob("**/*.md")):
            if _is_generated_overview(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parsed = parse_note(text)
            if not parsed:
                continue
            fields, _body = parsed
            notes[str(path.relative_to(root))] = {
                "device_id": fields.get("ha_device_id", ""),
                "ieee": fields.get("ieee_address", ""),
            }
        return notes

    @staticmethod
    def _orphan_paths(
        notes: dict[str, dict], known_device_ids: set[str], known_ieees: set[str]
    ) -> list[str]:
        """Relpaths whose registry id/ieee matches no known device. Pure filter."""
        orphans: list[str] = []
        for rel, info in notes.items():
            device_id = info.get("device_id")
            ieee = info.get("ieee")
            if device_id:
                if device_id in known_device_ids:
                    continue
            elif ieee:
                if ieee in known_ieees:
                    continue
            else:
                continue
            orphans.append(rel)
        return orphans

    def _computed_fields(
        self,
        dev,
        name: str,
        protocol: str,
        ieee: str | None,
        domains: set[str],
        area_by_id: dict[str, str],
        area_obj: dict,
        floors: dict[str, str],
        entry_title: dict[str, str],
        dreg,
    ) -> dict[str, str]:
        computed: dict[str, str] = {
            "name": name,
            "hersteller": dev.manufacturer or "",
            "modell": dev.model or "",
            "protokoll": protocol,
        }
        if protocol == "Zigbee":
            computed["friendly_name"] = name
            if ieee:
                computed["ieee_address"] = ieee
        computed["ha_device_id"] = dev.id

        if getattr(dev, "sw_version", None):
            computed["firmware"] = str(dev.sw_version)
        if getattr(dev, "hw_version", None):
            computed["hardware_revision"] = str(dev.hw_version)
        if getattr(dev, "serial_number", None):
            computed["seriennummer"] = str(dev.serial_number)
        mac = _first_connection(dev, "mac")
        if mac:
            computed["mac"] = mac
        area = area_obj.get(dev.area_id)
        if area:
            computed["bereich"] = area_by_id.get(dev.area_id) or ""
            if area.floor_id in floors:
                computed["etage"] = floors[area.floor_id]
        via = dreg.async_get(dev.via_device_id) if dev.via_device_id else None
        entry_id = dev.config_entry_id
        if via:
            via_name = device_display_name(via)
            if via_name and via_name != entry_title.get(entry_id, ""):
                computed["verbunden_über"] = via_name
        title = entry_title.get(entry_id, "") if entry_id else ""
        if title:
            computed["Integration"] = title
        if domains:
            computed["entity_count"] = str(len(domains))
        entity_type = self._infer_entity_type(domains)
        if entity_type:
            computed["entity_type"] = entity_type
        if getattr(dev, "configuration_url", None):
            computed["config_url"] = str(dev.configuration_url)

        override_typ = self._match_device_type(dev.id, name, dev.model)
        if override_typ:
            computed["typ"] = override_typ
        else:
            inferred_typ = self._infer_type_custom(domains)
            if inferred_typ:
                computed["typ"] = inferred_typ

        # Graph link to the protocol overview (Obsidian parses wikilinks in
        # frontmatter values, quoted or not). Always protocol-based, even in
        # area layout, so the note lands in its protocol cluster.
        computed["übersicht"] = (
            f"[[{OVERVIEW_FILENAME_TEMPLATE.format(proto=protocol)[:-3]}]]"
        )

        return {key: value for key, value in computed.items() if value}

    @staticmethod
    def _content_signature(
        fields: dict[str, str], body: str
    ) -> tuple[tuple[str, str], str]:
        content = {
            key: value
            for key, value in fields.items()
            if key not in _NOTE_RENAME_FIELDS
        }
        return tuple(sorted(content.items())), body

    @staticmethod
    def _valid_updated(value: str | None) -> bool:
        return bool(value and _NOTE_DATE_PATTERN.fullmatch(value))

    def _write_note(
        self,
        report: GenerateReport,
        root: Path,
        dev,
        name: str,
        protocol: str,
        ieee: str | None,
        domains: set[str],
        area_by_id: dict[str, str],
        area_obj: dict,
        floors: dict[str, str],
        entry_title: dict[str, str],
        dreg,
        transport_by_device: dict[str, str],
         by_device_id: dict[str, Path],
         by_ieee: dict[str, Path],
         dry_run: bool,
         previous_state: dict[str, dict] | None = None,
     ) -> tuple[str, Path, Path | None]:

        folder = root / safe_filename(self._folder_name(dev, protocol, area_by_id))
        target = folder / f"{safe_filename(name)}.md"

        existing_path = None
        if target.exists():
            parsed = parse_note(target.read_text(encoding="utf-8", errors="replace"))
            if parsed is None:
                report.errors.append(
                    f"{name}: Ziel {target} existiert bereits ohne gültiges Frontmatter"
                )
                return "error", target, None
            fields, _body = parsed
            if not (
                fields.get("ha_device_id") == dev.id
                or (ieee and fields.get("ieee_address") == ieee)
            ):
                report.errors.append(
                    f"{name}: Namenskollision – {target} gehört zu einem anderen Gerät"
                )
                return "error", target, None
            existing_path = target
        else:
            existing_path = by_device_id.get(dev.id)
            if existing_path is None and ieee:
                existing_path = by_ieee.get(ieee)

        existing_body = ""
        computed = self._computed_fields(
            dev, name, protocol, ieee, domains, area_by_id, area_obj, floors, entry_title, dreg
        )

        if existing_path is None:
            final = {}
            output_order = self.field_order
            for key in output_order:
                if key in computed and key in self.fields:
                    final[key] = computed[key]
                elif key in HAND_FIELDS and key in self.fields:
                    final[key] = ""
            if "note_type" in self.fields:
                final["note_type"] = "actor"
            if "entity_type" in self.fields and computed.get("entity_type"):
                final["entity_type"] = computed["entity_type"]
            if "updated" in self.fields:
                final["updated"] = datetime.now().strftime("%Y-%m-%d")
            if (
                protocol == "Matter"
                and "transport" in self.fields
                and transport_by_device.get(dev.id)
            ):
                final["transport"] = transport_by_device[dev.id]
            action = "created"
        else:
            if self.merge_mode == MERGE_MODE_CREATE_ONLY:
                return "existing", existing_path, None
            parsed = parse_note(existing_path.read_text(encoding="utf-8", errors="replace"))
            if parsed is None:
                report.errors.append(
                    f"{name}: bestehende Datei {existing_path} ist keine gültige Notiz"
                )
                return "error", existing_path, None
            existing_fields, existing_body = parsed
            output_order = self.field_order
            output_order += tuple(
                key
                for key in existing_fields
                if key not in output_order and key not in RETIRED_HA_FIELDS
            )
            if "notiz" in output_order:
                output_order = tuple(key for key in output_order if key != "notiz") + (
                    "notiz",
                )
            final = {}
            for key in output_order:
                if key in existing_fields:
                    if key in RETIRED_HA_FIELDS:
                        continue
                    final[key] = existing_fields[key]
                elif key in HAND_FIELDS and key in self.fields:
                    final[key] = ""
                elif (
                    key in self.fields
                    and (key == "typ" or key not in GENERATED_ON_CREATE_FIELDS)
                    and computed.get(key)
                ):
                    final[key] = computed[key]
            if "note_type" in self.fields:
                final["note_type"] = "actor"
            if "entity_type" in self.fields:
                if computed.get("entity_type"):
                    final["entity_type"] = computed["entity_type"]
                else:
                    final.pop("entity_type", None)
            if (
                protocol == "Matter"
                and "transport" in self.fields
                and transport_by_device.get(dev.id)
                and not final.get("transport")
            ):
                final["transport"] = transport_by_device[dev.id]
            if computed.get("name"):
                final["name"] = computed["name"]
            if (
                "übersicht" in self.fields
                and not final.get("übersicht")
                and computed.get("übersicht")
            ):
                final["übersicht"] = computed["übersicht"]
            previous_body = (previous_state or {}).get(
                str(existing_path.relative_to(root)), {}
            ).get("body")
            comparison_body = (
                previous_body if isinstance(previous_body, str) else existing_body
            )
            content_changed = self._content_signature(
                existing_fields, comparison_body
            ) != self._content_signature(final, existing_body)
            existing_updated = existing_fields.get("updated")
            if "updated" in self.fields:
                if content_changed:
                    final["updated"] = datetime.now().strftime("%Y-%m-%d")
                elif self._valid_updated(existing_updated):
                    final["updated"] = existing_updated
                else:
                    final.pop("updated", None)
            action = "updated"
            if existing_path != target:
                action = "renamed"

        if dry_run:
            return action, target, existing_path

        folder.mkdir(parents=True, exist_ok=True)
        emit_empty = frozenset(key for key in HAND_FIELDS if key in self.fields)
        content = serialize_note(final, output_order, emit_empty)
        if existing_body:
            content += "\n" + existing_body
        content += "\n"
        if action == "renamed":
            existing_path.rename(target)
        target.write_text(content, encoding="utf-8")
        return action, target, existing_path

    def _write_overviews(self, root: Path) -> Path:
        """Write the dataview overview pages (root + one per protocol).

        The root page aggregates all notes by typ and links each protocol
        overview via a fixed emoji. Each protocol page lists its notes as a
        dataview table. All pages are written even when a protocol folder is
        empty, so the Kategorien links always resolve.
        """
        protocol_filenames = {
            proto: OVERVIEW_FILENAME_TEMPLATE.format(proto=proto)
            for proto in OVERVIEW_PROTOCOLS
        }

        kategorien = [
            f"- [[{protocol_filenames[proto][:-3]}|{OVERVIEW_EMOJIS.get(proto, '')} {proto}]]"
            for proto in OVERVIEW_PROTOCOLS
        ]
        root_lines = [
            "---",
            "aliases: []",
            "---",
            "",
            "# ⚡ Aktoren",
            "",
            "```dataview",
            "TABLE WITHOUT ID",
            '  typ AS "🔧 Typ",',
            '  length(rows) AS "🔢 Anzahl"',
            f'FROM "{self.obsidian_base}"',
            "WHERE file.path != this.file.path ",
            '  AND !startswith(file.name, "01-Übersicht")',
            "GROUP BY typ",
            "SORT typ ASC",
            "```",
            "",
            "## Kategorien",
            *kategorien,
            "",
        ]
        root_target = root / OVERVIEW_ROOT_FILENAME
        root_target.write_text("\n".join(root_lines), encoding="utf-8")

        for proto in OVERVIEW_PROTOCOLS:
            proto_folder = root / proto
            proto_folder.mkdir(parents=True, exist_ok=True)
            columns = [
                '  file.link AS "Aktor",',
                '  typ AS "🔧 Typ",',
                '  hersteller AS "🏷️ Hersteller",',
                '  modell AS "📦 Modell",',
            ]
            if proto == "Matter":
                columns.append('  transport AS "🌐 Transport",')
            columns.extend(
                [
                    '  lagerort AS "📍 Lagerort",',
                    '  menge AS "🔢 Menge"',
                ]
            )
            proto_lines = [
                "---",
                "aliases: []",
                "---",
                "",
                f"# ⚡ {proto}-Aktoren",
                "",
                f"[[{OVERVIEW_ROOT_FILENAME[:-3]}|← Zurück zur Übersicht]]",
                "",
                "```dataview",
                "TABLE WITHOUT ID",
                *columns,
                f'FROM "{self.obsidian_base}/{proto}"',
                "WHERE file.path != this.file.path",
                "SORT typ ASC, hersteller ASC",
                "```",
                "",
            ]
            proto_target = proto_folder / protocol_filenames[proto]
            proto_target.write_text("\n".join(proto_lines), encoding="utf-8")

        # Drop the legacy static index note if it still exists.
        legacy = root / LEGACY_INDEX_FILENAME
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                # Not fatal; it is just skipped in mirror/orphan scans.
                pass

        return root_target

    def _delete_orphans(
        self,
        root: Path,
        report: GenerateReport,
        notes: dict[str, dict],
        known_device_ids: set[str],
        known_ieees: set[str],
    ) -> list[str]:
        """Delete notes whose registry id/ieee matches no known device.

        Called after the main loop (non dry-run only) with the pre-loop
        identity index. Returns the list of deleted paths and records them
        in report.removed_stale.
        """
        deleted: list[str] = []
        for rel in self._orphan_paths(notes, known_device_ids, known_ieees):
            path = root / rel
            try:
                path.unlink()
                deleted.append(str(path))
                report.removed_stale.append(str(path))
            except OSError as exc:
                report.errors.append(f"{path.name}: {exc}")
        return deleted

    @staticmethod
    def _tree_changed(report: GenerateReport) -> bool:
        """True when the export tree changed during this run.

        Note: report.updated lists every rewritten note (deterministic
        regeneration), so only the snapshot diff (changed_*) plus structural
        lists are a reliable change signal.
        """
        return bool(
            report.created
            or report.renamed
            or report.removed_stale
            or report.changed_created
            or report.changed_updated
            or report.changed_renamed
            or report.changed_removed
        )

    def _mirror_to_www(self, root: Path, report: GenerateReport) -> tuple[str, str]:
        """Mirror the note files into www/ and build a zip for download.

        Skipped when the tree did not change since the last run (the mirror
        already matches); always rebuilt when the mirror dir or zip is
        missing. Refused when the export directory is the mirror target or
        lives inside it, because the rmtree below would delete the notes it
        is supposed to copy.
        """
        www_dir = mirror_dir(self.hass.config.config_dir)
        zip_path = www_dir.with_name(f"{MIRROR_DIRNAME}.zip")
        if mirror_conflicts_with_export(self.hass.config.config_dir, root):
            message = (
                f"Export-Verzeichnis {root} liegt im Mirror-Ziel {www_dir} – "
                "Mirror und ZIP werden übersprungen, damit die Notizen erhalten bleiben"
            )
            _LOGGER.error(message)
            report.errors.append(message)
            return "", ""
        if (
            not self._tree_changed(report)
            and www_dir.is_dir()
            and zip_path.is_file()
        ):
            _LOGGER.info("Export unverändert, Mirror/ZIP übersprungen")
            return "/local/device_inventory_notes.zip", "/local/device_inventory_notes/"
        if www_dir.exists():
            shutil.rmtree(www_dir)
        www_dir.mkdir(parents=True, exist_ok=True)

        for path in sorted(root.rglob("*.md")):
            if path.name == LEGACY_INDEX_FILENAME:
                continue
            rel = path.relative_to(root)
            dest = www_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*.md")):
                zf.write(path, path.relative_to(root))

        return "/local/device_inventory_notes.zip", "/local/device_inventory_notes/"
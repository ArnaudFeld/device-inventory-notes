"""Generate Obsidian-compatible device note files from the Home Assistant registries."""

from __future__ import annotations

import asyncio
import datetime
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as flr

from .const import (
    CONF_EXPORT_DIR,
    CONF_EXTRA_MAP,
    CONF_FIELDS,
    CONF_FORCE_INCLUDE,
    CONF_IGNORED_DEVICES,
    CONF_LAYOUT,
    CONF_MERGE_MODE,
    DEFAULT_EXPORT_DIR,
    DEFAULT_FIELDS,
    FIELD_LABEL_TO_KEY,
    FIELD_ORDER,
    GENERATED_ON_CREATE_FIELDS,
    HA_FIELDS,
    HAND_FIELDS,
    INDEX_FILENAME,
    LAYOUT_AREA,
    MERGE_MODE_CREATE_ONLY,
    PROTOCOL_MAP,
    SERVICE_DOMAINS,
    TYPE_MAP,
)

_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|#%{}]+')
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")

ZIGBEE2MQTT_BRIDGE = "zigbee2mqtt_bridge"

# Fields that used to be HA-managed but no longer exist. Existing notes get
# these removed on the next update.
RETIRED_HA_FIELDS: frozenset[str] = frozenset({"transport"})


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


def _filter_values(raw: str) -> list[str]:
    return [
        line.strip().lower()
        for line in (raw or "").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


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
    body = "\n".join(lines[end + 1 :]).strip("\n")
    return fields, body


def _quote(value: str) -> str:
    value = _CONTROL_CHARS.sub(" ", value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def serialize_note(
    fields: dict[str, str], order: tuple[str, ...], emit_empty: frozenset[str] = frozenset()
) -> str:
    """Serialize frontmatter in the fixed field order, skipping empty values.

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


@dataclass
class GenerateReport:
    dry_run: bool = False
    total_scanned: int = 0
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    skipped_infra: list[str] = field(default_factory=list)
    skipped_ignored: list[str] = field(default_factory=list)
    skipped_service: list[str] = field(default_factory=list)
    skipped_unidentified: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    no_protocol_count: int = 0
    errors: list[str] = field(default_factory=list)
    index_file: str = ""
    download_zip: str = ""
    download_dir: str = ""
    orphaned: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "total_scanned": self.total_scanned,
            "created": self.created,
            "updated": self.updated,
            "renamed": [f"{old} -> {new}" for old, new in self.renamed],
            "skipped_infra": self.skipped_infra,
            "skipped_ignored": self.skipped_ignored,
            "skipped_service": self.skipped_service,
            "skipped_unidentified": self.skipped_unidentified,
            "skipped_existing": self.skipped_existing,
            "skipped_no_protocol_count": self.no_protocol_count,
            "errors": self.errors,
            "index_file": self.index_file,
            "download_zip": self.download_zip,
            "download_dir": self.download_dir,
            "orphaned": self.orphaned,
        }


class DeviceNoteGenerator:
    """Turn the device/entity registries into note files."""

    def __init__(self, hass: HomeAssistant, options: dict) -> None:
        self.hass = hass
        self.options = options
        self.export_dir = options.get(CONF_EXPORT_DIR) or DEFAULT_EXPORT_DIR
        self.layout = options.get(CONF_LAYOUT)
        self.merge_mode = options.get(CONF_MERGE_MODE)
        self.ignored = options.get(CONF_IGNORED_DEVICES, "")
        self.force_include = options.get(CONF_FORCE_INCLUDE, "")
        self.extra_map = dict(options.get(CONF_EXTRA_MAP) or {})
        # Always start with DEFAULT_FIELDS, then add any user-selected fields.
        # The config flow stores display labels; resolve them to the internal
        # frontmatter keys. Unknown entries pass through unchanged.
        selected = {FIELD_LABEL_TO_KEY.get(field, field) for field in DEFAULT_FIELDS}
        user_fields = options.get(CONF_FIELDS)
        if user_fields:
            selected |= {
                FIELD_LABEL_TO_KEY.get(field, field) for field in user_fields
            }
        self.fields: set[str] = selected | {"name", "ha_device_id"}

    def export_root(self) -> Path:
        path = self.export_dir
        if not Path(path).is_absolute():
            path = str(Path(self.hass.config.config_dir) / path)
        return Path(path)

    async def generate(self, dry_run: bool = False) -> GenerateReport:
        report = GenerateReport(dry_run=dry_run)
        root = self.export_root()
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
        for dev in dreg.devices.values():
            if dev.via_device_id:
                children_by_parent.setdefault(dev.via_device_id, []).append(dev.id)

        area_by_id = {area.id: area.name for area in areg.areas.values()}
        area_obj = {area.id: area for area in areg.areas.values()}

        domains_by_device: dict[str, set[str]] = {}
        for entity in ereg.entities.values():
            if entity.device_id:
                domains_by_device.setdefault(entity.device_id, set()).add(entity.domain)

        devices = sorted(dreg.devices.values(), key=lambda item: item.id)
        known_device_ids = {dev.id for dev in devices}
        known_ieees: set[str] = set()
        index_entries: list[tuple[str, str, str]] = []

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
                continue
            elif children_by_parent.get(dev.id):
                report.skipped_infra.append(f"{name} ({dev.id})")
                continue
            elif not dev.manufacturer and not dev.model:
                report.skipped_unidentified.append(f"{name} ({dev.id})")
                continue

            protocol, ieee = self._resolve_protocol(dev, entry_domain, self.extra_map)
            if protocol is None:
                report.no_protocol_count += 1
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
                    dry_run,
                )
            except OSError as exc:
                report.errors.append(f"{name}: {exc}")
                continue

            if action in {"created", "updated", "renamed", "existing"}:
                index_entries.append((name, protocol, area_by_id.get(dev.area_id) or ""))
            if action == "created":
                report.created.append(str(path))
            elif action == "updated":
                report.updated.append(str(path))
            elif action == "renamed":
                report.renamed.append((str(old_path), str(path)))
            elif action == "existing":
                report.skipped_existing.append(str(path))

        report.orphaned = await asyncio.to_thread(
            self._find_orphans, root, known_device_ids, known_ieees
        )
        if not dry_run:
            index_path = await asyncio.to_thread(self._write_index, root, index_entries)
            report.index_file = str(index_path)
            report.download_zip, report.download_dir = await asyncio.to_thread(
                self._mirror_to_www, root
            )

        return report

    @staticmethod
    def _resolve_protocol(
        dev, entry_domain: dict[str, str], extra_map: dict[str, str] | None = None
    ) -> tuple[str | None, str | None]:
        """protocol value + optional ieee address for the device.

        User-configured mappings from the options flow (extra_map) win over
        the built-in PROTOCOL_MAP.
        """
        extra_map = extra_map or {}
        for entry_id in dev.config_entries:
            domain = entry_domain.get(entry_id)
            if domain in extra_map:
                return extra_map[domain], None
            if domain in PROTOCOL_MAP:
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
                ieee = value if domain == "zha" else None
                return PROTOCOL_MAP[domain], ieee
        return None, None

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

    def _folder_name(self, dev, protocol: str, area_by_id: dict[str, str]) -> str:
        if self.layout == LAYOUT_AREA:
            return area_by_id.get(dev.area_id) or "Ohne Bereich"
        return protocol

    def _find_by_identifier(
        self, root: Path, device_id: str, ieee: str | None
    ) -> Path | None:
        """Locate an existing note anywhere in the export tree by id/ieee.

        Searches the whole tree so notes survive a layout switch
        (protocol folders <-> area folders).
        """
        if not root.is_dir():
            return None
        for path in sorted(root.glob("**/*.md")):
            if path.name == INDEX_FILENAME:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parsed = parse_note(text)
            if not parsed:
                continue
            fields, _body = parsed
            if fields.get("ha_device_id") == device_id:
                return path
            if ieee and fields.get("ieee_address") == ieee:
                return path
        return None

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
        via = dreg.devices.get(dev.via_device_id) if dev.via_device_id else None
        if via:
            via_name = device_display_name(via)
            entry_id = next(iter(dev.config_entries), None)
            entry = entry_title.get(entry_id) if entry_id else None
            if via_name and via_name != entry:
                computed["verbunden_über"] = via_name
        if dev.config_entries:
            title = entry_title.get(next(iter(dev.config_entries)), "")
            if title:
                computed["Integration"] = title
        if domains:
            computed["entity_count"] = str(len(domains))
        if getattr(dev, "configuration_url", None):
            computed["config_url"] = str(dev.configuration_url)

        inferred_typ = self._infer_type(domains)
        if inferred_typ:
            computed["typ"] = inferred_typ

        return {key: value for key, value in computed.items() if value}

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
        dry_run: bool,
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
            existing_path = self._find_by_identifier(root, dev.id, ieee)

        existing_body = ""
        computed = self._computed_fields(
            dev, name, protocol, ieee, domains, area_by_id, area_obj, floors, entry_title, dreg
        )

        if existing_path is None:
            final = {}
            for key in FIELD_ORDER:
                if key in computed and key in self.fields:
                    final[key] = computed[key]
                elif key in HAND_FIELDS and key in self.fields:
                    final[key] = ""
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
            final = {}
            for key in FIELD_ORDER:
                if key in existing_fields:
                    if key in RETIRED_HA_FIELDS:
                        continue
                    if key in HA_FIELDS and key not in self.fields:
                        continue
                    final[key] = existing_fields[key]
                elif key in HAND_FIELDS and key in self.fields:
                    final[key] = ""
                elif (
                    key in self.fields
                    and key not in GENERATED_ON_CREATE_FIELDS
                    and computed.get(key)
                ):
                    final[key] = computed[key]
            if computed.get("name"):
                final["name"] = computed["name"]
            action = "updated"
            if existing_path != target:
                action = "renamed"

        if dry_run:
            return action, target, existing_path

        folder.mkdir(parents=True, exist_ok=True)
        emit_empty = frozenset(key for key in HAND_FIELDS if key in self.fields)
        content = serialize_note(final, FIELD_ORDER, emit_empty)
        if existing_body:
            content += "\n" + existing_body
        content += "\n"
        if action == "renamed":
            existing_path.rename(target)
        target.write_text(content, encoding="utf-8")
        return action, target, existing_path

    def _write_index(self, root: Path, entries: list[tuple[str, str, str]]) -> Path:
        """Write the map-of-content note grouped by protocol."""
        by_proto: dict[str, list[tuple[str, str]]] = {}
        for name, protocol, area in entries:
            by_proto.setdefault(protocol, []).append((name, area))

        lines = [
            "---",
            'typ: "Übersicht"',
            f'update: "{datetime.date.today().isoformat()}"',
            "---",
            "",
            "# Geräte-Übersicht",
            "",
        ]
        for protocol in sorted(by_proto):
            lines.append(f"## {protocol}")
            for name, area in sorted(by_proto[protocol]):
                suffix = f" ({area})" if area else ""
                lines.append(f"- [[{safe_filename(name)}]]{suffix}")
            lines.append("")

        target = root / INDEX_FILENAME
        target.write_text("\n".join(lines), encoding="utf-8")
        return target

    def _find_orphans(
        self, root: Path, known_device_ids: set[str], known_ieees: set[str]
    ) -> list[str]:
        """Notes whose registry id/ieee matches no known device. Read-only."""
        if not root.is_dir():
            return []
        orphans: list[str] = []
        for path in sorted(root.glob("**/*.md")):
            if path.name == INDEX_FILENAME:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parsed = parse_note(text)
            if not parsed:
                continue
            fields, _body = parsed
            device_id = fields.get("ha_device_id")
            ieee = fields.get("ieee_address")
            if device_id:
                if device_id in known_device_ids:
                    continue
            elif ieee:
                if ieee in known_ieees:
                    continue
            else:
                continue
            orphans.append(str(path))
        return orphans

    def _mirror_to_www(self, root: Path) -> tuple[str, str]:
        """Mirror the note files into www/ and build a zip for download."""
        www_dir = Path(self.hass.config.config_dir) / "www" / "device_inventory_notes"
        if www_dir.exists():
            shutil.rmtree(www_dir)
        www_dir.mkdir(parents=True, exist_ok=True)

        for path in sorted(root.rglob("*.md")):
            if path.parent == root and path.name != INDEX_FILENAME:
                continue
            rel = path.relative_to(root)
            dest = www_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

        zip_path = Path(self.hass.config.config_dir) / "www" / "device_inventory_notes.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*.md")):
                zf.write(path, path.relative_to(root))

        return "/local/device_inventory_notes.zip", "/local/device_inventory_notes/"
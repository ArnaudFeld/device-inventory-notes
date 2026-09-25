"""Config flow for the Device Inventory Notes integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector

from urllib.parse import quote

from .const import (
    CONF_AUTO_UPDATE,
    CONF_DEVICE_TYPE_MAP,
    CONF_EXPORT_DIR,
    CONF_EXTRA_DOMAINS,
    CONF_EXTRA_MAP,
    CONF_EXTRA_PROTOCOL,
    CONF_EXTRA_REMOVE,
    CONF_FIELD_ORDER,
    CONF_FIELDS,
    CONF_FORCE_INCLUDE,
    CONF_IGNORED_DEVICES,
    CONF_LAYOUT,
    CONF_MERGE_MODE,
    CONF_NOTIFY_SERVICE,
    CONF_OBSIDIAN_BASE,
    CONF_ONLY_AREAS,
    CONF_SCHEDULE_ENABLED,
    CONF_SCHEDULE_TIME,
    CONF_TYPE_MAP,
    DEFAULT_AUTO_UPDATE,
    DEFAULT_EXPORT_DIR,
    DEFAULT_FIELDS,
    DEFAULT_LAYOUT,
    DEFAULT_MERGE_MODE,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_OBSIDIAN_BASE,
    DEFAULT_SCHEDULE_ENABLED,
    DEFAULT_SCHEDULE_TIME,
    DOMAIN,
    EXTRA_PROTOCOL_OPTIONS,
    LAYOUT_AREA,
    LAYOUT_PROTOCOL,
    MERGE_MODE_CREATE_ONLY,
    MERGE_MODE_MERGE,
    OVERVIEW_ROOT_FILENAME,
    PROTOCOL_MAP,
    PROTOCOL_UNKNOWN,
    SERVICE_DOMAINS,
)
from .generator import (
    device_display_name,
    mirror_conflicts_with_export,
    normalize_order,
    order_prefill,
    selectable_field_options,
)

_LOGGER = logging.getLogger(__name__)

def _placeholder_links(language: str) -> dict[str, str]:
    """Fertige Anker für die Flow-Beschreibung (ICU erlaubt kein HTML im Template)."""
    note_label = "Notizen-Übersicht" if language == "de" else "Notes overview"
    zip_label = "ZIP-Datei" if language == "de" else "ZIP file"
    return {
        "notes_link": (
            f'<a href="/local/device_inventory_notes/{quote(OVERVIEW_ROOT_FILENAME)}" '
            f'target="_blank">{note_label}</a>'
        ),
        "zip_link": (
            f'<a href="/local/device_inventory_notes.zip" '
            f'target="_blank">{zip_label}</a>'
        ),
    }


def _schema_layout(current: dict) -> vol.Schema:
    """First step: folder layout."""
    return vol.All(
        vol.Schema(
            {
                vol.Required(
                    CONF_LAYOUT, default=current.get(CONF_LAYOUT, DEFAULT_LAYOUT)
                ): selector.SelectSelector(
                    {
                        "options": [
                            selector.SelectOptionDict(value=LAYOUT_PROTOCOL, label="Ordner pro Protokoll"),
                            selector.SelectOptionDict(value=LAYOUT_AREA, label="Ordner pro Bereich"),
                        ]
                    }
                ),
            }
        ),
        extra=vol.PREVENT_EXTRA,
    )


def _schema_fields(current: dict) -> vol.Schema:
    """Second step: selectable fields (including hand-maintained ones)."""
    return vol.All(
        vol.Schema(
            {
                vol.Optional(
                    CONF_FIELDS,
                    default=current.get(CONF_FIELDS) or list(DEFAULT_FIELDS),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                        options=[
                            selector.SelectOptionDict(value=field, label=field)
                            for field in selectable_field_options(current.get(CONF_FIELDS))
                        ],
                    )
                ),
            }
        ),
        extra=vol.PREVENT_EXTRA,
    )


def _schema_order(current: dict) -> vol.Schema:
    """Order step: multiline text field, one field per line, line order = note order."""
    return vol.All(
        vol.Schema(
            {
                vol.Optional(
                    CONF_FIELD_ORDER,
                    default=order_prefill(current),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        ),
        extra=vol.PREVENT_EXTRA,
    )


def _candidate_domains(hass) -> dict[str, int]:
    """Count devices per integration domain that currently resolve to no protocol.

    Mirrors the generator's filter chain: service/meta devices, unnamed
    devices, infrastructure (via_device) and devices without manufacturer/model
    are excluded, as are devices that already get a protocol (PROTOCOL_MAP or
    the zigbee2mqtt MQTT special case).
    """
    devreg = dr.async_get(hass)
    entry_domain = {e.entry_id: e.domain for e in hass.config_entries.async_entries()}

    children_by_parent: dict[str, list[str]] = {}
    for dev in devreg.devices:
        if dev.via_device_id:
            children_by_parent.setdefault(dev.via_device_id, []).append(dev.id)

    known = set(PROTOCOL_MAP) | set(SERVICE_DOMAINS)
    counts: dict[str, int] = {}
    for dev in devreg.devices:
        if dev.entry_type == dr.DeviceEntryType.SERVICE:
            continue
        if any(ident and ident[0] in SERVICE_DOMAINS for ident in dev.identifiers):
            continue
        if not device_display_name(dev):
            continue
        if children_by_parent.get(dev.id):
            continue
        if not dev.manufacturer and not dev.model:
            continue
        domains_found: set[str] = set()
        has_zigbee_mqtt = False
        entry_id = dev.config_entry_id
        domain = entry_domain.get(entry_id) if entry_id else None
        if domain:
            domains_found.add(domain)
        for ident in dev.identifiers:
            if not ident:
                continue
            domains_found.add(ident[0])
            value = ident[1] if len(ident) > 1 else None
            if ident[0] == "mqtt" and (
                value == "zigbee2mqtt_bridge"
                or (isinstance(value, str) and value.startswith("zigbee2mqtt_"))
            ):
                has_zigbee_mqtt = True
        if not domains_found:
            continue
        if has_zigbee_mqtt or domains_found & known:
            continue
        for domain in domains_found:
            counts[domain] = counts.get(domain, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _schema_extra(current: dict, candidates: dict[str, int]) -> vol.Schema:
    """Step for adding/removing user-configured protocol mappings."""
    extra_map = dict(current.get(CONF_EXTRA_MAP) or {})
    available = [
        selector.SelectOptionDict(value=domain, label=f"{domain} ({count} Geräte)")
        for domain, count in candidates.items()
        if domain not in extra_map
    ]
    already = [
        selector.SelectOptionDict(value=domain, label=domain)
        for domain in sorted(extra_map)
    ]
    schema: dict = {}
    if available:
        schema[vol.Optional(CONF_EXTRA_DOMAINS, default=[])] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                multiple=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
                options=available,
            )
        )
    if already:
        schema[vol.Optional(CONF_EXTRA_REMOVE, default=[])] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                multiple=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
                options=already,
            )
        )
    schema[vol.Optional(CONF_EXTRA_PROTOCOL, default=PROTOCOL_UNKNOWN)] = (
        selector.SelectSelector(
            {
                "options": [
                    selector.SelectOptionDict(value=proto, label=proto)
                    for proto in EXTRA_PROTOCOL_OPTIONS
                ]
            }
        )
    )
    return vol.All(vol.Schema(schema), extra=vol.PREVENT_EXTRA)


def _schema_settings(current: dict) -> vol.Schema:
    """Third step: everything else."""
    return vol.All(
        vol.Schema(
            {
                vol.Required(
                    CONF_EXPORT_DIR, default=current.get(CONF_EXPORT_DIR, DEFAULT_EXPORT_DIR)
                ): selector.TextSelector(),
                vol.Required(
                    CONF_OBSIDIAN_BASE,
                    default=current.get(CONF_OBSIDIAN_BASE, DEFAULT_OBSIDIAN_BASE),
                ): selector.TextSelector(),
                vol.Required(
                    CONF_MERGE_MODE, default=current.get(CONF_MERGE_MODE, DEFAULT_MERGE_MODE)
                ): selector.SelectSelector(
                    {
                        "options": [
                            selector.SelectOptionDict(
                                value=MERGE_MODE_MERGE, label="Vorhandene aktualisieren (handgepflegte Felder behalten)"
                            ),
                            selector.SelectOptionDict(value=MERGE_MODE_CREATE_ONLY, label="Nur neue Notizen anlegen"),
                        ]
                    }
                ),
                vol.Required(
                    CONF_AUTO_UPDATE, default=current.get(CONF_AUTO_UPDATE, DEFAULT_AUTO_UPDATE)
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_SCHEDULE_ENABLED,
                    default=current.get(CONF_SCHEDULE_ENABLED, DEFAULT_SCHEDULE_ENABLED),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_SCHEDULE_TIME,
                    default=current.get(CONF_SCHEDULE_TIME, DEFAULT_SCHEDULE_TIME),
                ): selector.TimeSelector(),
                vol.Optional(
                    CONF_NOTIFY_SERVICE,
                    default=current.get(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE),
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_IGNORED_DEVICES,
                    default=current.get(CONF_IGNORED_DEVICES, ""),
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
                vol.Optional(
                    CONF_FORCE_INCLUDE,
                    default=current.get(CONF_FORCE_INCLUDE, ""),
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
                vol.Optional(
                    CONF_ONLY_AREAS,
                    default=current.get(CONF_ONLY_AREAS, ""),
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
                vol.Optional(
                    CONF_TYPE_MAP,
                    default=current.get(CONF_TYPE_MAP, ""),
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
                vol.Optional(
                    CONF_DEVICE_TYPE_MAP,
                    default=current.get(CONF_DEVICE_TYPE_MAP, ""),
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            }
        ),
        extra=vol.PREVENT_EXTRA,
    )


def _settings_errors(hass, user_input: dict) -> dict[str, str]:
    """Reject an export directory the www mirror would delete.

    The mirror is rebuilt by clearing <config>/www/device_inventory_notes, so
    exporting there (or below it) would make the run destroy its own notes.
    """
    export_dir = (user_input or {}).get(CONF_EXPORT_DIR)
    if export_dir and mirror_conflicts_with_export(hass.config.config_dir, export_dir):
        return {"base": "export_dir_in_www"}
    return {}


class DeviceInventoryNotesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Device Inventory Notes."""

    VERSION = 1

    def __init__(self) -> None:
        """Set up instance variables."""
        self._flow_data: dict = {}

    @staticmethod
    def async_get_options_flow(config_entry) -> DeviceInventoryNotesOptionsFlow:
        """Return the options flow for this config entry."""
        return DeviceInventoryNotesOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._flow_data.update(user_input)
            return await self.async_step_fields()
        return self.async_show_form(
            step_id="user",
            data_schema=_schema_layout(self._flow_data),
            errors=errors,
            description_placeholders=_placeholder_links(self.hass.config.language),
        )

    async def async_step_fields(self, user_input=None):
        if user_input is not None:
            self._flow_data.update(user_input)
            return await self.async_step_order()
        return self.async_show_form(
            step_id="fields",
            data_schema=_schema_fields(self._flow_data),
        )

    async def async_step_order(self, user_input=None):
        if user_input is not None:
            keys, unknown = normalize_order(user_input.get(CONF_FIELD_ORDER) or "")
            if unknown:
                # Kein Formularfehler: der Prefill bietet selbst Zeilen an, die
                # keinem bekannten Feld entsprechen (handgepflegte Felder). Ein
                # Fehler würde das Speichern blockieren und genau diese Felder
                # unwiederbringlich entfernen.
                _LOGGER.warning(
                    "Reihenfolge: Zeile ohne bekanntes Feld, bleibt ohne Wirkung: %s",
                    ", ".join(unknown),
                )
            self._flow_data[CONF_FIELD_ORDER] = keys
            return await self.async_step_settings()
        return self.async_show_form(
            step_id="order",
            data_schema=_schema_order(self._flow_data),
        )

    async def async_step_settings(self, user_input=None):
        if user_input is not None:
            errors = _settings_errors(self.hass, user_input)
            if errors:
                return self.async_show_form(
                    step_id="settings",
                    data_schema=_schema_settings(self._flow_data),
                    errors=errors,
                )
            self._flow_data.update(user_input)
            return self.async_create_entry(title="Device Inventory Notes", data=self._flow_data)
        return self.async_show_form(
            step_id="settings",
            data_schema=_schema_settings(self._flow_data),
        )


class DeviceInventoryNotesOptionsFlow(config_entries.OptionsFlow):
    """Handle options for an existing config entry."""

    def __init__(self) -> None:
        """Set up instance variables."""
        self._flow_data: dict = {}

    def _current(self) -> dict:
        return {**self.config_entry.data, **self.config_entry.options}

    async def async_step_init(self, user_input=None):
        self._flow_data = self._current()
        if user_input is not None:
            self._flow_data.update(user_input)
            return await self.async_step_fields()
        return self.async_show_form(
            step_id="init",
            data_schema=_schema_layout(self._flow_data),
            description_placeholders=_placeholder_links(self.hass.config.language),
        )

    async def async_step_fields(self, user_input=None):
        if user_input is not None:
            self._flow_data.update(user_input)
            return await self.async_step_order()
        return self.async_show_form(
            step_id="fields",
            data_schema=_schema_fields(self._flow_data),
        )

    async def async_step_order(self, user_input=None):
        if user_input is not None:
            keys, unknown = normalize_order(user_input.get(CONF_FIELD_ORDER) or "")
            if unknown:
                # Kein Formularfehler: der Prefill bietet selbst Zeilen an, die
                # keinem bekannten Feld entsprechen (handgepflegte Felder). Ein
                # Fehler würde das Speichern blockieren und genau diese Felder
                # unwiederbringlich entfernen.
                _LOGGER.warning(
                    "Reihenfolge: Zeile ohne bekanntes Feld, bleibt ohne Wirkung: %s",
                    ", ".join(unknown),
                )
            self._flow_data[CONF_FIELD_ORDER] = keys
            return await self.async_step_extra()
        return self.async_show_form(
            step_id="order",
            data_schema=_schema_order(self._flow_data),
        )

    async def async_step_extra(self, user_input=None):
        if user_input is not None:
            extra_map = dict(self._flow_data.get(CONF_EXTRA_MAP) or {})
            protocol = user_input.get(CONF_EXTRA_PROTOCOL) or PROTOCOL_UNKNOWN
            for domain in user_input.get(CONF_EXTRA_DOMAINS, []):
                extra_map[domain] = protocol
            for domain in user_input.get(CONF_EXTRA_REMOVE, []):
                extra_map.pop(domain, None)
            self._flow_data[CONF_EXTRA_MAP] = extra_map
            return await self.async_step_settings()
        candidates = _candidate_domains(self.hass)
        if not candidates and not self._flow_data.get(CONF_EXTRA_MAP):
            return await self.async_step_settings()
        return self.async_show_form(
            step_id="extra",
            data_schema=_schema_extra(self._flow_data, candidates),
        )

    async def async_step_settings(self, user_input=None):
        if user_input is not None:
            errors = _settings_errors(self.hass, user_input)
            if errors:
                return self.async_show_form(
                    step_id="settings",
                    data_schema=_schema_settings(self._flow_data),
                    errors=errors,
                )
            # No notification here: Home Assistant calls async_update_options
            # after this returns, and that already tells the user to run the
            # scan. A second copy used to arrive with the same notification_id.
            self._flow_data.update(user_input)
            return self.async_create_entry(title="", data=self._flow_data)
        return self.async_show_form(
            step_id="settings",
            data_schema=_schema_settings(self._flow_data),
        )
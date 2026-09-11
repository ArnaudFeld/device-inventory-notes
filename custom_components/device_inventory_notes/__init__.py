"""Device Inventory Notes integration.

Exports device inventory data from the Home Assistant registries as
Obsidian-compatible Markdown notes with YAML frontmatter.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change

from .const import (
    CONF_AUTO_UPDATE,
    CONF_SCHEDULE_ENABLED,
    CONF_SCHEDULE_TIME,
    DEBOUNCE_SECONDS,
    DEFAULT_SCHEDULE_ENABLED,
    DEFAULT_SCHEDULE_TIME,
    DOMAIN,
    EVENT_AREA_REGISTRY_UPDATED,
    EVENT_DEVICE_REGISTRY_UPDATED,
    EVENT_ENTITY_REGISTRY_UPDATED,
    INITIAL_RUN_DELAY,
    SERVICE_SCAN,
)
from .generator import DeviceNoteGenerator, GenerateReport

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = []

SCAN_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("dry_run", default=False): cv.boolean,
        vol.Optional("device_id", default=None): vol.Any(None, cv.string),
    }
)


class DeviceInventoryRuntime:
    """Holds the generator and the debounced auto-update listeners."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        options = {**entry.data, **entry.options}
        self.generator = DeviceNoteGenerator(hass, options)
        self._lock = asyncio.Lock()
        self._debounce: asyncio.TimerHandle | None = None
        self._remove_listeners: list[Callable[[], None]] = []
        self._remove_schedule: Callable[[], None] | None = None

    def _current_entry(self) -> ConfigEntry:
        """Return the fresh entry so options changes are always picked up."""
        return self.hass.config_entries.async_get_entry(self.entry.entry_id) or self.entry

    def _current_options(self) -> dict:
        entry = self._current_entry()
        return {**entry.data, **entry.options}

    def reload_options(self) -> None:
        """Rebuild the generator from the current entry options."""
        self.generator = DeviceNoteGenerator(self.hass, self._current_options())

    @property
    def auto_update(self) -> bool:
        return bool(self._current_options().get(CONF_AUTO_UPDATE, True))

    async def run(
        self, dry_run: bool = False, device_id: str | None = None
    ) -> dict[str, Any]:
        async with self._lock:
            try:
                self.reload_options()
                report: GenerateReport = await self.generator.generate(
                    dry_run=dry_run, device_id=device_id
                )
            except Exception:
                _LOGGER.exception("Fehler beim Generieren der Geräte-Notizen")
                raise
        self._log_report(report)
        if not dry_run:
            await self._notify_changes(report)
        return report.to_dict()

    async def _notify_changes(self, report: GenerateReport) -> None:
        """Persistent notification after a run, only when something changed.

        The notification_id is stable, so a new run replaces the previous
        summary instead of stacking up notifications.
        """
        created = report.changed_created
        updated = report.changed_updated
        renamed = report.changed_renamed
        removed = report.changed_removed
        if not (created or updated or renamed or removed):
            return

        if self.hass.config.language == "de":
            title = "Geräte-Inventar aktualisiert"
            if created:
                line_new = f"<b>Neu:</b> {len(created)}"
            else:
                line_new = ""
            if updated:
                line_updated = f"<b>Geändert:</b> {len(updated)}"
            else:
                line_updated = ""
            if renamed:
                line_renamed = f"<b>Umbenannt:</b> {len(renamed)}"
            else:
                line_renamed = ""
            if removed:
                line_removed = f"<b>Entfernt:</b> {len(removed)}"
            else:
                line_removed = ""
            details = []
            for path in created[:5]:
                details.append(f"  • + {path}")
            for old_path, new_path in renamed[:5]:
                details.append(f"  • ↔ {old_path} → {new_path}")
            for path in updated[:5]:
                details.append(f"  • ~ {path}")
            for path in removed[:5]:
                details.append(f"  • - {path}")
            if len(created) + len(updated) + len(renamed) + len(removed) > 5:
                details.append("  • …")
            message = "<br>".join(
                part for part in (line_new, line_updated, line_renamed, line_removed) if part
            )
            if details:
                message += "<br>" + "<br>".join(details)
        else:
            title = "Device inventory updated"
            counts = [
                f"{len(created)} new",
                f"{len(updated)} updated",
                f"{len(renamed)} renamed",
                f"{len(removed)} removed",
            ]
            counts = [c for c in counts if not c.startswith("0 ")]
            message = ", ".join(counts)
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "notification_id": f"{DOMAIN}_run",
                "title": title,
                "message": message,
            },
        )

    def _log_report(self, report: GenerateReport) -> None:
        if report.dry_run:
            _LOGGER.info(
                "TROCKENLAUF: %d angelegt, %d aktualisiert, %d umbenannt, "
                "%d ohne Protokoll, %d Infrastruktur, %d ignoriert, "
                "%d Service, %d ohne Hersteller/Modell, %d Bereichs-gefiltert",
                len(report.created),
                len(report.updated),
                len(report.renamed),
                report.no_protocol_count,
                len(report.skipped_infra),
                len(report.skipped_ignored),
                len(report.skipped_service),
                len(report.skipped_unidentified),
                len(report.skipped_area),
            )
        else:
            _LOGGER.info(
                "Fertig: %d neu, %d aktualisiert, %d umbenannt, "
                "%d ohne Protokoll, %d Infrastruktur, %d ignoriert, "
                "%d Service, %d ohne Hersteller/Modell, %d Bereichs-gefiltert, "
                "%d verwaiste Notizen gelöscht, %d geändert seit letztem Lauf",
                len(report.created),
                len(report.updated),
                len(report.renamed),
                report.no_protocol_count,
                len(report.skipped_infra),
                len(report.skipped_ignored),
                len(report.skipped_service),
                len(report.skipped_unidentified),
                len(report.skipped_area),
                len(report.removed_stale),
                len(report.changed_created)
                + len(report.changed_updated)
                + len(report.changed_renamed)
                + len(report.changed_removed),
            )
        if report.errors:
            _LOGGER.warning("%d Fehler beim Generieren", len(report.errors))
            for error in report.errors[:10]:
                _LOGGER.warning("  %s", error)

    @callback
    def _on_registry_change(self, _event) -> None:
        if not self.auto_update:
            return
        if self._debounce is not None:
            self._debounce.cancel()
        self._debounce = self.hass.loop.call_later(
            DEBOUNCE_SECONDS, self._fire_debounced
        )

    @callback
    def _fire_debounced(self) -> None:
        self._debounce = None
        task = self.hass.async_create_task(self.run())
        task.add_done_callback(self._on_run_done)

    @callback
    def _on_run_done(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Auto-Update fehlgeschlagen")

    @callback
    def _on_scheduled_run(self, _now) -> None:
        task = self.hass.async_create_task(self.run())
        task.add_done_callback(self._on_run_done)

    @callback
    def setup_schedule(self) -> None:
        """(Re-)install the daily scheduled run from the current options.

        Independent of the registry-triggered auto-update: a safety net that
        catches changes missed while HA was restarting (or when auto-update
        is disabled). Called on setup and after every options change, since
        options changes do not reload the entry.
        """
        if self._remove_schedule is not None:
            self._remove_schedule()
            self._remove_schedule = None
        options = self._current_options()
        if not options.get(CONF_SCHEDULE_ENABLED, DEFAULT_SCHEDULE_ENABLED):
            return
        raw = options.get(CONF_SCHEDULE_TIME) or DEFAULT_SCHEDULE_TIME
        try:
            hour_str, minute_str, *_ = str(raw).split(":")
            hour, minute = int(hour_str), int(minute_str)
        except ValueError:
            _LOGGER.warning("Ungültige schedule_time %r, Zeitplan deaktiviert", raw)
            return
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            _LOGGER.warning("Ungültige schedule_time %r, Zeitplan deaktiviert", raw)
            return
        self._remove_schedule = async_track_time_change(
            self.hass, self._on_scheduled_run, hour=hour, minute=minute, second=0
        )
        _LOGGER.info("Geplanter Tageslauf aktiv: %02d:%02d", hour, minute)

    @callback
    def setup_listeners(self) -> None:
        self._remove_listeners.append(
            self.hass.bus.async_listen(EVENT_DEVICE_REGISTRY_UPDATED, self._on_registry_change)
        )
        self._remove_listeners.append(
            self.hass.bus.async_listen(EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry_change)
        )
        self._remove_listeners.append(
            self.hass.bus.async_listen(EVENT_AREA_REGISTRY_UPDATED, self._on_registry_change)
        )

    @callback
    def shutdown(self) -> None:
        if self._debounce is not None:
            self._debounce.cancel()
        if self._remove_schedule is not None:
            self._remove_schedule()
            self._remove_schedule = None
        for remove in self._remove_listeners:
            remove()
        self._remove_listeners.clear()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    runtime = DeviceInventoryRuntime(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    async def _handle_scan(call: ServiceCall) -> dict[str, Any] | None:
        report = await runtime.run(
            dry_run=bool(call.data.get("dry_run", False)),
            device_id=call.data.get("device_id") or None,
        )
        if call.return_response:
            return report
        return None

    hass.services.async_register(
        DOMAIN,
        SERVICE_SCAN,
        _handle_scan,
        schema=SCAN_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    runtime.setup_listeners()
    runtime.setup_schedule()
    if runtime.auto_update:

        async def _initial_run() -> None:
            await asyncio.sleep(INITIAL_RUN_DELAY)
            await runtime.run()

        task = hass.async_create_task(_initial_run())
        task.add_done_callback(runtime._on_run_done)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    runtime: DeviceInventoryRuntime = hass.data[DOMAIN].pop(entry.entry_id)
    runtime.shutdown()
    hass.services.async_remove(DOMAIN, SERVICE_SCAN)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rebuild the generator with the new options.

    Home Assistant calls this after an options-flow change instead of
    reloading the entry, so the new fields/order take effect immediately
    without a restart. A notification reminds the user to run the scan
    service, since options changes do not trigger a regeneration.
    """
    runtime: DeviceInventoryRuntime = hass.data[DOMAIN][entry.entry_id]
    runtime.reload_options()
    runtime.setup_schedule()
    service_name = f"{DOMAIN}.{SERVICE_SCAN}"
    if hass.config.language == "de":
        message = (
            "Die Einstellungen wurden gespeichert. Bitte führe jetzt den Dienst "
            f'<a href="/config/tools/action?service={service_name}">'
            f"device_inventory_notes.scan_and_generate</a> aus, "
            "damit die Notizen mit den neuen Einstellungen aktualisiert werden."
        )
    else:
        message = (
            "The settings were saved. Please run the service "
            f'<a href="/config/tools/action?service={service_name}">'
            f"device_inventory_notes.scan_and_generate</a> now, "
            "so the notes are regenerated with the new settings."
        )
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "notification_id": f"{DOMAIN}_options_changed",
            "title": "Device Inventory Notes",
            "message": message,
        },
    )
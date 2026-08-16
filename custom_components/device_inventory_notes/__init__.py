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

from .const import (
    CONF_AUTO_UPDATE,
    DEBOUNCE_SECONDS,
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
    {vol.Optional("dry_run", default=False): cv.boolean}
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

    async def run(self, dry_run: bool = False) -> dict[str, Any]:
        async with self._lock:
            try:
                self.reload_options()
                report: GenerateReport = await self.generator.generate(dry_run=dry_run)
            except Exception:
                _LOGGER.exception("Fehler beim Generieren der Geräte-Notizen")
                raise
        self._log_report(report)
        return report.to_dict()

    def _log_report(self, report: GenerateReport) -> None:
        if report.dry_run:
            _LOGGER.info(
                "TROCKENLAUF: %d angelegt, %d aktualisiert, %d umbenannt, "
                "%d ohne Protokoll, %d Infrastruktur, %d ignoriert, "
                "%d Service, %d ohne Hersteller/Modell",
                len(report.created),
                len(report.updated),
                len(report.renamed),
                report.no_protocol_count,
                len(report.skipped_infra),
                len(report.skipped_ignored),
                len(report.skipped_service),
                len(report.skipped_unidentified),
            )
        else:
            _LOGGER.info(
                "Fertig: %d neu, %d aktualisiert, %d umbenannt, "
                "%d ohne Protokoll, %d Infrastruktur, %d ignoriert, "
                "%d Service, %d ohne Hersteller/Modell, %d verwaiste Notizen gelöscht",
                len(report.created),
                len(report.updated),
                len(report.renamed),
                report.no_protocol_count,
                len(report.skipped_infra),
                len(report.skipped_ignored),
                len(report.skipped_service),
                len(report.skipped_unidentified),
                len(report.removed_stale),
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
        for remove in self._remove_listeners:
            remove()
        self._remove_listeners.clear()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    runtime = DeviceInventoryRuntime(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    async def _handle_scan(call: ServiceCall) -> dict[str, Any] | None:
        report = await runtime.run(dry_run=bool(call.data.get("dry_run", False)))
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
    without a restart.
    """
    runtime: DeviceInventoryRuntime = hass.data[DOMAIN][entry.entry_id]
    runtime.reload_options()
# Device Inventory Notes

Erzeugt aus den Home Assistant Registries Markdown-Notizen mit YAML-Frontmatter –
eine Notiz pro Gerät, passend zum bestehenden Inventar-System der Obsidian-Vault
(Ordner wie `Zigbee/`, `Wifi/`, `Bluetooth/`, `HomematicIP/` mit Dataview-Übersichten).

## Was die Integration tut

- Liest Geräte-, Entitäts- und Bereichs-Registries sowie die Konfigurations-Einträge.
- Leitet daraus pro Gerät ab: `name`, `hersteller`, `modell`, `protokoll`, `typ`
  (aus den Entitäts-Domains) und die Geräte-Kennung (`ha_device_id`, bei Zigbee
  `ieee_address` + `friendly_name`).
- Schreibt Notizen als
  `<export_dir>/<protokoll oder Bereich>/<gerätename>.md`.
- **Handfelder werden nie überschrieben**: `lagerort`, `menge`, `kaufdatum`,
  `preis`, `gekauft_bei`, `garantie_bis`, `notiz` – ebenso `typ`
  (nur beim ersten Anlegen vorbefüllt, danach bleibt deine Korrektur erhalten).
- `/local/device_inventory_notes.zip` ist die letzte Notizen-Generierung
  (jeder Lauf spiegelt die aktuelle Datei nach www).
- Erkennt Umbenennungen: Wurde ein Gerät in HA umbenannt, wird die Notiz
  umbenannt (Handfelder gehen nicht verloren) und das Frontmatter-`name`-Feld
  folgt der Änderung mit.
- Erkennt Infrastruktur automatisch (via `via_device`-Referenzen): Koordinator,
  Bridge, Power-Strip-Hauptgerät, Netzwerk-Receiver usw. werden übersprungen.
- Filter: Geräte ohne Hersteller UND Modell (z. B. BLE-Tracker ohne eigene
  Identität) werden als `skipped_unidentified` übersprungen; Geräte ohne
  bekannte Protokoll-Zuordnung landen in `skipped_no_protocol_count`.
- Automatische Aktualisierung: 10 Sekunden nach jeder Änderung an Geräte-,
  Entitäts- oder Bereichs-Registry wird neu generiert (mit Debounce).
- Nur Bereiche: Beschränkt die Notizen auf Geräte in bestimmten Bereichen
  (Bereichsname oder Namensbestandteil, eine je Zeile). Geräte in anderen
  Bereichen werden als `skipped_area` übersprungen – `Trotzdem erstellen`
  gewinnt immer.
- Typ-Labels überschreiben: `domain: Label`-Zeilen (z. B. `light: Lampe`)
  überschreiben bzw. ergänzen die eingebaute Typ-Zuordnung.
- Änderungsprotokoll: Nach jedem Lauf wird `CHANGELOG.md` im Export-Verzeichnis
  aktualisiert (die letzten 20 Läufe), basierend auf einem Vergleich zum
  vorherigen Stand (`.din_last_state.json`). Es werden nur echte Änderungen
  protokolliert: neu, geändert, umbenannt, entfernt.
- Benachrichtigung nach dem Lauf: Wenn sich seit dem letzten Lauf wirklich
  etwas geändert hat, erscheint eine ersetzbare Benachrichtigung mit einer
  Zusammenfassung. Der erste Lauf nach der Einrichtung etabliert nur den
  Ausgangsstand und meldet nichts.

## Einrichtung

1. Ordner `custom_components/device_inventory_notes` nach
   `<config>/custom_components/` kopieren und Home Assistant neu starten
   (oder das ZIP-Archiv im Repository verwenden).
2. Einstellungen → Geräte & Dienste → Integration hinzufügen →
   **Device Inventory Notes**.
3. Nach der Einrichtung den Trockenlauf testen:
   `Service aufrufen → device_inventory_notes.scan_and_generate` mit
   `dry_run: true` prüft, welche Dateien entstehen würden.
4. Auf Wunsch automatisch: Die Option *Automatisch aktualisieren* erzeugt
   Notizen 10 Sekunden nach jeder Geräte-/Entitätsänderung (mit Debounce).

## Optionen

| Option | Werte | Bedeutung |
|---|---|---|
| Export-Verzeichnis | relativ zu `<config>` oder absolut | Standard: `device_inventory_notes` |
| Ordnerstruktur | `Ordner pro Protokoll` / `Ordner pro Bereich` | Standard: nach Protokoll (Zigbee, WiFi, …) |
| Merge-Modus | `Vorhandene aktualisieren` / `Nur neue Notizen` | Standard: aktualisieren |
| Automatisch aktualisieren | an/aus | Standard: an |
| Geplanter Tageslauf | an/aus + Uhrzeit | Standard: aus – Sicherheitsnetz (fängt verpasste Änderungen auf, auch bei ausgeschaltetem Auto-Update) |
| Feldauswahl | mehrere Felder wählbar | Welche HA-Felder in Notizen geschrieben werden (`name` und `ha_device_id` sind immer dabei) |
| Ignorierte Geräte | Geräte-ID oder Namensbestandteil, eine je Zeile | Geräte werden übersprungen |
| Trotzdem erstellen | Geräte-ID oder Namensbestandteil, eine je Zeile | erzwingt Mitnahme (überschreibt Infrastruktur- und Identitäts-Filter) |
| Nur Bereiche | Bereichsname oder Namensbestandteil, eine je Zeile | nur Geräte in diesen Bereichen (leer = alle) |
| Typ-Labels | `domain: Label`, eine je Zeile | überschreibt/ergänzt die eingebaute Typ-Zuordnung (z. B. `light: Lampe`) |

## Befehl / Service

`device_inventory_notes.scan_and_generate` – Felder:

- `dry_run` (boolean, optional): nur analysieren und Bericht liefern, nichts schreiben.
- `device_id` (string, optional): nur dieses Gerät verarbeiten (`ha_device_id` aus
  der Notiz); leer = alle Geräte. Im Einzel-Modus werden keine verwaisten Notizen
  gelöscht, CHANGELOG/Notification/Snapshot laufen normal. Kombinierbar mit
  `dry_run` (Vorschau für eine Notiz).

Rückgabe: Liste der erzeugten (`created`), aktualisierten (`updated`),
umbenannten (`renamed`), übersprungenen Dateien (`skipped_infra`,
`skipped_ignored`, `skipped_unidentified`, `skipped_area`) sowie Fehler
(`errors`), verwaiste Notizen (`orphaned`) und die Änderungen seit dem
letzten Lauf (`changed_created`, `changed_updated`, `changed_renamed`,
`changed_removed`).

## Protokoll-Zuordnung

Die Zuordnung passiert über die Integration des Konfigurations-Eintrags bzw.
der Geräte-Identifikatoren – z. B. `shelly`, `apple_tv`, `tuya` → `WiFi`;
`fritzbox` → `DECT`; `homematicip_local` → `HomematicIP`; `bluetooth`,
`switchbot`, `xiaomi_ble`, … → `Bluetooth`; `zha`, `zigbee2mqtt` → `Zigbee`;
`matter` → `Matter`. Geräte ohne bekannte Zuordnung werden übersprungen.

## Notiz-Format

```yaml
---
name: "Wohnzimmer Lampe"
typ: "Lampe"
hersteller: "IKEA of Sweden"
modell: "TRADFRI bulb E27"
protokoll: "Zigbee"
lagerort: ""
menge: "1"
kaufdatum: ""
preis: ""
gekauft_bei: ""
garantie_bis: ""
friendly_name: "Wohnzimmer Lampe"
ieee_address: "04:cd:15:…"
ha_device_id: "…"
notiz: ""
---
```

## Entwicklung

Keine externen Abhängigkeiten; reine Python-asyncio-Integration.
Syntax-Check: `python3 -m py_compile custom_components/device_inventory_notes/*.py`

## Lizenz

MIT – siehe [LICENSE](LICENSE).
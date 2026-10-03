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
- **Graph-Anbindung**: Jede Notiz trägt ein `übersicht`-Feld mit Wikilink auf
  ihre Protokoll-Übersicht (z. B. `übersicht: "[[01-Übersicht Zigbee]]"`),
  damit Aktoren im Obsidian-Graphen mit ihrer Übersicht verbunden sind
  (Dataview-Tabellen allein erzeugen keine Graph-Kanten). Die
  Protokoll-Übersichten verlinken zurück auf `01-Übersicht Aktoren`.
  Generator-verwaltet wie `name` (folgt Protokoll-Wechseln automatisch);
  fehlende oder leere Werte werden ergänzt, vorhandene Werte bleiben erhalten.
  Ein vorhandenes `typ` bleibt erhalten; ein leerer Wert kann ergänzt werden.
- **Handfelder werden nie überschrieben**: `lagerort`, `menge`, `kaufdatum`,
  `preis`, `gekauft_bei`, `garantie_bis`, `notiz`.
- **Metadatenkern**:
  - `note_type: "actor"` kennzeichnet die Notiz als Aktor-Notiz. Das Feld wird
    bei neuen Notizen gesetzt und bei älteren Notizen migriert.
  - `entity_type` enthält, wenn eindeutig klassifizierbar, den technischen
    Home-Assistant-Entity-Typ in Kleinschreibung, zum Beispiel `light` oder
    `sensor`. Bei mehreren konkurrierenden Typen bleibt das Feld weg.
  - `updated` ist das Datum `YYYY-MM-DD` der letzten fachlichen Änderung. Ein
    unveränderter Scan, eine reine Umbenennung und die alleinige Migration
    der Metadaten ändern das Datum nicht. Es wird nicht aus Dateisystemzeiten
    oder dem CHANGELOG abgeleitet.
  - `typ` bleibt das fachliche beziehungsweise Anzeigefeld und wird nicht in
    `entity_type` umbenannt. Benutzerkorrekturen bleiben erhalten.
  - Ein `status`-Feld wird für Aktoren vorerst nicht eingeführt.
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
- Geräte-Typen überschreiben: `Fragment: Label`-Zeilen werden zuerst gegen die
  exakte Geräte-ID, dann als Teiltreffer (Groß/Kleinschreibung egal) gegen
  Gerätename und Modell geprüft – erste Trefferzeile gewinnt. So bekommen auch
  Geräte ohne ableitbare Domains einen Typ (z. B. `Weihnachtsbaum:
  Lichterkette` für einen Twinkly-String ohne Entities, `MFC: Drucker`,
  `esp32-c3: BLE-Proxy`). Gilt wie die Inferenz nur für leere Typen –
  auch bei Updates (bestehende Werte bleiben unantastbar, auch Hand-Einträge).

Die eingebaute Domain-Tabelle kennt `climate`→Klima, `cover`→Rolladen,
`fan`→Lüfter, `light`→Lampe, `lock`→Schloss, `media_player`→Lautsprecher,
`vacuum`→Staubsauger, `camera`→Kamera, `humidifier`→Luftbefeuchter,
`valve`→Ventil, `lawn_mower`→Mähroboter, `siren`→Sirene,
`water_heater`→Warmwasserbereiter, `remote`→Fernbedienung und
`switch`→Steckdose (absichtlich Letzter, weil Schalter oft nur Zweitfunktion
sind). Rein messende Geräte fallen auf `Sensor` zurück; ohne ableitbare
Domains bleibt das Feld leer. Reihenfolge insgesamt: Hand-Eintrag >
Geräte-Override > Domain-Mapping > eingebaute Tabelle > Sensor > leer.
- Änderungsprotokoll: Nach jedem Lauf wird `CHANGELOG.md` im Export-Verzeichnis
  aktualisiert (die letzten 20 Läufe), basierend auf einem Vergleich zum
  vorherigen Stand (`.din_last_state.json`). Es werden nur echte Änderungen
  protokolliert, gruppiert mit Zählern: **Neu**, **Umbenannt**, **Geändert**,
  **Entfernt**.
- Benachrichtigung nach dem Lauf: Wenn sich seit dem letzten Lauf wirklich
  etwas geändert hat, erscheint eine ersetzbare Benachrichtigung mit einer
  Zusammenfassung. Der erste Lauf nach der Einrichtung etabliert nur den
  Ausgangsstand und meldet nichts.

## Installation

Über HACS:

1. HACS öffnen → *Integrationen* → Drei-Punkte-Menü →
   *Eigene Integration hinzufügen*
2. `ArnaudFeld/device-inventory-notes` als Repository angeben
3. *Device Inventory Notes* installieren und Home Assistant neu starten

Ohne HACS: `custom_components/device_inventory_notes` nach
`<config>/custom_components/` kopieren (alternativ das ZIP-Archiv aus dem
Release entpacken) und Home Assistant neu starten.

Benötigt Home Assistant 2024.3 oder neuer.

## Einrichtung

1. Einstellungen → Geräte & Dienste → Integration hinzufügen →
   **Device Inventory Notes**.
2. Im Konfigurationsdialog Export-Verzeichnis und Obsidian-Basispfad setzen,
   Felder auswählen, Reihenfolge festlegen, Zeitplan optional.
3. Nach der Einrichtung den Trockenlauf testen:
   `Service aufrufen → device_inventory_notes.scan_and_generate` mit
   `dry_run: true` prüft, welche Dateien entstehen würden.
4. Auf Wunsch automatisch: Die Option *Automatisch aktualisieren* erzeugt
   Notizen 10 Sekunden nach jeder Geräte-/Entitätsänderung (mit Debounce).

## Optionen

| Option | Werte | Bedeutung |
|---|---|---|
| Export-Verzeichnis | relativ zu `<config>` oder absolut (z. B. `/share/device_inventory_notes`, siehe unten) | Standard: `device_inventory_notes` |
| Ordnerstruktur | `Ordner pro Protokoll` / `Ordner pro Bereich` | Standard: nach Protokoll (Zigbee, WiFi, …) |
| Merge-Modus | `Vorhandene aktualisieren` / `Nur neue Notizen` | Standard: aktualisieren |
| Automatisch aktualisieren | an/aus | Standard: an |
| Geplanter Tageslauf | an/aus + Uhrzeit | Standard: aus – Sicherheitsnetz (fängt verpasste Änderungen auf, auch bei ausgeschaltetem Auto-Update) |
| Benachrichtigungs-Dienst | Dienstname, z. B. `notify.mobile_app_xyz` | leer = nur HA-interne Notification; wenn gesetzt, geht bei echten Änderungen zusätzlich ein Push raus |
| Feldauswahl | mehrere Felder wählbar | Welche HA-Felder in Notizen geschrieben werden (`name` und `ha_device_id` sind immer dabei) |
| Ignorierte Geräte | Geräte-ID oder Namensbestandteil, eine je Zeile | Geräte werden übersprungen; ihre Notizen werden beim nächsten Lauf gelöscht (Eintrag im CHANGELOG) |
| Trotzdem erstellen | Geräte-ID oder Namensbestandteil, eine je Zeile | erzwingt Mitnahme (überschreibt Infrastruktur- und Identitäts-Filter) |
| Nur Bereiche | Bereichsname oder Namensbestandteil, eine je Zeile | nur Geräte in diesen Bereichen (leer = alle) |
| Typ-Labels | `domain: Label`, eine je Zeile | überschreibt/ergänzt die eingebaute Typ-Zuordnung (z. B. `light: Lampe`) |
| Geräte-Typen | `Name`, `ID` oder `Modell: Label`, eine je Zeile | z. B. `Weihnachtsbaum: Lichterkette`, `MFC: Drucker`, `esp32-c3: BLE-Proxy` – greift vor der Domain-Logik, befüllt nur leere Typen |

## Export nach /share (z. B. für Obsidian per Samba)

Statt ins Konfigurations-Verzeichnis kann der Export auch auf die HAOS-Freigabe
`/share` geschrieben werden – dann liegen die Notizen direkt im per Samba/Netzwerk
erreichbaren `share`-Ordner (praktisch, wenn Obsidian auf einem anderen Rechner
läuft). Dazu als Export-Verzeichnis einfach einen absoluten Pfad eintragen:

`/share/device_inventory_notes`

Der www-Spiegel (`/local/…`-Links und ZIP) bleibt immer unter `<config>/www`,
nur Notizen, CHANGELOG und Snapshot wandern nach `/share`. Beim Wechsel des
Verzeichnisses etabliert der erste Lauf dort eine neue Baseline (kein CHANGELOG,
keine Notification beim ersten Mal); das alte Verzeichnis wird nicht
automatisch gelöscht.

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

## Ereignisse als Trigger

Jeder echte Lauf (manuell, Auto-Update, Zeitplan – nie Trockenläufe) feuert am
Ende `device_inventory_notes_scan_finished` mit Zählern (`created`, `updated`,
`renamed`, `changed_created`, `changed_updated`, `changed_renamed`,
`changed_removed`, `changed_total`, `errors`), `total_scanned`, `device_filter`
und `duration_seconds`. Schlägt ein Lauf fehl, feuert stattdessen
`device_inventory_notes_scan_failed` mit `error`, `device_filter` und
`duration_seconds`. Beispiel:

```yaml
trigger:
  - platform: event
    event_type: device_inventory_notes_scan_finished
condition:
  - condition: template
    value_template: "{{ trigger.event.data.changed_total > 0 }}"
action:
  - action: notify.mobile_app_xyz
    data:
      title: "Inventar aktualisiert"
      message: "{{ trigger.event.data.changed_total }} Änderungen in {{ trigger.event.data.duration_seconds }} s"
```

## Protokoll-Zuordnung

Die Zuordnung passiert über die Integration des Konfigurations-Eintrags bzw.
der Geräte-Identifikatoren – z. B. `shelly`, `apple_tv`, `tuya` → `WiFi`;
`fritzbox` → `DECT`; `homematicip_local` → `HomematicIP`; `bluetooth`,
`switchbot`, `xiaomi_ble`, … → `Bluetooth`; `zha`, `zigbee2mqtt` → `Zigbee`;
`matter` → `Matter`. Geräte ohne bekannte Zuordnung werden übersprungen.

## Notiz-Format

```yaml
---
note_type: "actor"
entity_type: "light"
updated: "2026-09-25"
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
Tests: `python3 -m unittest discover -s tests -v`

## Lizenz

MIT – siehe [LICENSE](LICENSE).
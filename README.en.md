![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.3%2B-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Tests](https://github.com/ArnaudFeld/device-inventory-notes/actions/workflows/tests.yml/badge.svg)

# Device Inventory Notes

**Language:** [Deutsch](README.md) | English

Home Assistant knows which devices exist. That information lives in the device,
entity and area registries, but there is no way to pull it out as a list you
could show to anyone. Maintaining an inventory usually means combining two
sources: the technical data from Home Assistant, and everything Home Assistant
cannot know, from memory.

This integration turns that into notes.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ArnaudFeld&repository=device-inventory-notes&category=integration)

![Actor index in Obsidian, counted by type](docs/uebersicht-dataview.png)

*The index page counts actors by type. The table is a Dataview query and
refreshes with every run.*

## Why it exists

For every device it writes one Markdown note with YAML frontmatter, filed by
protocol or by area. What Home Assistant knows goes into the note. What it does
not know stays empty and belongs to you.

These fields are never overwritten:

`lagerort` · `menge` · `kaufdatum` · `preis` · `gekauft_bei` · `garantie_bis` ·
`notiz` · `transport`

Everything else follows Home Assistant. Rename a device there and the note
follows, hand-maintained fields intact. Delete a device and its note goes with
it, recorded in the change log.

`transport` is the odd one out: it is not hand-maintained but derived for Matter
devices from their diagnostic entities, either `Thread` or `WiFi`. An existing
value is kept, an empty one is filled in. For every other protocol the field
stays empty.

## What you get

![Generated actor note in source mode with the frontmatter visible](docs/aktor-notiz.png)

*A generated note in source mode. At the top the fields from Home Assistant,
below them the hand-maintained fields the integration never touches.*

- **One note per device**, with name, manufacturer, model, protocol, type and
  device identifier. Zigbee devices also carry `ieee_address`.
- **Edges in the Obsidian graph.** Every note carries an `overview` field with a
  wikilink to its protocol overview. Dataview tables do not create graph edges
  on their own, this wikilink does.
- **Overview pages** per protocol plus one index page, with Dataview queries that
  sort by type, manufacturer and location.
- **A change log** covering the last 20 runs, grouped into *Created*,
  *Renamed*, *Changed* and *Removed*.
- **A notification** when something actually changed. The first run after setup
  only establishes the baseline and stays quiet.
- **A ZIP archive** at `/local/device_inventory_notes.zip`, plus an `/www` mirror
  to read through.

### What it does not do

The integration does not invent information. Where Home Assistant knows nothing,
the field stays empty, and `type` falls back to the protocol name because that is
the most honest label available. Devices with neither manufacturer nor model are
skipped, as are coordinators, bridges and other infrastructure.

## Requirements

- Home Assistant 2024.3 or newer
- For the overview pages: the Obsidian **Dataview** plugin. Without Dataview the
  integration still works, the overview pages just render empty query results.
- Obsidian itself is optional. The notes are plain Markdown files with
  frontmatter and read fine in any editor.

## Installation

<!-- The HACS badge links to the repository page. HACS cannot add a repository on
     its own, that happens once by hand. -->

### Via HACS

1. Open HACS, *Integrations*, three-dot menu, *Add custom repository*
2. Enter `ArnaudFeld/device-inventory-notes` as the repository
3. Install *Device Inventory Notes*
4. Restart Home Assistant

Once the repository is in HACS, the integration shows up under *Integrations*
and installs and updates without that extra step.

### Without HACS

Copy `custom_components/device_inventory_notes` into
`<config>/custom_components/`, or unpack the ZIP from the release there, then
restart Home Assistant.

## First steps

1. Settings → Devices & services → Add integration → **Device Inventory Notes**.
2. In the dialog set the export directory and the base path inside your vault.
3. Check the dry run before anything is written: Developer tools → *Perform
   action* → `device_inventory_notes.scan_and_generate` with `dry_run: true`.
   The report shows which files would be created.
4. Run the same service without `dry_run`. The overview pages are created along
   the way.
5. Confirm the notes arrive in your vault. The `export_dir_in_www` error appears
   when the export directory sits inside the www mirror, because the mirror would
   otherwise delete the tree it copies from.

## Options

| Option | Values | Meaning |
|---|---|---|
| Export directory | relative to `<config>` or absolute | Default: `device_inventory_notes` |
| Obsidian base path | path inside your vault | Default: `02 Home Assistant/Aktoren` |
| Folder structure | `Folder per protocol` / `Folder per area` | Default: by protocol |
| Merge mode | `Update existing` / `Only create new notes` | Default: update |
| Automatic updates | on/off | Default: on, 10 seconds after any registry change, debounced |
| Scheduled daily run | on/off and time | Default: off. A safety net for changes that get missed by the automatic updates |
| Notification service | service name, e.g. `notify.mobile_app_xyz` | empty = Home Assistant notification only, plus a push on real changes |
| Field selection | multiple fields | which Home Assistant fields end up in the notes, `name` and `ha_device_id` are always included |
| Ignored devices | device ID or name fragment, one per line | devices are skipped and their notes deleted on the next run |
| Force include | device ID or name fragment, one per line | forces inclusion, overrides the infrastructure and identity filters |
| Only areas | area name or fragment, one per line | restricts to devices in those areas, *Force include* always wins |
| Type labels | `domain: Label`, one per line | overrides the built-in type mapping, e.g. `light: Lamp` |
| Device types | `Name`, `ID` or `Model: Label`, one per line | applies before the domain logic and only fills empty types |

Note that the option names in the table are translated. The labels you see in
the dialog follow your Home Assistant language.

## Export to /share

Obsidian rarely runs on the same machine as Home Assistant. The HAOS `/share`
mount exists for that: enter an absolute path as the export directory and the
notes land in the folder reachable over Samba.

```
/share/device_inventory_notes
```

The www mirror with the `/local/` links and the ZIP always stays under
`<config>/www`; only the notes, the change log and the snapshot move to
`/share`. If you change the directory, the first run there establishes a new
baseline, which means no change log and no notification on that first run. The
previous directory is left untouched.

## Service

`device_inventory_notes.scan_and_generate`

| Field | Type | Meaning |
|---|---|---|
| `dry_run` | boolean | analyse and report only, write nothing |
| `device_id` | string | one device only, empty = all. In single-device mode no orphaned notes are deleted |

The response reports `created`, `updated`, `renamed`, `orphaned`, `errors`, the
skip lists (`skipped_infra`, `skipped_ignored`, `skipped_unidentified`,
`skipped_area`) and the changes since the previous run (`changed_created`,
`changed_updated`, `changed_renamed`, `changed_removed`).

## Events

Every real run fires `device_inventory_notes_scan_finished` when it is done,
with the counters, `total_scanned`, `device_filter` and `duration_seconds`. Dry
runs fire nothing. A failed run fires
`device_inventory_notes_scan_failed`.

```yaml
triggers:
  - trigger: event
    event_type: device_inventory_notes_scan_finished
  - condition: template
    value_template: "{{ trigger.event.data.changed_total > 0 }}"
actions:
  - action: notify.mobile_app_xyz
    data:
      title: "Inventory updated"
      message: "{{ trigger.event.data.changed_total }} changes in {{ trigger.event.data.duration_seconds | round(1) }} s"
```

## Type detection

Mapping runs through the device's config entry domain or its identifiers.
Examples: `shelly`, `apple_tv`, `tuya` → WiFi, `fritzbox` → DECT,
`homematicip_local` → HomematicIP, `zha`, `zigbee2mqtt` → Zigbee,
`matter` → Matter.

The device kind is additionally derived from the entity domains:

| Domain | Type | Domain | Type |
|---|---|---|---|
| `light` | Lamp | `camera` | Camera |
| `switch` | Plug | `humidifier` | Humidifier |
| `climate` | Climate | `valve` | Valve |
| `cover` | Blind | `lawn_mower` | Lawn mower |
| `fan` | Fan | `siren` | Siren |
| `lock` | Lock | `water_heater` | Water heater |
| `media_player` | Speaker | `remote` | Remote |
| `vacuum` | Vacuum | | |

`switch` sits at the end on purpose, because switches are often a secondary
function. Purely measuring devices come out as `Sensor`. When nothing can be
derived, the protocol name takes the field so `type` is never empty.

Overall precedence: `Device types` entry > `Type labels` > domain mapping >
table above > protocol name.

## Note format

The frontmatter keys are the German ones, unchanged, because they are what ends
up in your vault and what the Dataview queries match on. They are not
translated per note.

```yaml
---
typ: "Lamp"
hersteller: "IKEA of Sweden"
modell: "TRADFRI bulb E27"
protokoll: "Zigbee"
übersicht: "[[01-Übersicht Zigbee]]"
transport: ""
bereich: "Living room"
note_type: "actor"
entity_type: "light"
updated: "2026-09-25"
name: "Living room lamp"
friendly_name: "Living room lamp"
ieee_address: "04:cd:15:…"
ha_device_id: "…"
lagerort: ""
menge: ""
kaufdatum: ""
preis: ""
gekauft_bei: ""
garantie_bis: ""
notiz: ""
---
```

| Key | English |
|---|---|
| `name`, `friendly_name` | device name |
| `typ` | device kind, falls back to the protocol name |
| `hersteller`, `modell` | manufacturer, model |
| `protokoll` | protocol, decides the folder |
| `übersicht` | wikilink to the protocol overview |
| `bereich` | area |
| `note_type` | `actor` for device notes, `collection` for overviews |
| `entity_type` | Home Assistant entity domain, only when unambiguous |
| `updated` | date of the last substantive change |
| `lagerort` | location, yours |
| `menge` | quantity, yours |
| `kaufdatum`, `preis`, `gekauft_bei`, `garantie_bis` | purchase date, price, bought at, warranty until, yours |
| `notiz` | free-form notes, yours |
| `ieee_address`, `ha_device_id` | device identifiers |

The overview pages carry `note_type: collection` and an empty `aliases` field.

`updated` is the date of the last substantive change. A scan that changed
nothing, a pure rename and the metadata migration on its own all leave it alone.

## Development

No external dependencies, pure asyncio integration.

```
python3 -m unittest discover -s tests -v
```

The config flow tests need `voluptuous`, which is a test-only dependency.
Without it the test module skips itself.

## License

MIT, see [LICENSE](LICENSE).


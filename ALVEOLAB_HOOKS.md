# ALVEOLAB Farm: hooks into Bambuddy

Our code lives in `backend/app/alveolab/` (and `frontend/src/alveolab/`, later modules).
Every edit to an upstream Bambuddy file is listed here and nowhere else. If this list
grows, we are modifying Bambuddy again instead of hooking into it.

Base: Bambuddy v1.3.0. `ALVEOLAB_MODULES=0` skips the router and callbacks (`alveolab.setup`).

## Module: printer providers (Centauri 2 / ELEGOO CC2)

Code: `alveolab/printer_providers/` (base, bambu capabilities, `elegoo/*`), `alveolab/provider_jobs.py`
(print start/end of provider printers), `alveolab/dispatch.py` (queue dispatch), `alveolab/hooks.py`
(answers to the hook points), `alveolab/routes.py` (`/api/v1/printer-providers/*`),
`alveolab/migrations.py`, `alveolab/config.py`.

| # | File | Hook |
|---|------|------|
| 1 | `main.py` | import `alveolab` + one line `alveolab.setup(app, prefix)` (mounts router, registers job-event callback) |
| 2 | `core/database.py` | imports + `alveolab_migrations.run(conn, _safe_execute)` + `alveolab_models.load()` (in `init_db` and `load_all_models`, so create_all sees our tables) |
| 3 | `models/printer.py` | columns `provider` ("bambu" default), `bed_type` |
| 4 | `schemas/printer.py` | `provider` field, serial case kept for non-Bambu, access code optional for non-Bambu, `bed_type` + `BED_TYPES` |
| 5 | `services/printer_manager.py` | `_provider_clients`/`_providers` maps; `connect_printer` branches to `_connect_provider_printer`; `disconnect_*`, `get_status`, `get_all_statuses`, `is_connected` also see provider clients; `get_provider`, `get_provider_client`, `get_capabilities`, `set_provider_job_event_callback`. `get_client()` stays Bambu-only on purpose |
| 6 | `services/print_scheduler.py` | `_start_print` hands provider printers to `alveolab.dispatch.start_print`; idle check / hold use `cannot_take_queue_jobs`; stranded-item sweep reports "outcome not observed"; no AMS spool check or filament-deficit flag for provider printers |
| 7 | `api/routes/print_queue.py` | `add_to_queue` asks `hooks.validate_queue_item` (3MF refused for Centauri, wrong plate); `stop_queue_item` asks `hooks.stop_print` first |
| 8 | `api/routes/printers.py` | `create_printer` probes via `hooks.probe_new_printer`; stop/pause/resume ask the provider first; `/cover` is 404 for non-Bambu |

Frontend code: `frontend/src/alveolab/` (`api.ts` = our client + types, `printer-providers/*` = card parts,
add-printer modal, print modal, CANVAS panel; `locales/{en,it}.ts` = our strings; `__tests__/`).

| # | File | Hook |
|---|------|------|
| 9 | `api/client.ts` | `request` exported; `provider?`/`bed_type?` on `Printer`, `provider?` on `PrinterCreate`; type import from `alveolab/api` |
| 10 | `i18n/index.ts` | `withFarmStrings(lang, resource)` on the loaded locale and on bundled `en`. Our keys (`farm.*`) exist in en + it only, other languages fall back to English; `check:i18n` does not see them |
| 11 | `pages/PrintersPage.tsx` | `PrinterCard`: `usePrinterProvider`, Bambu-only sections gated on `printerProvider.isBambu`, `ProviderJobControls`, `CanvasPanel`, `ProviderFaultsBadge`, `ElegooPrintModal`; `AddPrinterModal`: `ProviderPicker` + `ElegooAddPrinterModal`; connect-error reason toast; bulk actions skip non-Bambu |
| 12 | `KProfilesView`, `PrintModal/index`, `RunWithPipelineModal`, `VirtualPrinterAddDialog`, `VirtualPrinterCard`, `spoolbuddy/AssignToAmsModal`, `CamWallPage` | printers query gets `select: bambuPrintersOnly` (3 lines each) |

## Module: production (recipes, stock, demand, plates to print)

Code: `alveolab/production/{models,schemas,services,routes}.py`, tables `production_*`.
API `/api/v1/production/*` unchanged (Jarvis uses it). Mounted by `alveolab.setup`; tables via
`alveolab/models.py` (hook 2). **No other hook.**

## Module: plates (build-plate) and G-code slicing for the Centauri 2

Code: `alveolab/plates/{builder,schemas,routes}.py`: **`POST /api/v1/alveolab/library/build-plate`**
(was `/library/files/build-plate`; Jarvis updated), `alveolab/library_hooks.py`.

| # | File | Hook |
|---|------|------|
| 13 | `api/routes/library.py` | `validate_print_file_upload` accepts raw G-code sliced for a Centauri; `upload_file` adds `sliced_for_model` of an uploaded G-code; `slice_and_persist` asks `persist_cc2_slice` (plain `.gcode` instead of 3MF); `slice_library_file` applies `with_printer_bed`; `_resolve_slice_destination` takes `managed_suffix` |
| 14 | `api/routes/archives.py` | `slice_archive` applies `with_printer_bed` |
| 15 | `schemas/slicer.py` | `SliceRequest.printer_id` |

## Module: prints outside the queue (live card, timeline bar, finished list)

Code: backend `alveolab/outside_queue.py` (`GET /api/v1/alveolab/queue/outside-prints`, finished prints
whose archive no queue row owns); frontend `alveolab/outside-queue/*` (live prints are read from the printer
statuses, no backend). Upstream history is server-paged since 1.3.0, so finished outside prints are a
separate list under it, not merged into it.

| # | File | Hook |
|---|------|------|
| 16 | `pages/QueuePage.tsx` | `useOutsideQueue(...)`; its count added to the Queue tab and stats; `OutsideQueuePrintCard`s in "Currently printing"; empty state waits for it; `externalBars` passed to the timeline; `<OutsideQueueHistory/>` under `HistorySection` |
| 17 | `components/QueueTimelineView.tsx` | `externalBars` prop: seeds each lane's chain (`seedExternalChains`), counts in "all done by" and the empty notice, renders `ExternalPrintBar` |

| 18 | `hooks/useWebSocket.ts` | no message (pong included) for 75 s = dead socket: drop it and reconnect (`WS_SILENCE_LIMIT_MS`) |
| 19 | `api/routes/makerworld.py` | `_map_service_error`: log every provider error; `ProviderUnavailableError` answers 424, not 502 (a Cloudflare tunnel replaces an origin 502 with its own page) |
| 20 | `main.tsx` | imports `alveolab/brand.css` after `index.css` (honey-yellow default accent, `.accent-alveolab`, dark text on filled accent) |
| 21 | `components/Layout.tsx`, `pages/LoginPage.tsx`, `pages/SetupPage.tsx` | logo and name from `alveolab/brand.ts` (one logo for light and dark) |
| 22 | `contexts/ThemeContext.tsx` | `'alveolab'` accent: type, default for dark and light, class list |
| 23 | `schemas/settings.py` | `dark_accent` / `light_accent` default `alveolab` |
| 24 | `frontend/index.html`, `public/manifest.json`, `public/img/{favicon*,apple-touch-icon,android-chrome-*}.png` | title, description, theme colour, icons; `alveolab_farm_logo.png` added |

## Not yet moved
Nothing: every fork change now lives in `alveolab/` or in a hook above.

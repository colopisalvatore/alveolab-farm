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
| 2 | `core/database.py` | import + one line `alveolab_migrations.run(conn, _safe_execute)` |
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

## Not yet moved (still on the fork, later modules)
production (B), plates (C), outside-queue + timeline (D), UI/branding (F), MakerWorld 424 + log (E).

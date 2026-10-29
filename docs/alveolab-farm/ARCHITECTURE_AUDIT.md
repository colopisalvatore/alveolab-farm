# ALVEOLAB Farm — Audit architetturale del fork Bambuddy (M0)

Data audit: 2026-10-29 · Fork: `colopisalvatore/alveolab-farm` · Base: upstream `maziggy/bambuddy` commit `98ecaf50` (22/10/2026), `APP_VERSION = "1.2.5.7"` (`backend/app/core/config.py:10`).

Tutte le informazioni sono ricavate dal codice del repository; i riferimenti `file:riga` sono relativi alla radice.

---

## 1. Stato del fork

| Voce | Esito |
|---|---|
| Divergenza da upstream | **0 commit avanti / 0 indietro** (`git rev-list --left-right --count origin/main...upstream/main` → `0 0`). Il fork è identico a upstream. |
| Remote `upstream` | Aggiunto in questa sessione: `https://github.com/maziggy/bambuddy.git`. |
| Lavori non committati | Solo `.gitignore` (righe per `.promptops/` e `.promptops-assets/`). **Preservato**, non toccato. |
| Personalizzazioni preesistenti | Nessuna nel codice. |
| Storia | 4.593 commit, CHANGELOG di 3.328 righe: progetto molto attivo (rilasci frequenti, molti fix numerati `#NNNN`). |

**Conseguenza:** il costo di merge da upstream cresce con ogni modifica a file "caldi" (`main.py`, `printer_manager.py`, `bambu_mqtt.py`, `print_scheduler.py`, `routes/printers.py`). Le modifiche ALVEOLAB devono stare preferibilmente in **file nuovi** e toccare quelli esistenti con patch piccole e localizzate.

## 2. Licenza

- `LICENSE`: **GNU AGPL-3.0**. Va mantenuta; vanno preservati avvisi di copyright e attribuzione (README, footer UI, `SECURITY.md`, `BACKERS.md`).
- AGPL §13: se l'app modificata viene resa accessibile in rete a utenti, a quegli utenti va offerto il sorgente corrispondente. Uso solo interno (dipendenti del laboratorio) → il fork resta privato ma il sorgente deve restare disponibile a chi usa il servizio. Il repository privato su GitHub è sufficiente per l'uso attuale; **non** esporre l'istanza a terzi (clienti) senza rivalutare.
- SDK ELEGOO `elegoo-link`: **Apache-2.0** (compatibile con AGPL-3.0 in direzione Apache→GPLv3). In questa fase **non** viene incorporato codice SDK: l'adapter Python è un'implementazione indipendente del protocollo, con citazione delle fonti (vedi `ELEGOO_PROTOCOL_RESEARCH.md`).
- Integrazione HACS `nict41/HACS-elegoo-integration`: usata solo come riferimento documentale secondario; nessun codice copiato.

## 3. Stack e dipendenze

### Backend (`backend/app/`)
- Python ≥ 3.10 (`pyproject.toml`), **FastAPI** `<0.136` (pin per supply-chain, `requirements.txt`), Uvicorn, **SQLAlchemy 2 async** (`aiosqlite` default, `asyncpg` opzionale per PostgreSQL), Pydantic 2.
- Protocollo Bambu: `paho-mqtt ≥ 2` (MQTT TLS 8883), `aioftp` + FTPS implicito 990 (`services/bambu_ftp.py`, 2.558 righe).
- Stampante virtuale: `pyftpdlib`, `cryptography`, `pyopenssl` (`services/virtual_printer/`).
- Altro: `defusedxml` (3MF), `openpyxl`, `reportlab`, `qrcode`, `asyncssh` (SpoolBuddy), LDAP/OIDC, PDFium.
- Dimensioni: 66 file route (`api/routes/`), ~150 service, 85 tabelle ORM.

### Frontend (`frontend/`)
- React 19, TypeScript 5.9, Vite 8, Tailwind, TanStack Query 5, Vitest 4. 28 pagine (`src/pages/`), 151 componenti, 303 file di test.
- Tipo `Printer` in `frontend/src/api/client.ts:416`.

### Deploy
- `Dockerfile`, `docker-compose.yml` con `network_mode: host` (necessario per SSDP/stampante virtuale). Porta 8000.
- Sidecar slicer opzionale (`slicer-api/`, fork `orca-slicer-api`). `spoolbuddy/`: daemon per bilancia/NFC su Raspberry.

## 4. Moduli e flussi attuali

### 4.1 Registro stampanti
- Modello `Printer` (`backend/app/models/printer.py`): `serial_number` UNIQUE, `ip_address`, `access_code` `String(20)` **in chiaro**, `model`, `location`, `nozzle_count`, camera esterna, plate detection, `awaiting_plate_clear`.
- Nessun campo produttore/protocollo prima di questo lavoro: ogni riga era implicitamente Bambu.
- Schemi `backend/app/schemas/printer.py`: `serial_number` viene forzato **maiuscolo** (topic MQTT Bambu case-sensitive, #1465); `access_code` obbligatorio (1–20 caratteri). `PrinterResponse` non espone mai `access_code`; `PrinterResponseWithSecret` solo con `PRINTERS_UPDATE`.

### 4.2 Discovery
- `backend/app/services/discovery.py`: SSDP su **porta 2021** (non standard Bambu) + `SubnetScanner` che prova porte 990 (FTPS) e 8883 (MQTT). Interamente Bambu-specifico.

### 4.3 Connessione e stato
- `PrinterManager` (`backend/app/services/printer_manager.py:384`) mantiene `self._clients: dict[int, BambuMQTTClient]`. Singleton `printer_manager` (riga ~1708). `init_printer_connections()` connette in parallelo tutte le stampanti attive all'avvio.
- `BambuMQTTClient` (`backend/app/services/bambu_mqtt.py`, **8.480 righe**): paho in thread separato, `check_staleness()` forza riconnessione dopo silenzio, `mark_power_off()` per smart plug, log MQTT diagnostici in memoria.
- `PrinterState` (`bambu_mqtt.py:887`): dataclass con ~70 campi, molti Bambu-specifici (AMS, HMS, stg_cur, nozzle rack H2C, FTS…). È **lo stato canonico** letto da tutta l'app: `printer_state_to_dict()` (WebSocket), `GET /printers/{id}/status`, scheduler, metriche Prometheus, Obico, inventario.
- Callback registrate da `main.py` (~righe 9677+): status change → broadcast WebSocket + riconciliazione; print start → archiviazione; print complete → archivio, consumo filamento, notifiche, plate-clear gate.

### 4.4 Archivio 3MF
- `on_print_start` (`backend/app/main.py:3992`) scarica il 3MF dalla stampante via **FTPS** (`download_file_async`, `download_file_try_paths_async`) e crea `PrintArchive` tramite `ArchiveService` (`services/archive.py`). `on_print_complete` (`main.py:6971`) chiude l'archivio, calcola consumo, foto finale, timelapse.
- Fortemente legato a Bambu: path SD/eMMC, plate `Metadata/plate_N.gcode`, `subtask_name`, HMS per classificare guasti.

### 4.5 MakerWorld
- `services/model_providers/` — già un **pattern provider** (ABC `ModelProvider` + `ProviderService` + `registry`), usato da MakerWorld (`model_providers/makerworld/`). Indipendente dalle stampanti: riutilizzabile così com'è.

### 4.6 Inventario filamenti
- Modelli `Spool`, `SpoolAssignment`, `SpoolUsageHistory`, catalogo colori/SKU, integrazione **Spoolman** opzionale (`services/spoolman.py`, `spoolman_tracking.py`). Assegnazione bobina ↔ slot espressa come `(printer_id, ams_id, tray_id)`: concetto AMS. `usage_tracker.py` usa `tray_now`/`tray_change_log` del `PrinterState`.

### 4.7 Code, scheduling, lotti
- `PrintQueueItem` (`models/print_queue.py`): stampante fissa **oppure** `target_model` + `target_location`, `ams_mapping`, `filament_overrides`, `batch_id`, `project_id`.
- `PrintBatch` + `PrintBatchPlate` (`models/print_batch.py`): lotti con quantità obiettivo per piatto — **base già pronta per i lotti ALVEOLAB/DEKO di M6**.
- `PrintScheduler` (`services/print_scheduler.py`, **8.733 righe**): selezione stampante per modello (`_printers_for_model`, `_find_idle_printer_for_model` ~riga 2878), `_is_printer_idle` (~4256), upload FTPS e `start_print` MQTT `project_file`.

### 4.8 Altre integrazioni
Notifiche (molti provider), Home Assistant, MQTT relay, smart plug (Tasmota/HA/REST/MQTT), Obico, Bambu Cloud, Orca Cloud, GitHub backup, LDAP/OIDC/MFA, metriche Prometheus (`routes/metrics.py`), webhook API key (`routes/webhook.py`).

## 5. Accoppiamento con le API Bambu Lab

Misurato con grep sul backend:

| Punto di accoppiamento | Occorrenze | Significato |
|---|---|---|
| `printer_manager.get_status(...)` | 112 | Legge `PrinterState` (formato Bambu) |
| `printer_manager.get_client(...)` | 69 (21 file) | Ottiene `BambuMQTTClient` e gli invia **comandi Bambu** |
| `printer_manager.get_model(...)` | 22 | Feature detection per modello Bambu |
| Valori `gcode_state` (`IDLE/PREPARE/RUNNING/PAUSE/FINISH/FAILED`) | diffusi | Vocabolario di stato usato da scheduler, UI, Obico |
| `raw_data["ams"]` | route status, inventario, Spoolman | Formato report MQTT Bambu |

Moduli **fortemente** accoppiati: `bambu_mqtt.py`, `bambu_ftp.py`, `print_scheduler.py`, `main.py` (callback archivio), `routes/printers.py` (controlli: pausa, temperature, AMS, jog, HMS), `discovery.py`, `virtual_printer/`, `usage_tracker.py`, `spoolman_tracking.py`, `firmware_*`.

Moduli **neutri o quasi**: libreria file, progetti, MakerWorld, inventario bobine (a parte gli slot AMS), finanza, utenti/auth, notifiche, statistiche su `PrintArchive`.

**Implicazione progettuale:** riscrivere i 181 punti di accesso per astrarli sarebbe un refactoring enorme e in conflitto permanente con upstream. Scelta: il provider non-Bambu **produce un `PrinterState`** (riempiendo solo i campi che conosce) così tutti i lettori di stato funzionano subito, mentre `get_client()` resta **solo Bambu** — i ~70 punti che inviano comandi Bambu ricevono `None` per una ELEGOO e degradano nel ramo già esistente "stampante non connessa", invece di inviare comandi sbagliati. Dettagli in `PRINTER_PROVIDER_DESIGN.md`.

## 6. Database e migrazioni

- Nessun Alembic. `init_db()` esegue `create_all()` e poi `run_migrations()` (`backend/app/core/database.py:1813`, file di 6.330 righe) con DDL idempotenti tramite `_safe_execute()` (savepoint + riconoscimento "già applicato" via SQLSTATE su PostgreSQL / messaggio su SQLite).
- Supporto doppio dialetto SQLite/PostgreSQL (`core/db_dialect.py`).
- Pattern da seguire per nuove colonne: `await _safe_execute(conn, "ALTER TABLE ... ADD COLUMN ... DEFAULT ...")` + campo ORM con `server_default`.

## 7. Configurazione e segreti

- `core/config.py` (pydantic-settings, `.env`), `DATA_DIR`, `DATABASE_URL`, logging su file con rotazione.
- `core/encryption.py` + `_migrate_encrypt_legacy_secrets()` cifrano alcuni segreti (token cloud ecc.), **ma `printers.access_code` resta in chiaro** (`String(20)`). Rischio accettato upstream (LAN); da annotare per ELEGOO se il protocollo richiede password/PIN.
- Filtri di log (`core/logging_filters.py`) per evitare di stampare segreti.

## 8. Test

- Backend: **610 file** di test, **11.475 funzioni `test_`** (`backend/tests/unit`, `backend/tests/integration`), pytest + xdist + asyncio auto. `conftest.py` forza un DB SQLite usa-e-getta e **rifiuta** di girare contro un DB reale (`_assert_disposable_database`).
- `test_backend.sh`: `ruff check`, `ruff format --check`, `pytest -n 30` (esclude `test_bambu_ftp.py` salvo `--full`).
- Frontend: 303 file Vitest (`npm test`). Non eseguiti in questa milestone (nessuna modifica frontend in M0–M2).
- Risultato baseline (HEAD pulito, worktree separato): vedi §10.

## 9. Rischi

| # | Rischio | Mitigazione |
|---|---|---|
| R1 | Conflitti di merge con upstream molto attivo | Codice nuovo in `services/printer_providers/`; patch minime a file caldi; remote `upstream` configurato; sync periodico documentato in `IMPLEMENTATION_PLAN.md`. |
| R2 | Un comando Bambu inviato a una ELEGOO | `get_client()` restituisce solo `BambuMQTTClient`; scheduler esclude stampanti senza capability `queue_dispatch`. |
| R3 | Pipeline archivio/consumi che interpreta una disconnessione come fine stampa | Il provider ELEGOO non emette callback start/complete finché M4 non le implementa; `JobState.UNKNOWN` esplicito; disconnessione → `connected=False`, stato lavoro **invariato**. |
| R4 | Protocollo CC2 non documentato ufficialmente, firmware che cambia | Ogni messaggio tracciato a riga dell'SDK; parser tolleranti (campi mancanti → `None`); checklist hardware prima di abilitare comandi. |
| R5 | 3MF Bambu non stampabile da ELEGOO | Capability `queue_dispatch` assente per ELEGOO; M4 distingue modello sorgente / progetto 3MF / profilo / file macchina. |
| R6 | Segreti in chiaro (`access_code`) | Non loggati; valutare cifratura in M4 se il CC2 usa credenziali. |
| R7 | Obico / camera / smart plug agiscono su stampanti ELEGOO in modo Bambu | Percorsi che passano da `get_client()` si fermano; Obico va escluso per provider in M3 (annotato). |
| R8 | Test automatici che contattano hardware | Test ELEGOO solo con fixture e broker finto; nessun socket reale. |

## 10. Baseline test (HEAD `98ecaf50`, prima di ogni modifica)

Eseguito in un worktree separato su HEAD pulito, Python 3.12.4 (Windows), comando:
`pytest tests/ -q -n 14 --ignore=tests/unit/services/test_bambu_ftp.py --timeout=300`

| Esecuzione | Passati | Falliti | Errori | Skip | Durata |
|---|---|---|---|---|---|
| Baseline HEAD `98ecaf50` (pulito) | 13.379 | 71 | 16 | 2 | 11m20s |
| Dopo M1+M2 | 13.512 | 71 | 16 | 2 | 7m50s |

- L'insieme dei test falliti è **identico** prima e dopo (confronto per ID: 0 nuovi, 0 risolti). I +133 passati sono i nuovi test ALVEOLAB.
- I 71+16 fallimenti sono **ambientali (Windows)**, non bug: permessi `chmod 0600`, unit systemd e installer `.sh`, path POSIX/symlink, `os.geteuid`, rilevamento container (`/proc`), timezone SQLite. Concentrati in `test_slice_external_folder_output.py` (14), `test_queue_variants_api.py` (9), `test_library_api.py` (8), `test_vp_diagnostic.py` (5), `test_cleartext_probe_2780.py` (5) e altri. Upstream gira la CI su Linux/Docker: per un verde completo usare `docker-compose.test.yml` o WSL.
- `ruff check` e `ruff format --check`: puliti.

## 11. Piano di intervento (sintesi)

1. **M0** (questo documento): audit, upstream, baseline.
2. **M1**: `services/printer_providers/` (contratti, capability, normalizzazione Bambu), colonna `printers.provider`, `PrinterManager` multi-provider senza cambiare il percorso Bambu, guardia nello scheduler.
3. **M2**: adapter ELEGOO CC2 read-only (protocollo, normalizzatore, client con riconnessione, discovery), test solo con fixture.
4. **M3+**: vedi `IMPLEMENTATION_PLAN.md`.

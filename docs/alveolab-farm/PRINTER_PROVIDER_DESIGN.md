# ALVEOLAB Farm — Progetto del livello provider (M1)

Stato: **implementato** (M1) + primo adapter ELEGOO read-only (M2). Codice in `backend/app/services/printer_providers/`.

## 1. Obiettivo e vincoli

- Gestire produttori diversi (Bambu Lab, ELEGOO, in futuro altri) da un'unica farm.
- **Non** peggiorare il supporto Bambu: il percorso `BambuMQTTClient` resta byte-identico.
- **Non** rifare i 181 punti che leggono `PrinterState` o chiamano `get_client()` (vedi `ARCHITECTURE_AUDIT.md` §5): conflitti permanenti con upstream.
- Nessun metodo imposto a un provider che non lo supporta: capability esplicite.
- Stati sconosciuti espliciti; una disconnessione non è mai "stampa finita".

## 2. Decisione principale

> Un provider non-Bambu **produce un `PrinterState`** (lo stato canonico già consumato da dashboard, WebSocket, API status, scheduler, metriche) e **non** viene mai restituito da `printer_manager.get_client()`.

Conseguenze:

| Area | Effetto per una stampante ELEGOO |
|---|---|
| `get_status()`, `get_all_statuses()`, `is_connected()` | Funzionano: leggono anche i client provider. Dashboard, WebSocket, `/printers/{id}/status`, Prometheus, notifica "stampante offline" funzionano senza modifiche. |
| `get_client()` (~70 punti che inviano comandi MQTT Bambu) | Restituisce `None` → i rami esistenti "Printer not connected" scattano. **Nessun comando Bambu può arrivare a una ELEGOO.** |
| `start_print()`, `stop_print()` del manager | Bambu-only (`_clients`), restituiscono `False`. |
| Scheduler coda | Escluso dalla guardia `_cannot_take_queue_jobs()` (capability `queue_dispatch` assente). Job fissato su ELEGOO resta in attesa con motivo esplicito "Not supported: … cannot take queue jobs yet". |
| Callback start/complete stampa (archivio, consumi) | Non collegati per ELEGOO fino a M4: l'archivio Bambu scarica il 3MF via FTPS. |
| Campi raw | Payload ELEGOO in `PrinterState.raw_data["elegoo"]`, mai a livello radice (dove i lettori Bambu cercano `ams`, `vt_tray`, `device`). |

Alternativa scartata: interfaccia astratta unica implementata anche da `BambuMQTTClient` + refactoring di tutti i chiamanti. Più "pulita" ma: tocca ~180 punti, rischio regressioni Bambu, conflitti con upstream a ogni sync. Rivalutabile solo se il fork smette di seguire upstream.

## 3. Componenti

```text
backend/app/services/printer_providers/
├── __init__.py        capabilities_for(provider, model), create_provider_client(provider, printer)
├── base.py            contratti tipizzati (enum, dataclass, Protocol)
├── bambu.py           capability Bambu + normalize_bambu_state(PrinterState) (pura, read-only)
└── elegoo/
    ├── protocol.py    costanti/topic/metodi CC2, builder e parser puri, cache delta
    ├── normalizer.py  stato CC2 → NormalizedTelemetry → PrinterState
    ├── capabilities.py
    ├── client.py      ElegooCC2Client (paho-mqtt, thread), probe_connection()
    └── discovery.py   discovery UDP 52700
backend/app/api/routes/printer_providers.py   API nuove (router separato)
```

### 3.1 Contratti (`base.py`)

| Tipo | Ruolo |
|---|---|
| `PrinterProvider` (`bambu`, `elegoo`) + `parse_provider()` | Famiglia protocollo della riga `printers.provider`. Valore assente → Bambu (righe storiche); stringa sconosciuta → **errore** (mai fallback silenzioso a Bambu). |
| `Capability` | Discovery/registrazione/riconnessione, telemetria (stato, temperature, camera chiusa, avanzamento, layer, ventole, velocità, errori, log), controllo lavori (start, pause/resume, stop, upload, lista file), multicolore (stato, modifica slot), camera, pipeline Bambuddy (`queue_dispatch`, `auto_archive`, `ams_management`). |
| `CapabilitySet` | `supported` + `notes` (motivo per ogni capability mancante, da mostrare in UI). |
| `ConnectionStatus` | `connecting`, `online`, `offline`, `auth_failed` (non ritentare: serve un umano), `unknown`. |
| `JobState` | `idle`, `preparing`, `printing`, `pausing`, `paused`, `resuming`, `stopping`, `finished`, `failed`, `cancelled`, `busy` (attività macchina non di stampa), **`unknown`**. |
| `TemperatureReading`, `PrinterFault`, `MultiColorSlot` | Telemetria tipizzata. `MultiColorSlot.source` = `detected` / `declared` / `unknown`: distingue colore rilevato dalla macchina da quello dichiarato dall'operatore. |
| `NormalizedTelemetry` | Snapshot neutro; ogni valore non riportato è `None`, mai `0` inventato. |
| `PrinterProviderClient` (Protocol) | Contratto minimo runtime: `provider`, `state`, `capabilities`, `connect()`, `disconnect()`, `check_staleness()`, `telemetry()`. |
| `SupportsJobControl` (Protocol) | Opzionale (pause/resume/stop) — usato da M4, verificato con `isinstance` **e** capability. |

### 3.2 `PrinterManager` (patch minima)

`backend/app/services/printer_manager.py`:
- nuova mappa `_provider_clients` separata da `_clients` (Bambu) + `_providers`;
- `connect_printer()` instrada per `printer.provider`; ramo Bambu invariato (test di regressione `test_bambu_connect_path_unchanged`);
- `get_status`, `get_all_statuses`, `is_connected`, `disconnect_printer`, `disconnect_all` vedono entrambe le mappe;
- nuovi: `get_provider()`, `get_provider_client()`, `get_capabilities()`.

### 3.3 Database

Colonna `printers.provider VARCHAR(20) NOT NULL DEFAULT 'bambu'` (ORM `models/printer.py`, migrazione idempotente in `core/database.py` accanto alle altre colonne `printers`). Le righe esistenti diventano `bambu` per default.

### 3.4 Schemi API

`schemas/printer.py`:
- `provider: Literal["bambu", "elegoo"] = "bambu"` (client esistenti invariati);
- seriale maiuscolo **solo per Bambu** (fix #1465 preservato); ELEGOO conserva il seriale come da discovery;
- `access_code` obbligatorio solo per Bambu; facoltativo per CC2 (serve solo con `token_status == 1`).

### 3.5 API nuove (router `printer-providers`, file separato per non toccare `printers.py`)

| Metodo | Path | Permesso | Funzione |
|---|---|---|---|
| GET | `/api/v1/printer-providers/printers/{id}/capabilities` | `PRINTERS_READ` | Capability + note (anche offline, dalla tabella statica). |
| GET | `/api/v1/printer-providers/printers/{id}/telemetry` | `PRINTERS_READ` | `NormalizedTelemetry` (Bambu o ELEGOO); `unknown` se non connessa. |
| POST | `/api/v1/printer-providers/elegoo/discover` | `DISCOVERY_SCAN` | Discovery CC2 (broadcast o IP unicast; solo IPv4). |

`POST /api/v1/printers/` accetta `provider: "elegoo"` e usa `probe_connection()` (connect + registrazione + query read-only) al posto del test MQTT Bambu.

## 4. Mappatura stato ELEGOO → vocabolario Bambuddy

Il resto dell'app ragiona in `gcode_state` Bambu. Tabella in `elegoo/normalizer.py`:

| `JobState` | `PrinterState.state` | Nota |
|---|---|---|
| idle | `IDLE` | |
| preparing | `PREPARE` | preriscaldo, homing, livellamento, download file |
| printing / resuming / stopping | `RUNNING` | |
| pausing / paused | `PAUSE` | anche ripristino da mancanza corrente (status 15) |
| finished | `FINISH` | |
| failed / cancelled | `FAILED` | come il firmware Bambu riporta l'annullamento |
| busy | `PREPARE` | stato "attivo": la guardia smart-plug non toglie corrente durante calibrazioni/aggiornamenti |
| unknown | `unknown` | |

## 5. Sicurezza e affidabilità (come è garantita)

| Requisito | Meccanismo |
|---|---|
| Nessun comando di lavoro in M2 | `protocol.build_request()` rifiuta (PermissionError) ogni metodo fuori da `READ_ONLY_METHODS = {1001, 1002, 2005}`. Testato. |
| Disconnessione ≠ fine stampa | Il client su disconnessione imposta solo `connected=False`; la cache di stato viene azzerata **solo** a una nuova connessione; finché il printer non riparla, il job resta quello visto per ultimo. Test `test_disconnect_does_not_finish_the_print`. Nessuna callback complete. |
| Stato sconosciuto esplicito | `JobState.UNKNOWN`; campi `None`. |
| Riconnessione | paho `reconnect_delay_set(5, 60)`; silenzio > 65 s o registrazione senza risposta → chiusura socket → paho riconnette lo stesso client → nuova registrazione (tecnica identica a `BambuMQTTClient._socket_close_for_reconnect`). |
| Credenziali | Mai loggate; log diagnostico salva solo payload (test). Codice rifiutato → `AUTH_FAILED` e stop dei tentativi. |
| Doppio avvio dopo riconnessione | Non applicabile in M2 (nessun avvio). Requisito di progetto M4 in `IMPLEMENTATION_PLAN.md`. |
| Test senza hardware | Client con `mqtt_client_factory` iniettabile; fixture JSON; API con probe/connect/discovery mockati. |

## 6. Estendere a un nuovo provider

1. Aggiungere il valore a `PrinterProvider` e al `Literal` di `PrinterBase.provider`.
2. Creare `printer_providers/<marca>/` con capability, normalizzatore e client che rispetta `PrinterProviderClient`.
3. Registrare in `capabilities_for()` e `create_provider_client()`.
4. Fixture + test senza hardware; checklist hardware.

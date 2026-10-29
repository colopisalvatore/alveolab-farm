# ELEGOO Centauri Carbon 2 (CC2) — Ricerca sul protocollo LAN

Data: 2026-10-29. Modello target: **ELEGOO Centauri Carbon 2 Combo** (con sistema multicolore **CANVAS**).

## Fonti

| Fonte | Commit | Licenza | Uso |
|---|---|---|---|
| SDK ufficiale `elegooofficial/elegoo-link` (C++) | `ec2d2269b588608a1b9ccb4ff9627ddb758ea482` (23/10/2026), CMake `VERSION 1.0.6` | Apache-2.0 | **Primaria**. Ogni comando implementato è tracciato qui. |
| `nict41/HACS-elegoo-integration` (Python, Home Assistant), `docs/CC2_PROTOCOL.md` | `11899079cef9730c1471a5da307793bcecc311bc` (19/10/2026) | MIT | Secondaria: osservazioni su firmware reale. Marcata **[community]**. Non garantisce funzionamento. |

Percorsi SDK relativi alla radice del repository SDK; `HA/` = repository community. Le affermazioni chiave (topic, porte, credenziali, tabella metodi, cache delta, heartbeat, parsing stato/CANVAS) sono state **riverificate a mano sul sorgente SDK** durante questa sessione.

Nessun codice SDK è stato copiato: l'adapter Python è un'implementazione indipendente del protocollo.

## 1. CC2 ≠ Centauri Carbon (gen 1) ≠ Neptune/Moonraker

L'SDK ha tre adapter distinti; la factory (`src/lan/core/printer_factory.cpp:17-44`) sceglie in base a `printerType` (`include/types/printer.h:23-32`: `ELEGOO_FDM_CC = 1`, `ELEGOO_FDM_CC2 = 2`, Klipper = 0/100).

| | **CC2** | CC1 (Centauri Carbon) | Moonraker (Neptune) |
|---|---|---|---|
| Discovery | UDP **52700**, `{"id":0,"method":7000}`, risposta `{id, result{…}}` | UDP 3000, `M99999`, risposta `{Id, Data{…}}` | nessuna (stub) |
| Controllo | **MQTT** `tcp://<ip>:1883`, broker sulla stampante, registrazione + PING/PONG | WebSocket SDCP `ws://<ip>:3030/websocket` | WebSocket JSON-RPC 2.0 |
| Envelope | `{id:int, method:int, params}` → `{id, method, result{error_code}}` | `{Id, Data{Cmd,…}, Topic:"sdcp/request/<id>"}` | `{"jsonrpc":"2.0",…}` |
| Upload | HTTP `PUT /upload` porta 80, corpo raw, `Content-Range`, `X-File-MD5`, `X-Token` | `POST /uploadFile/upload` multipart | `POST /api/files/local` |

Riconoscimento modello: stringa contenente `"Centauri Carbon 2"` o `"Centauri 2"` → CC2 (`printer.h:79-89`). In ALVEOLAB Farm: `elegoo/capabilities.py::is_cc2_model`; ogni altro modello ELEGOO riceve **capability vuote** (non viene guidato come CC2).

## 2. Discovery

- Broadcast UDP su ogni interfaccia, porta 52700 (`src/lan/adapters/elegoo_cc2_adapters.h:164`, `printer_discovery.cpp:433-517`).
- Richiesta: `{"id": 0, "method": 7000}` (`elegoo_fdm_cc2_discovery_strategy.cpp:10-14`).
- Risposta CC2: chiavi minuscole `id` + `result` (strategia :26). Campi `result`: `host_name`, `machine_model`, `sn`, `token_status` (1 = access code richiesto), `lan_status` (1 = LAN-only; 0/assente = cloud → l'SDK usa PIN da app) (:37-102).
- Timeout SDK 5 s, nessun re-broadcast di default (`printer_discovery.h:58-61`). [community] unicast a `<ip>:52700` risponde (`HA/docs/CC2_PROTOCOL.md:290-295`).
- Implementazione: `elegoo/discovery.py::discover_cc2`, parser `protocol.parse_discovery_response` (rifiuta CC1 e payload malformati).

## 3. Connessione MQTT

| Voce | Valore | Fonte |
|---|---|---|
| Broker | sulla stampante, `tcp://<ip>:1883`, **nessun TLS** | `elegoo_fdm_cc2_protocol.cpp:20-28` |
| Username | `elegoo` (costante) | `elegoo_fdm_cc2_protocol.cpp:143-190` |
| Password | access code; se assente, default di fabbrica `123456` (`CC2_DEFAULT_TOKEN`) | `elegoo_fdm_cc2_http_transfer.cpp:12`, `elegoo_fdm_cc2_protocol.cpp:151-186` |
| Opzioni | keepalive 60 s, clean session, QoS 1, retain false | `mqtt_protocol.cpp:79,159-173` |
| Client id | `1_PC_<1000..9999>`, request id `<client_id>_req` | `elegoo_fdm_cc2_protocol.cpp:13-17` |
| Sottoscrizioni | `elegoo/<sn>/<cid>/api_response`, `elegoo/<sn>/api_status`, `elegoo/<sn>/<rid>/register_response` | `:194-201` |
| Pubblicazione | comandi + heartbeat su `elegoo/<sn>/<cid>/api_request`; registrazione su `elegoo/<sn>/api_register` | `:205-237` |

**Sequenza** (`mqtt_protocol.cpp:111-342`): CONNECT → subscribe ×3 → publish `{"client_id","request_id"}` su `api_register` → attesa `register_response` (timeout 3–5 s) con `error == "ok"`; `"too many clients"` = limite client ([community] ~4). [community] `"already registered"` va trattato come successo (firmware 02.01.00.00).

**Heartbeat**: `{"type":"PING"}` ogni 10 s su `api_request`; risposta `{"type":"PONG"}`; > 65 s senza PONG → riconnessione (`elegoo_fdm_cc2_protocol.cpp:324-355`). Riconnessione SDK ogni 5 s (`connection_manager_base.cpp:195-238`).

**Scelte ALVEOLAB Farm** (`elegoo/client.py`):
- client id nel formato SDK ma **derivato dal seriale** (stabile tra riavvii) per non consumare slot client a ogni riconnessione;
- password: access code se configurato, altrimenti default SDK (non si prova la password vuota, che è solo un comportamento community);
- `CONNACK` 4/5 (paho 2: 134/135) → `AUTH_FAILED` e stop dei tentativi;
- qualunque messaggio ricevuto (anche PONG) aggiorna il timer di silenzio.

## 4. Metodi

Tabella `COMMAND_MAPPING_TABLE`, `elegoo_fdm_cc2_message_adapter.cpp:1391-1423`.

| Codice | Nome | Stato nell'SDK | In ALVEOLAB Farm |
|---|---|---|---|
| 1001 | attributi | attivo | **usato (read-only)** |
| 1002 | stato completo | attivo | **usato (read-only)** |
| 2005 | stato CANVAS | attivo | **usato (read-only)** |
| 1020 | avvio stampa | attivo | M4, bloccato in M2 |
| 1021 | pausa | attivo | M4, bloccato |
| 1022 | stop/annulla | attivo | M4, bloccato |
| 1023 | ripresa | **commentato** (riga 1399) | M4, solo dopo verifica hardware ([community] lo usa) |
| 1026–1031 | homing, movimento, temperatura, luce, ventole, velocità | commentati | non previsti |
| 1036–1048 | task/file list/detail/delete/disk | commentati | non previsti (eventuale M4 dopo verifica) |
| 1043 | rinomina | attivo | non usato |
| 1057 / 1058 | download file da URL / annulla | attivi | valutare in M4 |
| 2004 | auto-refill | attivo | non usato |
| 6000 / 6008 | push stato (delta) / attributi | eventi | gestiti |

Envelope richiesta `{"id","method","params"}`, id da contatore che parte da 100000 (`message_adapter.cpp:44-51`). Esito: `result.error_code == 0`. Codici errore: tabella in `protocol.ERROR_CODES` (adapter 1427-1474: 1000 token, 1003 parametro, 1009 occupata, 1010 non in stampa, 109 filamento esaurito, 1021 file non trovato, 1026 manca livellamento, 9000-9009 upload, …).

## 5. Stato e telemetria

- 1002 = stato completo (cache); 6000 = **delta** fuso ricorsivamente (oggetti merge, scalari/array sovrascritti, `exception_code` sostituito intero) (adapter 584-637, 1303-1348). Delta senza cache → scartato. Dopo 5 id push non consecutivi → nuova richiesta 1002 (549-577). Implementato in `protocol.CC2StatusCache`.
- `print_status` considerato **solo** con `status == 2`; durante il download (sub 1081/1082/1086) avanzamento e tempi non sono di stampa (970-1001).

| Campo | Significato |
|---|---|
| `machine_status.status` / `sub_status` | stato macchina (0 init, 1 idle, 2 printing, 3-4 filamento, 5 livellamento, 6 PID, 7 risonanza, 8 autotest, 9 update, 10 homing, 11 trasferimento file, 12 video, 13 estrusore, 14 emergenza, 15 ripristino corrente) |
| `machine_status.progress` | avanzamento % usato dall'SDK |
| `machine_status.exception_status`, `exception.exception_code` | errori attivi |
| `print_status.filename`, `current_layer`, `total_layer`, `print_duration`, `remaining_time_sec` | lavoro |
| `extruder.temperature/target`, `heater_bed.temperature/target`, `ztemperature_sensor.temperature` | temperature (camera senza target) |
| `fans.{fan,aux_fan,box_fan,heater_fan,controller_fan}.speed` | ventole |
| `gcode_move_inf.speed_mode` | 0 silenzioso, 1 bilanciato, 2 sport, 3 ludicrous |
| `led.status`, `external_device.{camera,u_disk,type}` | luce, periferiche, presenza CANVAS |

Mappatura sub-stati di stampa → `JobState`: vedi `normalizer.job_state_from_machine_status` (2501 pausing, 2502/2505 paused, 2401 resuming, 2503 stopping, 2504 cancelled, 2077 finished, 2075 printing, preriscaldo/homing/livellamento/download → preparing).

## 6. CANVAS (multicolore)

- Dati in `result.canvas_info` (risposta 2005 o dentro lo stato): `active_canvas_id`, `active_tray_id`, `auto_refill`, `canvas_list[{canvas_id, connected, tray_list[{tray_id, brand, filament_type, filament_name, filament_code, filament_color, min/max_nozzle_temp, status}]}]` (adapter 1216-1262).
- Presenza CANVAS: `external_device.type` stringa ≠ `""`/`"0"` o intero ≠ 0 (1126-1142).
- Avvio con mappatura: `params.config.slot_map = [{"t", "canvas_id", "tray_id"}]`, `t` = indice tool G-code (adapter 85-95, `printer.h:486-491`). [community] il file detail 1046 restituisce `color_map[{color,name,t}]` del file slicato.

**Conflitti / limiti dichiarati:**
1. `tray_id`: SDK dice 1-4 (0 = nessuno), esempio reale community 0-3. → ALVEOLAB Farm **non rinumera**: conserva l'id così com'è.
2. `status` del tray: SDK 0 vuoto / 1 precaricato / 2 caricato; community 1 presente / 2 attivo. → Solo `0 = vuoto` è certo; "attivo" si ricava da `active_canvas_id`/`active_tray_id`.
3. **Rilevato vs dichiarato: non esposto da nessuna fonte** (nessun RFID/flag). → `MultiColorSlot.source = unknown`. Nessuna simulazione: in M5 l'associazione bobina-slot sarà **manuale** in UI, con avviso.
4. Nessun metodo per modificare i dati di uno slot nell'SDK → capability `multicolor_slot_edit` assente.

## 7. Upload file (per M4, non implementato)

`PUT http://<ip>/upload` (porta 80), corpo raw a blocchi ≤ 1 MiB, header `Content-Type: application/octet-stream`, `Content-Range: bytes a-b/totale`, `X-File-Name`, `X-File-MD5` (MD5 file intero), `X-Token` (access code o default), risposta `{"error_code":0}` (`elegoo_fdm_cc2_http_transfer.cpp:55-290`). Il file caricato si avvia con `storage_media: "local"` e `filename` (esempio SDK `examples/printer_connection_test.cpp:323-388`). Estensioni: nessun controllo sul percorso LAN; il cloud accetta `gcode`/`3mf` (`cloud_service.cpp:2449-2455`). Limiti di dimensione: **non trovati**.

**3MF Bambu ≠ file stampabile ELEGOO.** Un 3MF slicato da Bambu Studio contiene G-code per macchine Bambu (`Metadata/plate_N.gcode`): non va inviato alla CC2. In M4 si distingue: modello sorgente (STL/3MF progetto) → profilo di slicing ELEGOO → file macchina (`.gcode`, o 3MF slicato da ElegooSlicer/Orca con profilo CC2) → stampante.

## 8. Camera

SDK: nessuno stream LAN (1042 commentato). [community] MJPEG `http://<ip>:8080/?action=stream`, di solito 1 sola connessione. Non implementato; capability `camera` assente.

## 9. Autenticazione e sicurezza

- Access code impostabile dal touchscreen ([community] Impostazioni → Rete → Access Code). Con `token_status == 0` si usa il default `123456`.
- Modalità cloud (`lan_status == 0`): [community] il controllo LAN richiede di attivare **LAN Only Mode**.
- **Tutto in chiaro** (MQTT e HTTP senza TLS): rete del laboratorio trattata come fidata; non esporre la stampante fuori dalla LAN.

## 10. Punti aperti (verifica su hardware: `HARDWARE_TEST_CHECKLIST.md`)

1. Firmware effettivo della nostra macchina e risposta a discovery.
2. Ripresa 1023 (disabilitata nell'SDK).
3. Numerazione `tray_id` e significato `status` 1/2.
4. Chiave `slot_map`: `t` (SDK) vs `slot` (doc community).
5. Scala ventole in lettura: 0-255 (osservato) vs percentuale (struct SDK). Implementazione attuale: 0-255.
6. Continuità push: `id` dell'envelope (SDK) vs `result.sequence` (community).
7. Upload senza `X-Token` su firmware stock; limiti di dimensione.
8. Numero massimo di client MQTT (slicer + app + farm contemporanei).

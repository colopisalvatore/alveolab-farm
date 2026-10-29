# ALVEOLAB Farm — Checklist test su hardware reale

**Regola:** nessun test automatico contatta stampanti reali. Ogni voce qui sotto si esegue **a mano**, sulla macchina indicata, **solo dopo autorizzazione esplicita di Salvo** (data + firma nella colonna Esito). Le voci "comando" (M4) possono muovere assi, scaldare o avviare stampe: piatto libero, operatore presente.

Legenda rischio: 🟢 sola lettura · 🟡 cambia stato non distruttivo · 🔴 avvia/ferma lavori o scalda.

## Preparazione (una volta)

- [ ] Annotare modello esatto, versione firmware (`software_version.ota_version`), IP fisso/DHCP riservato della Centauri 2 Combo.
- [ ] Verificare sul touchscreen: **LAN Only Mode** attivo; access code impostato o no (annotare quale, **non** nel repository: in `brain/secrets/` con `cred.py set`).
- [ ] Backup DB ALVEOLAB Farm prima di aggiungere la stampante.
- [ ] Abilitare log diagnostico della stampante in UI (Logging) durante i test.

## M2 — Connessione e monitoraggio (read-only)

| ID | Rischio | Test | Atteso | Esito |
|---|---|---|---|---|
| HW-M2-01 | 🟢 | `POST /api/v1/printer-providers/elegoo/discover` (broadcast, poi `targets:[IP]`) | La CC2 compare con `sn`, `machine_model`, `token_status`, `lan_status=1` | |
| HW-M2-02 | 🟢 | Aggiungere la stampante (`provider: "elegoo"`, seriale da discovery, access code se `token_status=1`) | Probe ok, riga salvata, card in dashboard online | |
| HW-M2-03 | 🟢 | Access code volutamente errato | Errore `printer_connection_failed`; client `auth_failed`; **nessun** tentativo ripetuto nei log | |
| HW-M2-04 | 🟢 | Stampante idle: confrontare `GET …/telemetry` con il touchscreen | Temperature ugello/piano/camera coerenti (±2 °C); `job_state=idle` | |
| HW-M2-05 | 🟢 | Durante una stampa avviata **dal touchscreen**: ventole | Scala ventole: confrontare % touchscreen con `fans_percent` (ipotesi 0-255 → %) | |
| HW-M2-06 | 🟢 | Durante la stessa stampa | Nome file, %, layer corrente/totale, tempo residuo coerenti; `printing` | |
| HW-M2-07 | 🟢 | Pausa/ripresa **dal touchscreen** | Transizioni `pausing → paused → resuming → printing` | |
| HW-M2-08 | 🟢 | Fine stampa | `finished` (sub 2077), poi `idle` | |
| HW-M2-09 | 🟢 | Staccare il cavo/Wi-Fi della stampante per 2 min durante una stampa | Card offline entro ~65 s; **stato lavoro invariato**; nessun archivio "completato"; al ritorno riconnessione e stato reale | |
| HW-M2-10 | 🟢 | Riavvio ALVEOLAB Farm con CC2 accesa | Riconnessione senza errore `too many clients` (client id stabile) | |
| HW-M2-11 | 🟢 | Farm + ElegooSlicer + app ELEGOO connessi insieme | Annotare quanti client accetta; comportamento con `too many clients` | |
| HW-M2-12 | 🟢 | Provocare un errore (es. filamento esaurito, se sicuro) | Fault riportato con codice (109) | |
| HW-M2-13 | 🟢 | Continuità push 6000: log diagnostico | Gli `id` dei push sono consecutivi? (SDK) o serve `result.sequence`? | |

## M4 — Controllo lavori e upload (richiede autorizzazione per ogni voce)

| ID | Rischio | Test | Atteso | Esito |
|---|---|---|---|---|
| HW-M4-01 | 🟡 | Upload file `.gcode` piccolo (profilo CC2) via `PUT /upload` | `error_code 0`; file visibile sul touchscreen; MD5 accettato | |
| HW-M4-02 | 🟡 | Upload senza `X-Token` su firmware stock | Annotare se accettato | |
| HW-M4-03 | 🟡 | Upload 3MF slicato da ElegooSlicer/Orca (profilo CC2) | Annotare se stampabile | |
| HW-M4-04 | 🔴 | Avvio stampa test (1020) da farm, `slot_map` vuoto | Avvio unico; nessun doppio avvio dopo riconnessione forzata | |
| HW-M4-05 | 🔴 | Pausa (1021) / ripresa (1023, disabilitata nell'SDK) | Verificare se 1023 funziona sul nostro firmware | |
| HW-M4-06 | 🔴 | Annullamento (1022) | `cancelled`; archivio marcato annullato | |
| HW-M4-07 | 🔴 | Riconnessione forzata subito dopo invio avvio | Nessun secondo 1020 inviato | |

## M5 — CANVAS

| ID | Rischio | Test | Atteso | Esito |
|---|---|---|---|---|
| HW-M5-01 | 🟢 | 4 bobine diverse, `GET …/telemetry` → `multicolor_slots` | `tray_id` 0-based o 1-based? annotare | |
| HW-M5-02 | 🟢 | Bobina ELEGOO con RFID vs bobina generica dichiarata a mano | Il protocollo distingue rilevato/dichiarato? (ipotesi: no) | |
| HW-M5-03 | 🟢 | Svuotare uno slot | `status = 0`, slot `loaded=false` | |
| HW-M5-04 | 🟢 | Significato `status` 1 vs 2 | Annotare (precaricato/caricato vs presente/attivo) | |
| HW-M5-05 | 🔴 | Stampa 2 colori con `slot_map` (`t` vs `slot`) | Colori corretti | |

## M2 — Prima connessione: procedura consigliata

1. Discovery broadcast (HW-M2-01). Se la farm gira in Docker bridge, usare `targets` con l'IP.
2. Aggiungere la stampante da API/UI con il seriale **esatto** restituito dalla discovery.
3. Abilitare il logging della stampante e lasciare girare 10 minuti idle: verificare PING/PONG regolari e nessuna riconnessione.
4. Solo dopo, una stampa avviata dal touchscreen per HW-M2-05…08.

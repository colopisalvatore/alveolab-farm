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
| HW-M2-01 | 🟢 | `POST /api/v1/printer-providers/elegoo/discover` (broadcast, poi `targets:[IP]`) | La CC2 compare con `sn`, `machine_model`, `token_status`, `lan_status=1` | ✅ 29/10 (autorizzato da Salvo): unicast → `sn`, `machine_model` "Centauri 2", `protocol_version` 1.0.0, `token_status` 1; `lan_status` 0 → 1 dopo LAN Only Mode. Broadcast dal PC Windows: 0 risposte (da riprovare dal CT 100). |
| HW-M2-02 | 🟢 | Aggiungere la stampante (`provider: "elegoo"`, seriale da discovery, access code se `token_status=1`) | Probe ok, riga salvata, card in dashboard online | ✅ 29/10: con LAN Only spento → `register_timeout` (firmware non collegato al broker). Con LAN Only attivo: aggiunta in farm sul CT 100 come stampante 5, card online (Idle, temperature, fw). Prova 10 min idle dal PC: 59 PING/59 PONG, 82 push senza buchi, nessuna riconnessione. |
| HW-M2-03 | 🟢 | Access code volutamente errato | Errore `printer_connection_failed`; client `auth_failed`; **nessun** tentativo ripetuto nei log || ✅ 29/10 (parziale): password errata → CONNACK 5 / 135 "Not authorized" (mappato su `auth_failed`). Assenza di retry da verificare col client completo. |
| HW-M2-04 | 🟢 | Stampante idle: confrontare `GET …/telemetry` con il touchscreen | Temperature ugello/piano/camera coerenti (±2 °C); `job_state=idle` | ✅ 29/10 (parziale): idle → `job_state=idle`, ugello 39 °C, piano 21 °C, fw 02.01.01.04. **Nessun sensore camera** nei dati (capability tolta). Confronto con touchscreen da fare. |
| HW-M2-05 | 🟢 | Durante una stampa avviata **dal touchscreen**: ventole | Scala ventole: confrontare % touchscreen con `fans_percent` (ipotesi 0-255 → %) | ✅ 29/10: ventola pezzo grezza 255.0 → farm 100% = touchscreen 100%. Scala 0-255 confermata (il 78% iniziale era la rampa del primo layer). |
| HW-M2-06 | 🟢 | Durante la stessa stampa | Nome file, %, layer corrente/totale, tempo residuo coerenti; `printing` | ✅ 29/10 (benchy da Orca, avviata dal touchscreen): nome file, % e tempo residuo coerenti (2270 s ≈ 38 min del file). Solo layer corrente: **il totale layer non è nel protocollo**. Sottostato 1066 (spurgo, non documentato) ora = preparazione. |
| HW-M2-07 | 🟢 | Pausa/ripresa **dal touchscreen** | Transizioni `pausing → paused → resuming → printing` | ✅ 29/10: pausa e ripresa dal touchscreen → `pausing` (2501) → `resuming` (2401) → `printing` (2075). `paused` non visto col polling a 5 s. |
| HW-M2-08 | 🟢 | Fine stampa | `finished` (sub 2077), poi `idle` | ⚠️ 29/10: `printing` al 99% per ~2,5 min (scarico filamento), poi direttamente `idle`; `finished` (2077) non visto col polling a 5 s. In M4 la fine lavoro deve gestire anche `printing`→`idle` a ~100%. |
| HW-M2-09 | 🟢 | Staccare il cavo/Wi-Fi della stampante per 2 min durante una stampa | Card offline entro ~65 s; **stato lavoro invariato**; nessun archivio "completato"; al ritorno riconnessione e stato reale | |
| HW-M2-10 | 🟢 | Riavvio ALVEOLAB Farm con CC2 accesa | Riconnessione senza errore `too many clients` (client id stabile) | |
| HW-M2-11 | 🟢 | Farm + ElegooSlicer + app ELEGOO connessi insieme | Annotare quanti client accetta; comportamento con `too many clients` | ✅ 29/10 (parziale): farm + OrcaSlicer (Elegoo Link) + client di prova insieme, nessun `too many clients`. Limite massimo non cercato. |
| HW-M2-12 | 🟢 | Provocare un errore (es. filamento esaurito, se sicuro) | Fault riportato con codice (109) | |
| HW-M2-13 | 🟢 | Continuità push 6000: log diagnostico | Gli `id` dei push sono consecutivi? (SDK) o serve `result.sequence`? | ✅ 29/10: push 6000 con `id` dell'envelope consecutivi (10, 11, 12…), nessun refresh necessario. |

## M4 — Controllo lavori e upload (richiede autorizzazione per ogni voce)

**Come si esegue una voce.** Sul CT 100, in `/opt/alveolab-farm/deploy/alveolab-farm/.env`, si aggiunge la capability della voce a `ALVEOLAB_ELEGOO_ENABLE` (es. `ALVEOLAB_ELEGOO_ENABLE=file_upload`), poi `docker compose -f deploy/alveolab-farm/docker-compose.yml up -d`. Piatto libero, operatore davanti alla stampante. Se la voce fallisce, si toglie la capability e si riavvia: la Centauri torna in sola lettura. Ordine proposto: 01 → 04 → 05 → 06 → 07, poi 03.

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
| HW-M5-01 | 🟢 | 4 bobine diverse, `GET …/telemetry` → `multicolor_slots` | `tray_id` 0-based o 1-based? annotare | ✅ 29/10: `tray_id` 0-based (0-3). |
| HW-M5-02 | 🟢 | Bobina ELEGOO con RFID vs bobina generica dichiarata a mano | Il protocollo distingue rilevato/dichiarato? (ipotesi: no) | ⚠️ 29/10: bobina dichiarata a mano → `brand` "Generic", `filament_code` "0x0006", nessun campo RFID/manuale. Manca il confronto con una bobina ELEGOO con tag. |
| HW-M5-03 | 🟢 | Svuotare uno slot | `status = 0`, slot `loaded=false` | |
| HW-M5-04 | 🟢 | Significato `status` 1 vs 2 | Annotare (precaricato/caricato vs presente/attivo) | ✅ 29/10: `status` 1 = presente, non caricata (campi vuoti); 2 = caricata (`active_tray_id` = quello slot). |
| HW-M5-05 | 🔴 | Stampa 2 colori con `slot_map` (`t` vs `slot`) | Colori corretti | |

**Comandi CANVAS dalla farm (M5, 30/10).** Si accendono come le voci M4, una capability alla volta in `ALVEOLAB_ELEGOO_ENABLE`. Stampante ferma, operatore davanti. Ordine proposto: 06 → 07 → 08 → 09.

| ID | Rischio | Test | Atteso | Esito |
|---|---|---|---|---|
| HW-M5-06 | 🟡 | `multicolor_slot_edit`: dalla card, slot 1 → Generic, PLA Matte, colore blu (metodo 2003) | `error_code 0`; touchscreen e `GET …/telemetry` mostrano PLA Matte blu; nessun movimento | ✅ 31/10 (fw 02.01.01.04): dalla card slot 1 (`tray_id` 0) da Generic PLA Matte `FFFFFF` a `2850DF`; 2003 accettato (PUT 200), telemetria, card e touchscreen (visto da Salvo) blu, stampante ferma. Rimesso `FFFFFF` allo stesso modo, telemetria identica a prima. `multicolor_slot_edit` resta acceso nel `.env` del CT 100. |
| HW-M5-07 | 🟢 | `multicolor_auto_refill`: interruttore acceso e spento (2004 `{auto_refill}`) | Il flag cambia anche sul touchscreen; `multicolor_auto_refill` segue | ✅ 31/10: acceso dall'interruttore della card (2004 accettato, PUT 200), telemetria `true`, touchscreen attivo (visto da Salvo); spento con lo stesso `PUT …/canvas/auto-refill` `{"enabled": false}`, telemetria `false`, touchscreen spento. Stampante ferma, idle. `multicolor_auto_refill` resta acceso nel `.env` del CT 100. |
| HW-M5-08 | 🔴 | `multicolor_load_unload`: carica dallo slot 1 (2001) con stampante ferma | Il filamento arriva all'ugello; risposta entro 5 min; `active_tray_id` = slot 1 | |
| HW-M5-09 | 🔴 | Scarica lo slot caricato (2002) | Il filamento torna nel CANVAS; nessun errore sul touchscreen | |

## M2 — Prima connessione: procedura consigliata

1. Discovery broadcast (HW-M2-01). Se la farm gira in Docker bridge, usare `targets` con l'IP.
2. Aggiungere la stampante da API/UI con il seriale **esatto** restituito dalla discovery.
3. Abilitare il logging della stampante e lasciare girare 10 minuti idle: verificare PING/PONG regolari e nessuna riconnessione.
4. Solo dopo, una stampa avviata dal touchscreen per HW-M2-05…08.

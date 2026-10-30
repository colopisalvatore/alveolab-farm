# ALVEOLAB Farm — Piano di implementazione

Aggiornato: 2026-10-30. Ogni milestone è piccola, verificabile e lascia il percorso Bambu invariato.

## Stato

| Milestone | Stato | Esito |
|---|---|---|
| **M0** Audit e baseline | ✅ | `ARCHITECTURE_AUDIT.md`; remote `upstream` configurato; baseline test eseguita. |
| **M1** Astrazione provider | ✅ | `services/printer_providers/` (contratti, capability, normalizzazione Bambu); colonna `printers.provider`; `PrinterManager` multi-provider; guardia scheduler; API capability/telemetria. |
| **M2** ELEGOO read-only | ✅ vertical slice (solo fixture) · ⏳ verifica hardware | Protocollo CC2, normalizzatore, client MQTT con registrazione/heartbeat/riconnessione, discovery UDP, registrazione manuale via API. **Mai provato su una CC2 reale.** |
| M3 Integrazione UI | ✅ | Card per capability (Bambu invariata; ELEGOO: solo monitoraggio, niente Stampa/camera/file/controlli/HMS/diagnostica), pannello CANVAS in sola lettura, "Aggiungi stampante" con scelta produttore e ricerca ELEGOO per IP, motivo preciso se l'aggiunta fallisce, stampanti non Bambu escluse dai flussi solo Bambu (invio stampa, muro camere, rilevamento guasti, pipeline, stampante virtuale, AMS SpoolBuddy, profili K). Badge errori ELEGOO (codici riportati dalla stampante) e azioni di massa limitate alle Bambu, con avviso delle stampanti saltate. |
| M4 Controllo e upload | 🟡 codice pronto · ⏳ prove hardware | Upload HTTP, avvio/pausa/ripresa/stop, coda verso la Centauri con file controllato (solo file slicati per CC2), fine lavoro dai push, archivio e piatto da liberare. **Comandi spenti di default**: si accendono uno per uno con `ALVEOLAB_ELEGOO_ENABLE` dopo ogni prova HW-M4. Dettagli sotto. |
| M5 CANVAS e inventario | ⏳ | — |
| M6 Produzione multibrand | ⏳ | — |
| M7 Branding e consolidamento | ⏳ | — |

## File toccati da M0–M2

Nuovi: `backend/app/services/printer_providers/**`, `backend/app/api/routes/printer_providers.py`, `backend/tests/_fixtures/elegoo_cc2.py`, `backend/tests/unit/services/test_printer_providers.py`, `backend/tests/unit/services/test_elegoo_cc2.py`, `backend/tests/unit/test_scheduler_provider_guard.py`, `backend/tests/integration/test_printer_providers_api.py`, `docs/alveolab-farm/*`.

Patch su file upstream (piccole, per merge facili): `models/printer.py` (+1 colonna), `core/database.py` (+1 migrazione), `schemas/printer.py` (provider, validatori), `services/printer_manager.py` (mappa provider), `services/print_scheduler.py` (guardia), `api/routes/printers.py` (probe ELEGOO in create), `main.py` (+1 router), `tests/unit/test_printer_schema.py` (+4 test).

## Prossimi passi

### M2 — chiusura (bloccante per M3/M4)
1. Eseguire HW-M2-01…13 (`HARDWARE_TEST_CHECKLIST.md`) con autorizzazione.
2. Correggere le ipotesi smentite (scala ventole, continuità push, client id).
3. Salvare 3-5 messaggi reali (anonimizzati) come nuove fixture.

### M3 — Integrazione UI (frontend)
- `Printer` in `frontend/src/api/client.ts`: campo `provider`; chiamata a `/printer-providers/printers/{id}/capabilities`.
- Dialog "Aggiungi stampante": selettore produttore (Bambu Lab / ELEGOO), discovery ELEGOO, access code facoltativo.
- Card stampante: badge produttore/modello; nascondere o disabilitare (con tooltip dalle `notes`) i controlli Bambu-only (AMS, HMS, jog, temperature, camera Bambu, file manager FTP) quando la capability manca.
- Pannello CANVAS read-only da `telemetry.multicolor_slots` (con dicitura "colore dichiarato/non verificato").
- Escludere le stampanti non-Bambu da Obico, dal file manager FTPS e dalla diagnostica connessione Bambu (oggi falliscono "pulite" perché `get_client()` è `None`, ma vanno nascoste).
- Test Vitest per la gestione capability.

### M4 — Controllo e upload ELEGOO
- Pipeline file: modello sorgente → profilo di slicing ELEGOO → file macchina. Rifiutare i 3MF slicati per Bambu (`sliced_for_model`/metadati) verso una CC2.
- Upload HTTP `PUT /upload` (chunk 1 MiB, MD5, `X-Token`) e avvio 1020; pausa 1021; stop 1022; ripresa 1023 solo se HW-M4-05 la conferma.
- **Avvio idempotente:** chiave di job persistita prima dell'invio; dopo una riconnessione non si reinvia 1020 se lo stato riporta già `printing` con lo stesso `filename`/`uuid`; nessun retry automatico dell'avvio.
- Capability `job_*`, `file_upload` attivate solo dopo HW-M4 superati; `queue_dispatch` per ELEGOO tramite un percorso di dispatch separato (non il percorso FTPS Bambu).
- Archivio: callback start/complete per ELEGOO; fine lavoro solo da `finished`/`cancelled`/`failed` riportati dalla macchina, mai da disconnessione.

### M5 — CANVAS e inventario
- Associazione manuale bobina inventario ↔ (stampante, `canvas_id`, `tray_id`) (estendere `SpoolAssignment` o tabella parallela per non toccare la semantica AMS).
- Controllo pre-avvio: `color_map` del file vs slot CANVAS (tipo + colore), blocco se discordanti, override esplicito.
- Preset multicolore per produzione ripetitiva.

### M6 — Produzione ALVEOLAB/DEKO
- Riutilizzare `PrintBatch` + `PrintBatchPlate` (già esistenti) aggiungendo brand, prodotto, variante/dimensione, colore/materiale, ordine cliente; assegnazione a stampanti idonee per capability.
- Prodotti ricorrenti (svuotatasche moto 220/130 mm, decorazioni casa) come template di lotto.

### M7 — Branding e consolidamento
- Nome "ALVEOLAB Farm" in `index.html`, `manifest.json`, `config.app_name`, icone; **mantenere** licenza AGPL, copyright e attribuzione a Bambuddy (README, About, footer).
- Documentazione utente.

## Branding (anticipo di M7, 29/10/2026)

- Colori dal logo ufficiale (`Pictures/Vettoriali/Alveolab.svg`): giallo miele `#f5b817` / `#fdcd00`, scuro `#d99a00`, nero brand. Nuovo accent `alveolab` (default chiaro e scuro, selezionabile in Impostazioni → Aspetto insieme ai colori originali). Sulle superfici gialle il testo bianco diventa nero (`index.css`, contrasto).
- Logo ALVEOLAB (`frontend/public/img/alveolab_farm_logo.png`) in sidebar, header mobile, login e setup; favicon/icone PWA rigenerate dal logo; titolo e manifest "ALVEOLAB Farm".
- **Non toccati (attribuzione AGPL/Bambuddy):** link GitHub Bambuddy in sidebar, overlay "powered by Bambuddy", LICENSE, README. I 106 testi i18n che citano "Bambuddy" restano per ora (M7).
- `static/` (build committato da upstream) non viene ricommittato: l'immagine Docker ricompila il frontend.
- Il banner "aggiornamento disponibile" segue ancora i rilasci upstream: **non** aggiornare il container con l'immagine `ghcr.io/maziggy/bambuddy` (perderebbe ELEGOO e branding); si aggiorna facendo merge di upstream nel fork e ricostruendo.

## Deploy home lab (CT 100, 192.168.1.101)

Compose: `deploy/alveolab-farm/docker-compose.yml`. Stesso nome container (`bambuddy`), rete host, porta 8100, **stessi volumi** (`bambuddy_bambuddy_data`, `bambuddy_bambuddy_logs`) → dati invariati; la sola migrazione DB è l'aggiunta di `printers.provider`.

```bash
# sul CT 100
cd /opt/alveolab-farm                     # sorgente del branch (git archive dal PC)
docker compose -f deploy/alveolab-farm/docker-compose.yml build
docker tag ghcr.io/maziggy/bambuddy:latest bambuddy-rollback:pre-alveolab
(cd /opt/bambuddy && docker compose down)                     # i volumi named restano
docker run --rm -v bambuddy_bambuddy_data:/data -v /root/backup:/b alpine \
  tar czf /b/bambuddy-data-pre-alveolab-$(date +%Y%m%d).tgz -C /data .
docker compose -f deploy/alveolab-farm/docker-compose.yml up -d
```
Rollback: `docker compose -f deploy/alveolab-farm/docker-compose.yml down`, ripristino del tgz nel volume se serve, poi `cd /opt/bambuddy && docker compose up -d` (immagine originale taggata `bambuddy-rollback:pre-alveolab`). La colonna `provider` in più è ignorata da Bambuddy upstream.

## Sincronizzazione con upstream

```bash
git fetch upstream
git checkout -b sync/upstream-YYYYMMDD
git merge upstream/main          # conflitti attesi solo nei file della lista "Patch su file upstream"
cd backend && ruff check && ruff format --check
pytest tests/ -n auto --ignore=tests/unit/services/test_bambu_ftp.py
```
Cadenza consigliata: ogni rilascio upstream o almeno mensile. Confrontare i fallimenti con la baseline (su Windows 71 fail + 16 error preesistenti, vedi audit §10).

## Decisioni conservative prese senza chiedere

| Decisione | Motivo |
|---|---|
| Provider produce `PrinterState`, `get_client()` resta Bambu-only | Massima compatibilità e minimo conflitto upstream. |
| Solo metodi 1001/1002/2005 inviabili in M2 (blocco a codice) | Nessun comando reale prima dei test hardware autorizzati. |
| Password CC2 = access code o default SDK | Comportamento SDK ufficiale. |
| Client id stabile derivato dal seriale | Evita di esaurire gli slot client della stampante. |
| Ventole lette come PWM 0-255 | Dati reali community; da verificare (HW-M2-05). |
| `tray_id` non rinumerato; `source` CANVAS = `unknown` | Fonti in conflitto; nessuna simulazione. |
| Nessuna modifica frontend in M0–M2 | Il prompt pone la UI in M3; API già pronte. |


## M4 — come è fatto (30/10/2026)

**Accensione per voce.** Le capability `file_upload`, `job_start`, `job_pause_resume`, `job_stop` della CC2 esistono solo se elencate nella variabile d'ambiente `ALVEOLAB_ELEGOO_ENABLE` del container (`.env` accanto a `deploy/alveolab-farm/docker-compose.yml`). `queue_dispatch` si accende da sola quando ci sono sia `file_upload` sia `job_start`. Vuota = sola lettura, come in M3. Scelta voluta: accendere un comando che muove la macchina è una decisione di deploy, non un clic.

**Comandi** (`elegoo/client.py`):
- costruiti da `protocol.build_job_request`, separato da `build_request` (solo lettura);
- QoS 0 e mai ripetuti: paho ripubblica i QoS 1 non confermati dopo una riconnessione, che poteva avviare due volte la stessa stampa;
- esiti espliciti: `accepted`, `rejected` (codice della stampante), `no_reply` (inviato, esito sconosciuto, nessun nuovo tentativo), `not_sent`, `refused` (rifiutato dalla farm);
- consentiti solo dagli stati sensati: pausa da `printing`, ripresa da `paused`, stop da uno stato attivo, avvio da `idle`/`finished`/`cancelled`/`failed`;
- l'avvio ha una chiave di lavoro (`billing_run_id` della riga di coda, scritta nel DB **prima** dell'invio) e il client rifiuta una seconda partenza con la stessa chiave.

**File** (`elegoo/files.py`): alla CC2 va solo un `.gcode` o un 3MF slicato che dichiara, nelle impostazioni dello slicer (`printer_model` / `printer_settings_id`), di essere per una Centauri 2. Bambu, Centauri Carbon di prima generazione, file senza indicazione, 3MF non slicati: rifiutati. La libreria accetta i `.gcode` grezzi solo se slicati per la CC2 (per le Bambu resta il messaggio di upstream) e ne salva il `sliced_for_model`.

**Coda** (`print_scheduler._start_print_provider`): controllo capability → controllo file → upload (innocuo, salva solo il file) → CAS `pending → printing` con nuova chiave → un solo avvio. Niente preriscaldo, iniezione G-code, FTPS, mappatura AMS, controllo filamento AMS, watchdog che rimette in coda. Se l'avvio resta senza risposta la riga resta `printing` con l'avviso, e decide lo stato della stampante.

**Fine lavoro** (`client._track_job` → `services/provider_jobs.py`): eventi `started` / `ended` solo con collegamento attivo. Esito: quello riportato dalla macchina (2077 finita, 2504 annullata, emergenza = fallita); torna `idle` dopo ≥ 99% = finita (osservato sulla nostra CC2 il 29/10, segnato come dedotto); collegamento perso durante il lavoro o `idle` a metà = **esito non osservato**, la riga va in `failed` con un messaggio che chiede di controllare il pezzo. Una disconnessione da sola non chiude mai niente. A ogni fine, piatto da liberare prima del lavoro successivo. Le stampe avviate da Orca o dal touchscreen non creano archivio (la CC2 non permette di scaricare il file).

**Interfaccia:** card della Centauri con "Stampa un file" (solo file della libreria slicati per CC2, opzione livellamento), pausa/ripresa/stop con le stesse conferme della card Bambu. Senza capability accese resta l'avviso di sola lettura. Il pulsante Stampa e il trascinamento file restano solo Bambu.

**Valori scelti senza prova hardware** (da confermare con HW-M4): `print_layout` "A" (piatto testurizzato, come l'esempio SDK), `printer_check` = livellamento solo se richiesto, `delay_video` e `bedlevel_force` spenti, `slot_map` vuoto (vale la mappatura del file; CANVAS manuale è M5).

# Deploy ALVEOLAB Farm 1.3.1 (branch `alveolab/v1.3.0`)

Target: CT 100, `root@192.168.1.101`, stack in `/opt/alveolab-farm`, container `bambuddy`,
volumes `bambuddy_bambuddy_data` / `bambuddy_bambuddy_logs` (external), porta 8100.
Needs Salvo's OK for every SSH step. Never `rm -rf`.

## 0. Before (no SSH)

- [ ] Branch `alveolab/v1.3.0` pushed (ask Salvo), compose file `deploy/alveolab-farm/docker-compose.yml` committed (it was missing from the 1.3.1 base).
- [ ] Jarvis branch `fix/farm-build-plate-alveolab` (commit `01b35a4`) ready to merge/release together: the old `/library/files/build-plate` now answers 405.
- [ ] Choose a moment with few printers running. Centauri 2 job running = wait (a restart mid-print lost the job link before, HW-M4-05).
- [ ] Decide the four 1.3.1 items (section 5).

## 1. Look and back up (SSH, read-mostly)

```bash
ssh root@192.168.1.101 'docker ps --format "{{.Names}} {{.Status}}"; docker images alveolab-farm; ls -la /opt/alveolab-farm/deploy/alveolab-farm/; df -h /var/lib/docker /opt'
```

Free space matters: pool `local-lvm` was at 86.8%. The DB is ~78 MB, the image a few hundred MB.

Backup (container keeps running, `.backup` is consistent):

```bash
ssh root@192.168.1.101 '
set -e
D=/opt/backup-pre130-$(date +%Y%m%d-%H%M); mkdir -p $D
docker exec bambuddy sh -c "python - <<PY
import sqlite3
s=sqlite3.connect(\"/app/data/bambuddy.db\"); d=sqlite3.connect(\"/app/data/bambuddy.pre130.db\"); s.backup(d); d.close()
PY"
docker cp bambuddy:/app/data/bambuddy.pre130.db $D/bambuddy.db
cp /opt/alveolab-farm/deploy/alveolab-farm/.env $D/env.bak
docker tag alveolab-farm:latest alveolab-farm:pre-130
ls -la $D; docker images alveolab-farm'
```

Check: `bambuddy.db` size ~78 MB, `env.bak` present, image `alveolab-farm:pre-130` listed.
If `sqlite3`/python is not in the container, stop the container instead and copy the file from the volume.

## 2. Deploy (new folder, no overwrite of the old tree)

`tar -x` over the old tree would keep deleted files (old `printer_providers/`, old routes) inside the
Docker build context. Extract into a new folder instead and carry only the `.env`:

```bash
cd /c/Dev/alveolab-farm   # branch alveolab/v1.3.0, clean
git -c core.autocrlf=false archive --format=tar HEAD | ssh root@192.168.1.101 \
 'mkdir -p /opt/alveolab-farm-130 && tar -x -C /opt/alveolab-farm-130 \
  && cp /opt/alveolab-farm/deploy/alveolab-farm/.env /opt/alveolab-farm-130/deploy/alveolab-farm/.env \
  && cd /opt/alveolab-farm-130 \
  && docker compose -f deploy/alveolab-farm/docker-compose.yml build \
  && docker compose -f deploy/alveolab-farm/docker-compose.yml up -d'
```

Same compose project name and container name (`bambuddy`), so `up -d` replaces the running container.
The old `/opt/alveolab-farm` stays untouched for rollback. First start converts the DB once (one way).

## 3. Checks (in order)

1. `docker ps`: `bambuddy` healthy. `docker logs --tail 200 bambuddy`: no traceback, migrations ran, `alveolab` module loaded.
2. `GET /api/v1/printers/`: every printer present; P1S/A1 reconnect (state not None), Centauri 2 visible with `provider: elegoo`.
3. UI: yellow ALVEOLAB branding, Printers, Queue (outside-queue card), Production page, Library.
4. `GET /api/v1/production/recipes`, `/needs`, `/demand`: same counts as before the deploy.
5. Jarvis (new branch deployed): printers, farm snapshot, needs, queue, archives read OK; `build-plate` on `/alveolab/library/build-plate`.
6. One test print from the queue on a P1S/A1 (plain `.gcode` for the Centauri, never 3MF).
7. MakerWorld import of one model.
8. Centauri: status live, then start/stop only if `ALVEOLAB_ELEGOO_ENABLE` in `.env` has the commands on (not tested with real hardware in this branch).
9. WebSocket page open 15+ min: data keeps updating (75 s silence fix).

## 4. Rollback

```bash
ssh root@192.168.1.101 '
cd /opt/alveolab-farm && docker compose -f deploy/alveolab-farm/docker-compose.yml down
docker tag alveolab-farm:pre-130 alveolab-farm:latest
docker run --rm -v bambuddy_bambuddy_data:/data -v /opt/backup-pre130-XXXX:/b alpine cp /b/bambuddy.db /data/bambuddy.db
cd /opt/alveolab-farm && docker compose -f deploy/alveolab-farm/docker-compose.yml up -d --no-build'
```

The DB restore is required: 1.3.1 converted it and the old image may not read it. Restore the Jarvis release too (old build-plate path).

## 5. Decide at deploy

- Plate moves at print end for the camera photo (#3240, on by default): turn off in Settings > General if A1 bed-slingers suffer.
- Jobs wait when filament does not match (#2799): check Jarvis jobs do not sit blocked.
- Archives count prints, not files: costs and exports change; Jarvis reads archives (checked: reads OK).
- DB conversion is one way (rollback = restore backup).
- Also update container `orca-slicer-api` (new filament lists, renamed presets) with Aldo.

## 6. Known gaps / out of scope

- Not tested with live hardware: connected printers, real print, Centauri start/stop.
- Full backend suite not re-run after the last guard-test fixes (touched files pass).
- Farm is exposed without login (`auth_enabled=false`); Jarvis sends ~58 `POST /production/demand` every 10 min.

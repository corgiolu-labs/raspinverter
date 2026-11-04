# raspinverter — convenzioni di lavoro

Monitor e gestione di un impianto solare **off-grid**, 24/7 su un **Raspberry Pi 3B**:
Modbus RTU verso l'inverter EASUN, correnti Hall + ADS1115 via I2C, stato di carica a
conteggio di coulomb, bilanciamento banchi e commutazione rete, dashboard PWA Flask +
SQLite. Il `README.md` descrive funzioni e hardware: qui ci sono le convenzioni.

## Questo codice governa un impianto vero, acceso

Non è un prototipo da banco. Il Pi pilota **tre relè**: bilanciamento dei due banchi e
il **relè rete**, che commuta sulla rete quando la serie scende sotto **46 V**. Una
modifica sbagliata può scaricare il pacco o lasciare la casa senza commutazione.

Conseguenze pratiche:

- Niente modifiche "al volo" sulla logica relè o sulle soglie senza averle discusse.
- Il poll gira ogni 5 s in un thread daemon: attenzione a non bloccarlo.
- La lettura I2C avviene **a prescindere** dall'esito Modbus — è voluto, non è un bug:
  le correnti di batteria devono restare misurate anche se l'inverter non risponde.

## Accesso al Pi

Indirizzi completi in `Desktop\COMANDI DISPOSITIVI\COMANDI RASPYNVERTER.txt`.
Il più stabile è **Tailscale**: `ssh raspynverter@100.74.218.113`, dashboard su
`https://raspynverter.tailb0ef4c.ts.net`. Via cavo/ICS è `.222`, su Wi-Fi di casa
`192.168.10.83`, su Ethernet riservato `192.168.10.50`.

**La chiave SSH funziona.** `sudo -n systemctl restart inverter.service` funziona
**senza password**: `/etc/sudoers.d/inverter-deploy` concede NOPASSWD per start/stop/
restart/daemon-reload **di quella sola unit**. Tutto il resto (`sudo reboot`, file di
sistema) chiede la password, e da sessione non locale polkit rifiuta comunque
`nmcli con add/down`: le modifiche di rete **le fa l'utente**.

## Servizi

`inverter.service` (dashboard + poll), `inverter-learn.service` + `.timer`.
Dopo un deploy il riavvio del servizio lo esegue l'utente.

## Struttura

| | |
|---|---|
| `inverter_api.py` | Flask porta 8000, ~30 endpoint, poll loop, init DB |
| `database.py`, `config.py`, `hardware.py` | persistenza, configurazione, I/O fisico |
| `daily_analyzer.py`, `learn.py` | analisi giornaliera e apprendimento |
| `web/` | PWA con Chart.js **vendorizzato** — niente CDN, deve funzionare offline |
| `config/inverter_config.json` | taratura **dell'impianto reale**, in `.gitignore`: usare `inverter_config.example.json` come modello |
| `old/` | materiale pre-riscrittura, vedi sotto |

## Backup del database: la trappola già pagata

Il DB SQLite è in WAL. **Copiare i file a caldo produce un archivio inservibile**: una
copia del 2026 fatta così è risultata troncata e corrotta — `archive`,
`daily_analysis`, `battery_counters` e `i2c_snapshots` illeggibili, di `samples`
recuperabile un solo giorno. È stata buttata il 04/11/2026.

Per un backup valido: fermare il servizio, **oppure** usare `sqlite3 .backup` / l'API
di backup online di SQLite. Mai `cp` sul file vivo. Sul Pi c'e' `~/backup_db.py`, che
fa lo snapshot con l'API, lo valida con `integrity_check` **prima** di tenerlo, lo
comprime e ne ruota tre; gira da cron alle 02:30, dopo la manutenzione delle 02:00.

Il VACUUM scrive tutto nel WAL: dopo un'archiviazione con `vacuum=1` il file principale
e' piccolo ma il `-wal` resta grosso finche' non si fa
`pragma wal_checkpoint(TRUNCATE)`.

## `i2c_snapshots` si pota, non si conserva

Serve solo al grafico del bilanciamento tensioni **del giorno corrente**. Non potarla
e' costato un DB da 2,78 GB con 2,25 milioni di righe (novembre 2026). Oggi
`I2C_RETENTION_DAYS = 14` in `database.py`, applicata da `_archive_compute_and_apply`:
non allungare la finestra senza un motivo, il Pi ha una SD.

## `old/`

Versione precedente alla riscrittura (aprile 2026), ridotta a ciò che può servire:

- **`old/tests/`** — 8 test della vecchia API. **Il repo attuale non ha test**: sono
  la base da cui ripartire, ma presuppongono la struttura `backend/` che non esiste
  più e vanno adattati alla forma piatta.
- `old/docs/ARCHITECTURE.md` — architettura a layer, utile se si rimodularizza.
- `old/graphs/monthly/` — 12 grafici agosto-settembre 2025: **unica traccia superstite
  di quel periodo**, i dati sorgente sono persi.

Il resto (backend, src, scripts, deploy, config, requirements) è stato scartato: la
versione nella radice è testata e funzionante ed è sempre il punto di partenza.

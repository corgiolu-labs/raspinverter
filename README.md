# RASPINVERTER

Monitoraggio di un inverter tramite **Raspberry Pi**: lettura **Modbus RTU** su interfaccia seriale (tipicamente RS-232 / UART), normalizzazione dei registri, persistenza in **SQLite** e interfaccia web (dashboard + impostazioni + analisi giornaliera).

## Descrizione del progetto

Il sistema interroga l’inverter in **holding registers** Modbus, applica scale e conversioni (inclusi valori **signed** dove serve) e calcola grandezze derivate (es. corrente di rete da P/V, fattore di potenza del carico). I campioni vengono salvati nel database e resi disponibili tramite API REST consumate dal frontend in `backend/web/`.

**Nota:** la mappa registri e le scale sono il frutto di **reverse engineering / adattamento** sul campo; vanno verificate sul proprio modello di inverter e firmware.

## Funzionalità principali

- **Acquisizione dati**: polling configurabile, seriale Modbus RTU (`pymodbus`, fallback opzionale `minimalmodbus`).
- **“Decodifica” protocollo**: tabella registri → nomi fisici (PV, batteria, rete, carico, temperature, bus DC, ecc.).
- **Dashboard e API**: server **Flask** con storico giornaliero, energie aggregate, totali, relay su GPIO (opzionale), sensori **I2C** (opzionale, es. ADS1115).
- **Analisi giornaliera**: modulo `src/daily_analyzer.py` con endpoint dedicati e pagina analisi.
- **Script di supporto**: test lettura in tempo reale (`scripts/realtime_inverter_test.py`), generazione grafici da analisi salvate (`scripts/auto_graph_generator.py`, richiede matplotlib/pandas).

## Architettura (flusso dati)

```text
Inverter  --[RS-232 / UART Modbus RTU]-->  Raspberry Pi
                                              |
                                              v
                         backend/inverter_api.py  (entrypoint)
                         poll thread: Modbus + I2C -> SQLite
                         Flask: backend/app.py + routes/* (api_routes orchestrator)
                                              |
                                              v
                                    browser  -->  backend/web/*
```

- Il **Pi** esegue il servizio Python: il **thread di polling** legge Modbus (e opzionalmente I2C) e persiste in `data/inverter_history.db`; **Flask** espone le stesse API e gli asset statici di prima.
- Il backend è stato **suddiviso in moduli** (`config`, `db`, `services`, `routes`, `models`) mantenendo **compatibilità di avvio** da `backend/inverter_api.py` e lo stesso file di configurazione JSON. Dettaglio moduli: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Project structure (backend)

```text
backend/
  __init__.py
  inverter_api.py    # entrypoint: sys.path, db_init, relay, poll_loop, app.run
  app.py             # create_app(), Compress, after_request, register_routes
  config.py          # path, JSON config, env override seriale/polling/I2C
  db.py              # SQLite: connessione, schema, trim/archivio
  poll_state.py      # stato condiviso poll thread <-> API (ultimo campione, lock, stop)
  import_paths.py    # aggiunta idempotente di repo src/ (daily_analyzer)
  routes/
    api_routes.py    # orchestratore: chiama register_*_routes sui sotto-moduli
    static_routes.py
    health_routes.py
    analysis_routes.py
    config_routes.py
    inverter_routes.py
    i2c_routes.py
    energy_routes.py # history, energy, totals/today, maintenance/archive
    battery_routes.py
    relay_routes.py
  services/
    modbus_service.py
    i2c_service.py
    relay_service.py
    battery_service.py
    inverter_query_service.py   # payload /api/inverter
    energy_query_service.py     # finestre e aggregazioni energia/storico
    config_service.py           # merge/persist config da POST /api/config
  models/
    register_map.py  # REGS / signed / blocchi lettura Modbus
  web/               # frontend statico (invariato)
```

## Test automatici

Dalla **radice del repository** (nessun hardware richiesto; usano `unittest` della standard library):

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

**Cosa è coperto:** smoke `create_app()`, validazione config (`validate_config`), helper energia/finestre temporali, costruzione payload inverter con dati fittizi (DB reale per `battery_net_wh` mockato nei test).

**Cosa non è coperto:** accesso seriale Modbus, GPIO, I2C reale, polling in thread, contenuto reale del database di produzione.

`requirements-dev.txt` è opzionale (i test non aggiungono dipendenze obbligatorie oltre `requirements.txt`).

## Development notes

- Avvio locale dalla **radice del repo**: `python backend/inverter_api.py` oppure `python scripts/run_backend.py` (imposta CWD sulla root del repo e avvia lo stesso entrypoint).
- Variabili d’ambiente opzionali: vedi `.env.example` (solo esempi non sensibili; stessi nomi già supportati da `config.py`).
- Per modificare la **mappa Modbus**, intervenire su `backend/models/register_map.py` (non più nel monolite `inverter_api.py`).
- Logging: `logging` per modulo (`logging.getLogger(__name__)`); messaggi di startup in `inverter_api` / `app` / `routes.api_routes`. Nei loop di polling gli errori non bloccanti usano soprattutto `logger.debug` per evitare rumore.
- Path Python: `backend/` viene aggiunto all’avvio da `inverter_api.py`; `import_paths.ensure_src_path()` centralizza l’accesso a `src/` (usato anche da `create_app()`).

## Production notes

- Eseguire il servizio con working directory coerente con il repo (o path assoluti in `INVERTER_CONFIG` se necessario).
- Esempio **systemd**: `deploy/raspinverter.service.example` — copiare in `/etc/systemd/system/`, personalizzare `User`, `WorkingDirectory` e `ExecStart` (venv), poi `systemctl enable --now`.
- `threaded=True` su Flask è mantenuto come in precedenza.
- Database: stesso file SQLite sotto `data/`; schema invariato — backup periodico consigliato in produzione.

## Requisiti

- Python 3.10+ consigliato.
- Dipendenze runtime: `requirements.txt` (Flask, pymodbus, pyserial; opzionali smbus2/GPIO su Pi).
- Opzionale sviluppo: `requirements-dev.txt` (solo note; i test usano `unittest` incluso in Python).

## Configuration

Prima di eseguire il sistema, crea il file di configurazione locale (`config/inverter_config.json` è elencato in `.gitignore` e non va committato).

1. Copia l’esempio:

```bash
cp config/inverter_config.example.json config/inverter_config.json
```

Su Windows (PowerShell dalla radice del repo):

```powershell
Copy-Item config\inverter_config.example.json config\inverter_config.json
```

2. Modifica i parametri in `config/inverter_config.json` in base al tuo inverter e all’hardware (porta seriale, `unit_id`, batteria/SOC, relay, I2C). Il file `inverter_config.example.json` in repository è il riferimento di struttura; il file locale può contenere valori reali.

**Nota:** personalizza solo la configurazione JSON; non è necessario modificare la logica applicativa per adattare seriale, relay o sensori.

Variabili d’ambiente (opzionali) possono ancora sovrascrivere la seriale e il polling: vedi sezione Connessione inverter.

## Utilizzo

Dalla **radice del repository** (così `config/` e `data/` risolvono correttamente):

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python backend/inverter_api.py
```

Apri il browser su `http://<indirizzo-pi>:8000` (porta modificabile con variabile d’ambiente `PORT`).

### Connessione inverter

1. Collegare l’inverter alla seriale del Pi (adattatore USB‑serial o UART GPIO, secondo l’impianto).
2. Configurare porta e parametri in `config/inverter_config.json` (creato come sopra) e/o con le variabili d’ambiente (es. `INVERTER_MODBUS_SERIAL_PORT`, `INVERTER_MODBUS_BAUDRATE`, `INVERTER_UNIT_ID`, `POLL_INTERVAL_SEC`). Su Raspberry Pi è comune `/dev/serial0`; su PC di prova spesso `COMx` (Windows) o `/dev/ttyUSB0` (Linux).

### Test rapido Modbus (senza avviare Flask)

```bash
python scripts/realtime_inverter_test.py --port COM3 --baud 9600 --unit-id 1
```

(Sostituire la porta con quella effettiva.)

### Grafici da analisi (opzionale)

```bash
python scripts/auto_graph_generator.py
```

Genera immagini sotto `graphs/` (cartella ignorata da git).

## Avvertenze

- **Hardware e sicurezza**: lavorare su tensioni e connessioni dell’inverter solo con competenza; questo software non sostituisce manuali o normative.
- **Compatibilità inverter**: registri e scale sono **specifici del dispositivo** usato nello sviluppo; riuso su altri modelli richiede verifica e possibili modifiche alla mappa in `backend/models/register_map.py`.
- **Reverse engineering**: il progetto assume una conoscenza sperimentale del protocollo; non è un prodotto certificato dal costruttore.

## Struttura repository

```text
RASPINVERTER/
├── backend/           # Flask app factory, route, servizi, web statico
├── src/               # Logica condivisa (es. daily_analyzer)
├── scripts/           # Utility, run_backend.py, test seriale / grafici
├── tests/             # unittest (smoke app, config, energia, inverter payload)
├── deploy/            # es. raspinverter.service.example (systemd)
├── config/            # inverter_config.example.json (template); inverter_config.json locale (ignorato)
├── data/              # Database SQLite (generato in esecuzione; non versionare .db)
├── docs/              # ARCHITECTURE.md e altri appunti
├── .env.example       # Esempi variabili d'ambiente (non sensibili)
├── requirements.txt
├── requirements-dev.txt
├── README.md
└── .gitignore
```

## License

This project is licensed under CC BY-NC 4.0.

Commercial use is not permitted without explicit authorization.

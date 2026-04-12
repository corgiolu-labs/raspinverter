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
                                    backend/inverter_api.py
                                    (poll + SQLite + API)
                                              |
                                              v
                                    browser  -->  backend/web/*
```

- Il **Pi** esegue il servizio Python, apre la porta seriale configurata e scrive i campioni in `data/inverter_history.db` (creata al primo avvio).
- Il **client** (browser sulla LAN o in locale) usa le route Flask per HTML/JS statici e le route `/api/*` per JSON.

## Requisiti

- Python 3.10+ consigliato.
- Dipendenze principali: vedere `requirements.txt` (Flask, pymodbus, pyserial; opzionali smbus2/GPIO su Pi).

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
- **Compatibilità inverter**: registri e scale sono **specifici del dispositivo** usato nello sviluppo; riuso su altri modelli richiede verifica e possibili modifiche alla mappa in `backend/inverter_api.py`.
- **Reverse engineering**: il progetto assume una conoscenza sperimentale del protocollo; non è un prodotto certificato dal costruttore.

## Struttura repository

```text
RASPINVERTER/
├── backend/           # Flask, API, frontend statico (web/)
├── src/               # Logica condivisa (es. daily_analyzer)
├── scripts/           # Utility e test seriale / grafici
├── config/            # inverter_config.example.json (template); inverter_config.json locale (ignorato)
├── data/              # Database SQLite (generato in esecuzione; non versionare .db)
├── docs/              # Appunti / diagrammi (opzionale)
├── requirements.txt
├── README.md
└── .gitignore
```

## License

This project is licensed under CC BY-NC 4.0.

Commercial use is not permitted without explicit authorization.

# Architettura backend (RASPINVERTER)

Panoramica dei moduli sotto `backend/` dopo il refactor conservativo: stesso runtime Flask/SQLite, stessi path API; il codice è solo organizzato per responsabilità.

## Flusso dati

1. **`inverter_api.py`** — entrypoint: aggiunge `backend/` e `src/` a `sys.path`, esegue `db_init()`, `relay_setup()`, avvia un thread **daemon** con `poll_loop()`, registra handler segnali/`atexit`, costruisce l’app con `create_app()` e chiama `app.run(threaded=True)`.
2. **`poll_loop()`** (in `inverter_api.py`) — in ciclo: `modbus_service.read_regs()`, snapshot I2C (`i2c_service`), aggiorna `poll_state.last_sample`, scrive su SQLite (`db`), `relay_auto_step()`, contatori batteria (`battery_service`).
3. **`app.py`** — `create_app()`: istanza Flask, opzionale Flask-Compress, header cache/charset, `register_routes(app)`.
4. **`routes/api_routes.py`** — tutte le route HTTP/API e i file statici sotto `web/` (stessi URL di prima).

## Moduli

| Modulo | Ruolo |
|--------|--------|
| `config.py` | Path repo, caricamento JSON, helper `_get`/`ev`, parametri seriale/polling/I2C, `validate_config`. |
| `db.py` | Connessione SQLite, init schema, trim/archivio, helper timestamp. |
| `models/register_map.py` | Mappa registri Modbus (`REGS`, signed, blocchi lettura). |
| `services/modbus_service.py` | Client pymodbus + fallback minimalmodbus, `LAST_OK`/`LAST_ERR`. |
| `services/i2c_service.py` | Lettura I2C opzionale, `LAST_I2C`. |
| `services/relay_service.py` | GPIO relay, `relay_apply` / `relay_auto_step`. |
| `services/battery_service.py` | Contatore energia netta batteria in DB. |
| `poll_state.py` | `stop_event`, `lock`, `last_sample` condivisi tra poll thread e route. |

## Dipendenze esterne

- **`src/daily_analyzer.py`** — importato dentro `register_routes` per gli endpoint di analisi giornaliera (path `src/` aggiunto dall’entrypoint).

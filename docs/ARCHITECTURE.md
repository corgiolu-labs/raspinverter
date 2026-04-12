# Architettura backend (RASPINVERTER)

Panoramica dei moduli sotto `backend/`: stesso runtime Flask/SQLite e stessi path API; organizzazione a layer incrementale (Phase 1 + Phase 2).

## Backend layers

| Layer | File / cartella | Responsabilità |
|-------|-----------------|----------------|
| Entry | `inverter_api.py` | `sys.path` (directory `backend/`), `import_paths.ensure_src_path()`, `db_init`, relay, thread `poll_loop`, segnali, `create_app()`, `app.run`. |
| Path | `import_paths.py` | Aggiunta idempotente di `repo/src` per `daily_analyzer` (evita duplicazione con `app.py`). |
| App factory | `app.py` | `create_app()`, Flask-Compress opzionale, `after_request`, `register_routes(app)`. |
| Config / persistenza | `config.py`, `db.py` | JSON, env, SQLite. |
| Dominio Modbus | `models/register_map.py` | Tabella registri. |
| Servizi | `services/*` | Modbus, I2C, relay, batteria. |
| Query / applicativo | `inverter_query_service.py`, `energy_query_service.py`, `config_service.py` | Logica di composizione JSON per route principali (senza Flask `request` dove possibile). |
| Stato runtime | `poll_state.py`, `*_service.LAST_*` | Campione in memoria, stop/lock; ultimo esito Modbus e snapshot I2C restano nei servizi (scelta esplicita, senza DI). |
| HTTP | `routes/*` | Route divise per area; `api_routes.py` solo orchestratore. |

## Runtime flow

1. Avvio processo → `inverter_api`: path → import moduli → `db_init()` → `relay_setup()` → thread **daemon** `poll_loop`.
2. `poll_loop`: sotto lock, `read_regs()` → I2C → aggiornamento `poll_state.last_sample` / `i2c_service.LAST_I2C` → INSERT `samples` / `i2c_snapshots` → `relay_auto_step` → batteria.
3. In parallelo: `create_app()` → `ensure_src_path()` → Flask + `register_routes(app)` → `app.run(threaded=True)`.

## Route modules

Ogni file espone `register_<area>_routes(app: Flask) -> None`. L’ordine di registrazione è definito in `routes/api_routes.py` (nessun Blueprint; stessi decorator `@app.route` di prima).

| File | Path principali |
|------|-----------------|
| `static_routes.py` | `/`, `/settings`, `/analysis`, asset sotto `/web` (CSS/JS/manifest/SW/icons/offline). |
| `health_routes.py` | `/api/health`, `/api/test` |
| `analysis_routes.py` | `/api/analysis/*` |
| `config_routes.py` | `/api/config` |
| `inverter_routes.py` | `/api/inverter` |
| `i2c_routes.py` | `/api/i2c/latest`, `/api/i2c/history` |
| `energy_routes.py` | `/api/history`, `/api/energy`, `/api/totals/today`, `/api/maintenance/archive` |
| `battery_routes.py` | `/api/battery/*` |
| `relay_routes.py` | `/api/relay/*` |
| `api_routes.py` | Istanza `DailyAnalyzer` e chiamate sequenziali ai `register_*` sopra. |

## Service layer (query / config)

Funzioni pure o quasi-pure che riducono la logica nelle route:

| Modulo | Contenuto |
|--------|-----------|
| `inverter_query_service.py` | Scelta campione DB vs memoria, SOC %, `stale_seconds`, relay, I2C, `battery_net_wh` (seconda query), campi `last_ok` / `last_error`. |
| `energy_query_service.py` | `normalize_energy_unit`, `parse_energy_window`, serie minuti storico, merge campioni+archivio, bucket hour/day/month/year, payload totali giornalieri. |
| `config_service.py` | `build_config_get_payload`, `merge_config_from_post`, `finalize_config_persist` (webhook legacy, scrittura file, `relay_setup`). |

Le route in `inverter_routes.py`, `energy_routes.py`, `config_routes.py` si limitano a I/O HTTP/SQL e chiamano questi servizi.

## Test (`tests/`)

Esecuzione dalla root del repo:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

- **Smoke:** `create_app()` e presenza route note.
- **Config:** `validate_config` con payload valido e due casi invalidi.
- **Energia:** unità, finestra temporale, riempimento minuti.
- **Inverter:** payload con mock di `_fetch_battery_net_wh` (nessun DB reale richiesto per quel valore).

Non coprono hardware, thread di polling né integrazione seriale/GPIO.

## Deploy

- `scripts/run_backend.py` — CWD = root repo, import di `inverter_api.main()`.
- `deploy/raspinverter.service.example` — unit systemd da adattare (utente, path, venv).

## Moduli di servizio (riepilogo)

| Modulo | Ruolo |
|--------|--------|
| `config.py` | Path repo, JSON, `validate_config`, override env. |
| `db.py` | Connessione, schema, archivio/trim. |
| `models/register_map.py` | `REGS`, signed, blocchi. |
| `services/modbus_service.py` | pymodbus / minimalmodbus, `LAST_OK` / `LAST_ERR`. |
| `services/i2c_service.py` | I2C opzionale, `LAST_I2C`. |
| `services/relay_service.py` | GPIO, `relay_apply`, `relay_auto_step`. |
| `services/battery_service.py` | Contatori batteria in DB. |

## Dipendenze esterne

- **`src/daily_analyzer.py`** — importato in `register_routes` (dopo `ensure_src_path()`).

## Technical debt / monolite residuo (intenzionale)

- **`poll_loop`** resta in `inverter_api.py` (orchestrazione breve, basso rischio di regressione).
- **Stato “ultima lettura”** distribuito: `poll_state.last_sample` vs `modbus_service.LAST_*` vs `i2c_service.LAST_I2C` — documentato in `poll_state.py`; unificare solo se serve un refactor più ampio.
- **Nessun Blueprint Flask** in questa fase: registrazione diretta su `app` per massima parità col comportamento precedente.
- **Silent `except` residui** possono esistere in servizi non toccati o in percorsi rari; la Phase 2 ha concentrato logging su poll loop, route critiche e DB mkdir.
- **SQL ancora nelle route** per `/api/history`, `/api/energy`, `/api/totals/today`: solo la parte di aggregazione/composizione risposta è nel service layer; spostare anche le query richiederebbe un refactor più ampio.

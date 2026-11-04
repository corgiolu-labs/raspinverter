# old/ — materiale pre-riscrittura

Recuperato dal vecchio albero del progetto (ultima modifica 13/04/2026), rimasto
fuori dal repo quando il backend e' stato riscritto nella forma piatta attuale.
**La versione di riferimento e' quella nella radice del repo: testata e funzionante.**
Qui c'e' solo cio' che puo' servire in futuro.

| | |
|---|---|
| `tests/` | 8 test della vecchia API (config, energy, inverter, smoke, validazione, helper energia, inverter_query_service). Il repo attuale non ha test: questi sono adattabili, presuppongono la struttura `backend/` che non esiste piu'. |
| `docs/ARCHITECTURE.md` | Architettura a layer del vecchio backend Flask. Utile se si vorra' rimodularizzare. |
| `graphs/monthly/` | 12 grafici di agosto-settembre 2025 (cicli batteria, produzione FV, consumi, efficienza). **Unica traccia superstite di quel periodo**: i dati sorgente non sono recuperabili. PNG ricompressi a palette 256 colori, 59% piu' leggeri, nessuna perdita visibile. |

## Cosa e' stato scartato e perche'

- `backend/`, `src/`, `scripts/`, `deploy/`, `config/`, `LICENSE`, `README`, `requirements*`:
  superati dalla versione corrente.
- `data/inverter_history.db` (+ `-wal`, `-shm`, 6,4 MB): **file troncato e corrotto**.
  L'integrity check referenzia pagine oltre la fine del file; `archive`,
  `daily_analysis`, `battery_counters` e `i2c_snapshots` illeggibili. Di `samples`
  erano recuperabili 16.021 righe, ma di un solo giorno (27/03/2026), non dello
  storico 2025. Cancellato: non conteneva quello che sembrava contenere.

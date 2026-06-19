#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Layer database SQLite + archivio/trim + contatori batteria.
Estratto da inverter_api.py (refactor 2026-06-06) — nessuna logica cambiata.
"""
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from config import DB_PATH, POLL_S, DEFAULT_NET_RESET_V, _get, _bool, now_str, parse_ts


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def db():
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    con = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA busy_timeout=5000;")
    return con


def db_init():
    print(f"[db] Initializing database...", flush=True)
    with db() as con:
        print(f"[db] Creating samples table...", flush=True)
        con.execute("""
            CREATE TABLE IF NOT EXISTS samples(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              timestamp TEXT NOT NULL,
              pv_w REAL, pv_v REAL, pv_a REAL,
              battery_w REAL, battery_v REAL, battery_a REAL,
              grid_w REAL, grid_v REAL, grid_hz REAL, grid_a REAL,
              load_w REAL, load_v REAL, load_hz REAL, load_a REAL, load_va REAL, load_pf REAL, load_percent REAL,
              dc_temp REAL, inverter_temp REAL, heatsink_temp REAL, dc_bus_v REAL
            );
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON samples(timestamp);")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS uniq_ts ON samples(timestamp);")

        print(f"[db] Creating archive table...", flush=True)
        con.execute("""
            CREATE TABLE IF NOT EXISTS archive(
              day TEXT PRIMARY KEY,
              pv_Wh REAL, load_Wh REAL, grid_Wh REAL,
              batt_in_Wh REAL, batt_out_Wh REAL
            );
        """)

        print(f"[db] Creating battery_counters table...", flush=True)
        con.execute("""
            CREATE TABLE IF NOT EXISTS battery_counters(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              counter_type TEXT NOT NULL,
              start_timestamp TEXT NOT NULL,
              start_battery_v REAL NOT NULL,
              total_batt_in_Wh REAL DEFAULT 0.0,
              total_batt_out_Wh REAL DEFAULT 0.0,
              total_batt_net_Wh REAL DEFAULT 0.0,
              reset_reason TEXT,
              created_at TEXT NOT NULL
            );
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_battery_counters_type ON battery_counters(counter_type);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_battery_counters_timestamp ON battery_counters(start_timestamp);")

        print(f"[db] Creating i2c_snapshots table...", flush=True)
        con.execute("""
            CREATE TABLE IF NOT EXISTS i2c_snapshots(
              timestamp TEXT PRIMARY KEY,
              data TEXT
            );
        """)

        print(f"[db] Committing changes...", flush=True)
        con.commit()
        print(f"[db] Database initialization completed", flush=True)


# ---------------------------------------------------------------------------
# Archive helpers: dry-run, sizes
# ---------------------------------------------------------------------------
def _db_files_size_bytes() -> int:
    total = 0
    try:
        total += os.path.getsize(str(DB_PATH))
    except Exception:
        pass
    for suf in ("-wal", "-shm"):
        try:
            total += os.path.getsize(str(DB_PATH) + suf)
        except Exception:
            pass
    return total


def _archive_compute_and_apply(cutoff: str, apply: bool) -> Dict[str, Any]:
    """Calcola righe da archiviare/cancellare; se apply=True esegue. Ritorna riepilogo."""
    summary: Dict[str, Any] = {}
    with db() as con:
        cnt = con.execute("SELECT COUNT(*) FROM samples WHERE timestamp < ?", (cutoff,)).fetchone()[0]
        summary["minutes_to_delete"] = int(cnt or 0)
        rows = con.execute("""
            WITH m AS (
              SELECT strftime('%Y-%m-%d %H:%M:00', timestamp) AS ts_min,
                     AVG(pv_w) AS pv_w,
                     AVG(battery_w) AS battery_w,
                     AVG(load_w) AS load_w,
                     AVG(grid_w) AS grid_w
              FROM samples
              WHERE timestamp < ?
              GROUP BY ts_min
            )
            SELECT
              date(ts_min) AS day,
              SUM(pv_w)/60.0        AS pv_Wh,
              SUM(load_w)/60.0      AS load_Wh,
              SUM(grid_w)/60.0      AS grid_Wh,
              SUM(CASE WHEN battery_w>0 THEN battery_w ELSE 0 END)/60.0  AS batt_in_Wh,
              SUM(CASE WHEN battery_w<0 THEN -battery_w ELSE 0 END)/60.0 AS batt_out_Wh
            FROM m
            GROUP BY day
            ORDER BY day ASC;
        """, (cutoff,)).fetchall()
        summary["days_to_archive"] = len(rows)

        if apply and rows:
            for r in rows:
                con.execute("""
                    INSERT OR REPLACE INTO archive
                      (day, pv_Wh, load_Wh, grid_Wh, batt_in_Wh, batt_out_Wh)
                    VALUES (?,?,?,?,?,?)
                """, (r["day"], r["pv_Wh"], r["load_Wh"], r["grid_Wh"], r["batt_in_Wh"], r["batt_out_Wh"]))
            con.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
            con.commit()
    return summary


# ---------------------------------------------------------------------------
# Battery net counter
# ---------------------------------------------------------------------------
def get_current_battery_counter():
    """Ottiene il contatore corrente della batteria netta o ne crea uno nuovo."""
    try:
        with db() as con:
            # Cerca il contatore attivo piu' recente
            row = con.execute("""
                SELECT * FROM battery_counters
                WHERE counter_type = 'daily_net'
                ORDER BY id DESC
                LIMIT 1
            """).fetchone()

            if row:
                return dict(row)
            else:
                print(f"[battery] Nessun contatore: ne creo uno nuovo", flush=True)
                now = now_str()
                cursor = con.execute("""
                    INSERT INTO battery_counters
                    (counter_type, start_timestamp, start_battery_v, created_at)
                    VALUES (?, ?, ?, ?)
                """, ('daily_net', now, 0.0, now))
                con.commit()
                new_id = cursor.lastrowid
                print(f"[battery] New counter created with ID: {new_id}", flush=True)
                return {
                    'id': new_id,
                    'counter_type': 'daily_net',
                    'start_timestamp': now,
                    'start_battery_v': 0.0,
                    'total_batt_in_Wh': 0.0,
                    'total_batt_out_Wh': 0.0,
                    'total_batt_net_Wh': 0.0,
                    'reset_reason': 'initial',
                    'created_at': now
                }
    except Exception as e:
        print(f"[battery] Error in get_current_battery_counter: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return None


def reset_battery_counter(reason="manual"):
    """Azzera il contatore della batteria netta e ne crea uno nuovo."""
    now = now_str()
    with db() as con:
        con.execute("""
            UPDATE battery_counters
            SET reset_reason = ?
            WHERE counter_type = 'daily_net'
            AND reset_reason IS NULL
        """, (reason,))
        cursor = con.execute("""
            INSERT INTO battery_counters
            (counter_type, start_timestamp, start_battery_v, created_at)
            VALUES (?, ?, ?, ?)
        """, ('daily_net', now, 0.0, now))
        con.commit()
        print(f"[battery] Contatore azzerato: {reason}", flush=True)
        return cursor.lastrowid


def set_battery_full(reason="full_charge"):
    """Calibra il contatore al 100%: net_Wh = capacita' nominale (SOC pieno).
    Usato dal tasto 'Batteria carica'. Speculare a reset_battery_counter (vuoto/0%)."""
    try:
        cap_wh = float(_get("battery.nominal_ah", 500)) * float(_get("battery.nominal_voltage", 51.2))
    except Exception:
        cap_wh = 0.0
    if cap_wh <= 0:
        cap_wh = 25600.0
    now = now_str()
    with db() as con:
        con.execute("""
            UPDATE battery_counters
            SET reset_reason = ?
            WHERE counter_type = 'daily_net'
            AND reset_reason IS NULL
        """, ("full_charge_calibration",))
        cursor = con.execute("""
            INSERT INTO battery_counters
            (counter_type, start_timestamp, start_battery_v, total_batt_in_Wh, total_batt_out_Wh, total_batt_net_Wh, reset_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ('daily_net', now, 0.0, cap_wh, 0.0, cap_wh, None, now))
        con.commit()
        print(f"[battery] Calibrazione PIENO: net={cap_wh:.0f} Wh (SOC 100%): {reason}", flush=True)
        return cursor.lastrowid


def update_battery_counter(charge_a, battery_v):
    """Conteggio COULOMB (Ah) sulla corrente Hall tarata.
    charge_a = corrente di carica dai sensori Hall (A): >0 carica, <0 scarica.
    Integra gli Ah e li converte in Wh-equivalenti con la TENSIONE NOMINALE COSTANTE
    (niente skew di tensione carica/scarica). Applica l'efficienza coulombica
    (battery.soc.charge_efficiency ~0.99) e CLAMPA total_batt_net_Wh a [0, capacita'].
    net_Wh = net_Ah x V_nom -> SOC = net_Wh/cap_Wh = net_Ah/cap_Ah (frazione di Ah)."""
    if charge_a is None or battery_v is None:
        return
    counter = get_current_battery_counter()
    if not counter:
        return
    try:
        v_nom = float(_get("battery.nominal_voltage", 51.2))
        cap = float(_get("battery.nominal_ah", 500)) * v_nom
    except Exception:
        v_nom, cap = 51.2, 0.0
    if cap <= 0:
        cap = 25600.0
    try:
        eff = float(_get("battery.soc.charge_efficiency", 0.99))
    except Exception:
        eff = 0.99
    # Ah nell'intervallo POLL_S -> Wh-equivalenti a tensione nominale costante
    energy_wh = (float(charge_a) * v_nom * POLL_S) / 3600.0
    with db() as con:
        if charge_a > 0:  # Carica: efficienza coulombica + clamp a cap
            e = energy_wh * eff
            con.execute("""
                UPDATE battery_counters
                SET total_batt_in_Wh = total_batt_in_Wh + ?,
                    total_batt_net_Wh = MAX(0.0, MIN(?, total_batt_net_Wh + ?))
                WHERE id = ?
            """, (e, cap, e, counter['id']))
        elif charge_a < 0:  # Scarica: clamp a 0
            e = abs(energy_wh)
            con.execute("""
                UPDATE battery_counters
                SET total_batt_out_Wh = total_batt_out_Wh + ?,
                    total_batt_net_Wh = MAX(0.0, MIN(?, total_batt_net_Wh - ?))
                WHERE id = ?
            """, (e, cap, e, counter['id']))
        con.commit()


def check_battery_reset_condition(battery_v, battery_w):
    """Controlla se e' necessario azzerare il contatore della batteria."""
    if battery_v is None or battery_w is None:
        return False
    battery_v = float(battery_v)
    battery_w = float(battery_w)
    try:
        reset_thr = float(_get("battery.net_reset_voltage", DEFAULT_NET_RESET_V))
    except Exception:
        reset_thr = DEFAULT_NET_RESET_V

    # Azzera solo se in scarica (battery_w < 0) e raggiunge la soglia
    if battery_w < 0 and battery_v <= reset_thr:
        with db() as con:
            last_reset = con.execute("""
                SELECT start_timestamp FROM battery_counters
                WHERE counter_type = 'daily_net'
                ORDER BY id DESC
                LIMIT 1
            """).fetchone()
            if last_reset:
                last_reset_dt = parse_ts(last_reset['start_timestamp'])
                if last_reset_dt:
                    time_diff = datetime.now() - last_reset_dt
                    if time_diff.total_seconds() < 3600:  # evita azzeramenti multipli entro 1h
                        return False
        reset_battery_counter(f"battery_{reset_thr:.1f}v_discharge_{battery_v:.1f}V")
        return True
    return False


# ---------------------------------------------------------------------------
# Auto-calibrazione PIENO (regola: V alta + corrente di coda bassa, sostenuta, di giorno con PV)
# ---------------------------------------------------------------------------
_full_cand_since = None   # quando la condizione "pieno" e' diventata continuativamente vera
_last_autofull = None     # ultima auto-calibrazione (per cooldown)

def check_battery_full_condition(battery_v, battery_a, pv_w):
    """Rileva la carica completa e calibra il SOC al 100% in automatico.
    Pieno = tensione >= soglia AND |corrente| <= corrente di coda AND PV in produzione,
    mantenuto per hold_seconds. Con cooldown e guardia 'gia' pieno'. Ritorna True se calibra."""
    global _full_cand_since, _last_autofull
    try:
        if not _bool(_get("battery.autocal.enabled", True), True):
            _full_cand_since = None
            return False
        if battery_v is None or battery_a is None:
            _full_cand_since = None
            return False
        v = float(battery_v); a = float(battery_a); pv = float(pv_w or 0.0)
        v_full = float(_get("battery.autocal.full_voltage", _get("battery.soc.vmax_v", 54.0)) or 54.0)
        i_tail = float(_get("battery.autocal.tail_current_a", 5.0))
        hold_s = float(_get("battery.autocal.hold_seconds", 600))
        pv_min = float(_get("battery.autocal.min_pv_w", 50.0))
        cooldown_s = float(_get("battery.autocal.cooldown_seconds", 4 * 3600))

        cond = (v >= v_full) and (abs(a) <= i_tail) and (pv >= pv_min)
        now = datetime.now()
        if not cond:
            _full_cand_since = None
            return False
        if _full_cand_since is None:
            _full_cand_since = now
            return False
        if (now - _full_cand_since).total_seconds() < hold_s:
            return False
        if _last_autofull is not None and (now - _last_autofull).total_seconds() < cooldown_s:
            return False
        cap = float(_get("battery.nominal_ah", 500)) * float(_get("battery.nominal_voltage", 51.2))
        cur = get_current_battery_counter()
        if cur and cap > 0 and float(cur.get("total_batt_net_Wh", 0.0)) >= 0.99 * cap:
            _last_autofull = now
            return False
        set_battery_full(f"auto_full v={v:.1f}V i={a:.1f}A pv={pv:.0f}W")
        _last_autofull = now
        _full_cand_since = None
        print(f"[battery] AUTO-CALIBRAZIONE PIENO: V={v:.1f}V I={a:.1f}A PV={pv:.0f}W -> SOC 100%", flush=True)
        return True
    except Exception as e:
        print(f"[battery] check_battery_full_condition error: {e}", flush=True)
        return False

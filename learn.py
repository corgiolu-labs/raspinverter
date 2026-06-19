#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inverter-learn — stima adattiva online (Fase 1, SHADOW MODE).

Legge lo storico (battery_counters, i2c_snapshots, samples) e impara nel tempo
i parametri reali del banco batteria, SENZA toccare il SOC ne' la config:
  - capacita' reale (Ah)            dai cicli pieno->vuoto (invecchiamento)
  - bias sensori Hall vs inverter   (deriva di taratura)
  - tensioni pieno/vuoto osservate  vs soglie configurate

Output: data/learned_params.json (valore + confidenza + n_campioni + note).
Pensato per girare da un timer systemd ogni ~10 min. Read-only sul DB (WAL).
Nessuna azione, nessuna scrittura di config: e' una fase di sola osservazione.
"""
import json
import re
import statistics
from datetime import datetime, timedelta

from config import DATA_DIR, _get, _bool, parse_ts
from database import db

LEARNED_PATH = DATA_DIR / "learned_params.json"
SCHEMA_VERSION = 1


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ema(old, new, alpha=0.3):
    return float(new) if old is None else (1.0 - alpha) * float(old) + alpha * float(new)


def _cfg_floats():
    def f(path, d):
        try:
            return float(_get(path, d))
        except Exception:
            return float(d)
    return {
        "nominal_ah": f("battery.nominal_ah", 500.0),
        "v_nom": f("battery.nominal_voltage", 51.2),
        "vmax_v": f("battery.soc.vmax_v", 57.0),
        "reset_v": f("battery.net_reset_voltage", 46.0),
        "charge_eff": f("battery.soc.charge_efficiency", 0.99),
    }


# --- estimatore 1: capacita' reale dai cicli pieno->vuoto -------------------
def estimate_capacity(con, cfg):
    """Una riga di battery_counters creata da set_battery_full parte con in=cap_nom_wh.
    Se quella riga si chiude per scarica (46V), il netto scaricato ~ capacita' reale."""
    v_nom = cfg["v_nom"]
    nom_ah = cfg["nominal_ah"]
    cap_nom_wh = nom_ah * v_nom
    rows = con.execute("""
        SELECT id, total_batt_in_Wh, total_batt_out_Wh, reset_reason
        FROM battery_counters WHERE counter_type='daily_net' ORDER BY id
    """).fetchall()
    measures = []
    for i in range(1, len(rows)):
        prev_r = rows[i - 1]
        r = rows[i]
        started_full = "full" in (prev_r["reset_reason"] or "").lower()
        rr = (r["reset_reason"] or "").lower()
        ended_empty = ("discharge" in rr) or ("46" in rr)
        if not (started_full and ended_empty):
            continue
        out = float(r["total_batt_out_Wh"] or 0.0)
        inn = float(r["total_batt_in_Wh"] or 0.0)
        # correzione per blip di carica intermedi (la riga partiva con in=cap_nom_wh)
        net_out_wh = out - max(0.0, inn - cap_nom_wh)
        # ciclo "pulito": scarica significativa e poca ricarica intermedia
        if net_out_wh > 0.40 * cap_nom_wh and (inn - cap_nom_wh) < 0.20 * cap_nom_wh:
            measures.append(net_out_wh / v_nom)  # Ah
    if not measures:
        return None
    val = None
    for m in measures:           # EMA: i cicli piu' recenti pesano di piu'
        val = _ema(val, m, 0.4)
    val = max(0.60 * nom_ah, min(1.05 * nom_ah, val))
    n = len(measures)
    return {
        "value_ah": round(val, 1),
        "n_cycles": n,
        "last_measures_ah": [round(x, 1) for x in measures[-5:]],
        "vs_nominal_pct": round(100.0 * val / nom_ah, 1),
        "confidence": "alta" if n >= 3 else ("media" if n == 2 else "bassa"),
    }


# --- estimatore 2: bias dei sensori Hall vs inverter ------------------------
def estimate_hall_bias(con, lookback_h=48, max_rows=4000):
    """Confronta - Î£(corrente Hall) con battery_a dell'inverter sugli stessi istanti.
    bias ~ 0 = sensori allineati; un bias stabile != 0 indica deriva di taratura."""
    since = (datetime.now() - timedelta(hours=lookback_h)).strftime("%Y-%m-%d %H:%M:%S")
    snaps = con.execute("""
        SELECT timestamp, data FROM i2c_snapshots
        WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?
    """, (since, max_rows)).fetchall()
    if not snaps:
        return None
    ba = {}
    for row in con.execute(
        "SELECT timestamp, battery_a FROM samples WHERE timestamp >= ?", (since,)
    ).fetchall():
        if row["battery_a"] is not None:
            ba[row["timestamp"]] = float(row["battery_a"])
    diffs = []
    chan_means = {"BATT1": [], "BATT2": [], "BATT3": [], "BATT4": [], "BATT5": []}
    for s in snaps:
        try:
            d = json.loads(s["data"])
        except Exception:
            continue
        m1 = d.get("adc_mod1", {}) or {}
        m2 = d.get("adc_mod2", {}) or {}
        vals = {n: (m1.get(n) or {}).get("current_a") for n in ("BATT1", "BATT2", "BATT3", "BATT4")}
        vals["BATT5"] = (m2.get("BATT5") or {}).get("current_a")
        cur = [v for v in vals.values() if v is not None]
        if len(cur) < 5:
            continue
        for n, v in vals.items():
            if v is not None:
                chan_means[n].append(v)
        inv = ba.get(s["timestamp"])
        if inv is not None:
            diffs.append((-sum(cur)) - inv)  # carica: Hall negativo, battery_a positivo
    if not diffs:
        return None
    n = len(diffs)
    return {
        "bias_a": round(statistics.fmean(diffs), 2),
        "stdev_a": round(statistics.pstdev(diffs) if n > 1 else 0.0, 2),
        "n_samples": n,
        "per_channel_mean_a": {k: round(statistics.fmean(a), 2) for k, a in chan_means.items() if a},
        "confidence": "alta" if n >= 500 else ("media" if n >= 100 else "bassa"),
    }


# --- estimatore 3: tensioni pieno/vuoto realmente osservate -----------------
def estimate_thresholds(con, cfg):
    rows = con.execute("""
        SELECT start_timestamp, reset_reason FROM battery_counters
        WHERE counter_type='daily_net' ORDER BY id
    """).fetchall()
    full_vs = []
    empty_vs = []
    for i in range(len(rows)):
        rr = rows[i]["reset_reason"] or ""
        m = re.search(r"discharge_([\d.]+)V", rr)   # es. "battery_46.0v_discharge_45.8V"
        if m:
            try:
                empty_vs.append(float(m.group(1)))
            except Exception:
                pass
        # riga creata da set_full = quella la cui PRECEDENTE chiude con 'full'
        if i >= 1 and "full" in (rows[i - 1]["reset_reason"] or "").lower():
            t = rows[i]["start_timestamp"]
            srow = con.execute(
                "SELECT battery_v FROM samples WHERE timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
                (t,)
            ).fetchone()
            if srow and srow["battery_v"] is not None:
                full_vs.append(float(srow["battery_v"]))
    out = {}
    if full_vs:
        out["full_v_observed"] = round(statistics.fmean(full_vs[-5:]), 1)
        out["full_v_config"] = round(cfg["vmax_v"], 1)
        out["n_full"] = len(full_vs)
    if empty_vs:
        out["empty_v_observed"] = round(statistics.fmean(empty_vs[-5:]), 1)
        out["empty_v_config"] = round(cfg["reset_v"], 1)
        out["n_empty"] = len(empty_vs)
    return out or None


# --- estimatore 4: previsione pieno/vuoto dai profili orari ------------------
def estimate_forecast(con, cfg, days=14, horizon_h=48):
    """Da SOC attuale + profili orari medi PV/carico (ultimi N giorni), proietta in
    avanti (passi da 30 min) e stima quando la batteria raggiunge pieno (cap) o vuoto (0).
    Puramente osservativo: nessuna azione."""
    v_nom = cfg["v_nom"]
    cap_wh = cfg["nominal_ah"] * v_nom
    eff = cfg["charge_eff"]
    if cap_wh <= 0:
        return None
    r = con.execute(
        "SELECT total_batt_net_Wh FROM battery_counters ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not r or r["total_batt_net_Wh"] is None:
        return None
    net = float(r["total_batt_net_Wh"])
    soc_pct = round(100.0 * net / cap_wh, 1)
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = con.execute("""
        SELECT CAST(strftime('%H', timestamp) AS INTEGER) AS h,
               AVG(pv_w) AS pv, AVG(load_w) AS ld, COUNT(*) AS n
        FROM samples WHERE timestamp >= ? GROUP BY h
    """, (since,)).fetchall()
    if not rows:
        return None
    pv_h = {int(x["h"]): float(x["pv"] or 0.0) for x in rows}
    ld_h = {int(x["h"]): float(x["ld"] or 0.0) for x in rows}
    n_tot = sum(int(x["n"] or 0) for x in rows)
    if len(pv_h) < 12 or n_tot < 500:
        return {"current_soc_pct": soc_pct, "based_on_days": days, "n_samples": n_tot,
                "confidence": "n/d",
                "note": "storico insufficiente per un profilo orario affidabile"}
    now = datetime.now()
    full_eta = empty_eta = None
    was_full = net >= 0.999 * cap_wh
    was_empty = net <= 0.001 * cap_wh
    sim = net
    for step in range(1, horizon_h * 2 + 1):       # passi da 30 minuti
        t = now + timedelta(minutes=30 * step)
        flow_w = pv_h.get(t.hour, 0.0) - ld_h.get(t.hour, 0.0)
        d_wh = flow_w * 0.5
        if d_wh > 0:
            d_wh *= eff
        sim = max(0.0, min(cap_wh, sim + d_wh))
        if full_eta is None and not was_full and sim >= 0.999 * cap_wh:
            full_eta = t
        if empty_eta is None and not was_empty and sim <= 0.001 * cap_wh:
            empty_eta = t

    def fmt(dt):
        if dt is None:
            return None
        dd = (dt.date() - now.date()).days
        pre = "oggi " if dd == 0 else ("domani " if dd == 1 else dt.strftime("%d/%m "))
        return pre + dt.strftime("%H:%M")

    return {
        "current_soc_pct": soc_pct,
        "full_eta": fmt(full_eta),
        "empty_eta": fmt(empty_eta),
        "based_on_days": days,
        "n_samples": n_tot,
        "confidence": "media" if n_tot >= 5000 else "bassa",
    }


# --- estimatore 5: squilibrio banchi (shadow, solo raccomandazione) ---------
def estimate_balance(con, days=7, max_rows=4000):
    """Analizza lo squilibrio SERIE1-SERIE2 nel tempo (da i2c_snapshots). SHADOW:
    raccomanda soltanto; il controllo reale dei rele' resta in balance_step (gated da
    balance.enabled). diff = banco1 - banco2; chronic_weaker = banco mediamente piu' basso."""
    dev = _get("balance.source_device", "adc_mod2")
    ch1 = _get("balance.bank1_channel", "SERIE1")
    ch2 = _get("balance.bank2_channel", "SERIE2")
    try:
        start_diff = float(_get("balance.start_diff_v", 0.3))
        stop_diff = float(_get("balance.stop_diff_v", 0.1))
    except Exception:
        start_diff, stop_diff = 0.3, 0.1
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = con.execute(
        "SELECT data FROM i2c_snapshots WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
        (since, max_rows)
    ).fetchall()
    if not rows:
        return None
    diffs = []
    current = None
    for r in rows:
        try:
            d = json.loads(r["data"])
        except Exception:
            continue
        mod = d.get(dev, {}) or {}
        c1 = mod.get(ch1); c2 = mod.get(ch2)
        v1 = c1.get("value") if isinstance(c1, dict) else None
        v2 = c2.get("value") if isinstance(c2, dict) else None
        if v1 is None or v2 is None:
            continue
        diff = float(v1) - float(v2)
        if current is None:
            current = {"diff": round(diff, 3), "s1": round(float(v1), 2), "s2": round(float(v2), 2)}
        diffs.append(diff)
    if not diffs or current is None:
        return None
    n = len(diffs)
    mean = statistics.fmean(diffs)
    absd = [abs(x) for x in diffs]
    pct_above = round(100.0 * sum(1 for x in absd if x >= start_diff) / n, 1)
    return {
        "current_diff_v": current["diff"],
        "serie1_v": current["s1"],
        "serie2_v": current["s2"],
        "mean_diff_v": round(mean, 3),
        "max_abs_diff_v": round(max(absd), 3),
        "chronic_weaker": "banco2" if mean > 0 else "banco1",
        "pct_time_above_start": pct_above,
        "start_diff_v": start_diff,
        "stop_diff_v": stop_diff,
        "auto_enabled": _bool(_get("balance.enabled", False), False),
        "n_samples": n,
        "confidence": "alta" if n >= 1000 else ("media" if n >= 200 else "bassa"),
    }


# --- estimatore 6: trigger ENEL balance-aware (F4, shadow) ------------------
def estimate_grid(con, cfg, lookback_min=15):
    """F4 SHADOW: raccomandatore trigger rete ENEL. NON pilota il rele' (controllo reale =
    relay_auto_step). Regola (prove utente): ENEL ON se V_tot<=46V E banchi bilanciati; ma a
    decidere e' il banco PIU' DEBOLE: se scenderebbe sotto il cutoff entro ~60s (handoff ENEL)
    va anticipato, perche' un banco che stacca interrompe la serie e spegne l'inverter."""
    trigger_v = float(_get("grid.trigger_v", 46.0))
    cutoff_v = float(_get("grid.bank_cutoff_v", 22.2))    # DATOU BOSS over-discharge (datasheet)
    margin_v = float(_get("grid.bank_safety_v", 1.3))     # margine di sicurezza sopra il cutoff
    floor_v = cutoff_v + margin_v                          # ~23.5V: il banco debole non deve arrivarci entro 60s
    tol_pct = float(_get("grid.balance_tol_pct", 10.0))
    sync_s = float(_get("grid.enel_sync_s", 60.0))
    dev = _get("balance.source_device", "adc_mod2")
    ch1 = _get("balance.bank1_channel", "SERIE1")
    ch2 = _get("balance.bank2_channel", "SERIE2")
    s1 = s2 = None
    snap = con.execute("SELECT data FROM i2c_snapshots ORDER BY timestamp DESC LIMIT 1").fetchone()
    if snap:
        try:
            mod = (json.loads(snap["data"]).get(dev, {}) or {})
            c1 = mod.get(ch1); c2 = mod.get(ch2)
            s1 = float(c1["value"]) if isinstance(c1, dict) and c1.get("value") is not None else None
            s2 = float(c2["value"]) if isinstance(c2, dict) and c2.get("value") is not None else None
        except Exception:
            pass
    srow = con.execute("SELECT battery_v FROM samples ORDER BY timestamp DESC LIMIT 1").fetchone()
    total_v = float(srow["battery_v"]) if srow and srow["battery_v"] is not None else None
    # rate di scarica dai samples recenti (V/min, negativo = in scarica)
    since = (datetime.now() - timedelta(minutes=lookback_min)).strftime("%Y-%m-%d %H:%M:%S")
    hist = con.execute(
        "SELECT timestamp, battery_v FROM samples WHERE timestamp >= ? ORDER BY timestamp", (since,)
    ).fetchall()
    rate_v_min = None
    if len(hist) >= 2 and hist[0]["battery_v"] is not None and hist[-1]["battery_v"] is not None:
        t0 = parse_ts(hist[0]["timestamp"]); t1 = parse_ts(hist[-1]["timestamp"])
        if t0 and t1:
            dtm = (t1 - t0).total_seconds() / 60.0
            if dtm > 0:
                rate_v_min = (float(hist[-1]["battery_v"]) - float(hist[0]["battery_v"])) / dtm
    diff = (s1 - s2) if (s1 is not None and s2 is not None) else None
    weaker = weaker_v = None
    if s1 is not None and s2 is not None:
        weaker, weaker_v = ("banco1", s1) if s1 <= s2 else ("banco2", s2)
    mean_bank = ((s1 + s2) / 2.0) if (s1 is not None and s2 is not None) else None
    tol_v = (mean_bank * tol_pct / 100.0) if mean_bank else None
    balanced = (abs(diff) <= tol_v) if (diff is not None and tol_v is not None) else None
    # proiezione del banco debole a +sync_s (rate per-banco ~ rate_totale/2, serie simmetrica)
    weaker_proj = None
    if weaker_v is not None and rate_v_min is not None and rate_v_min < 0:
        weaker_proj = weaker_v + (rate_v_min / 2.0) * (sync_s / 60.0)
    recommend = False
    reasons = []
    if total_v is not None and total_v <= trigger_v:
        recommend = True
        if balanced is True:
            reasons.append(f"V {total_v:.1f}<={trigger_v:.0f}V e banchi bilanciati")
        elif balanced is False:
            reasons.append(f"V {total_v:.1f}<={trigger_v:.0f}V ma SBILANCIATI (diff {abs(diff):.2f}V)")
        else:
            reasons.append(f"V {total_v:.1f}<={trigger_v:.0f}V")
    if weaker_proj is not None and weaker_proj <= floor_v:
        recommend = True
        reasons.append(f"{weaker} ~{weaker_v:.1f}V scenderebbe a ~{weaker_proj:.1f}V (<{floor_v:.0f}V) entro {sync_s:.0f}s")
    return {
        "should_enel_now": recommend,
        "reasons": reasons,
        "total_v": round(total_v, 1) if total_v is not None else None,
        "serie1_v": round(s1, 2) if s1 is not None else None,
        "serie2_v": round(s2, 2) if s2 is not None else None,
        "diff_v": round(diff, 3) if diff is not None else None,
        "balanced": balanced,
        "weaker_bank": weaker,
        "weaker_v": round(weaker_v, 2) if weaker_v is not None else None,
        "discharge_v_min": round(rate_v_min, 3) if rate_v_min is not None else None,
        "weaker_proj_60s_v": round(weaker_proj, 2) if weaker_proj is not None else None,
        "trigger_v": trigger_v,
        "bank_cutoff_v": cutoff_v,
        "bank_floor_v": round(floor_v, 1),
        "enel_sync_s": sync_s,
        "relay_real_on_v": float(_get("relay.on_v", 47.5)),
        "note": "SHADOW: raccomanda, non pilota il rele' (reale=relay_auto_step). Cutoff banco 22.2V (datasheet DATOU BOSS over-discharge); floor = cutoff + margine.",
    }


def main():
    cfg = _cfg_floats()
    try:
        prev = json.loads(LEARNED_PATH.read_text(encoding="utf-8"))
    except Exception:
        prev = {}

    out = {
        "schema_version": SCHEMA_VERSION,
        "mode": "shadow",            # Fase 1: osserva e impara, NON applica nulla al SOC
        "updated_at": _now(),
        "config_snapshot": cfg,
    }
    recs = []
    cap = hall = thr = fc = bal = grid = None
    try:
        with db() as con:
            cap = estimate_capacity(con, cfg)
            hall = estimate_hall_bias(con)
            thr = estimate_thresholds(con, cfg)
            fc = estimate_forecast(con, cfg)
            bal = estimate_balance(con)
            grid = estimate_grid(con, cfg)
    except Exception as e:
        out["error"] = f"db: {e}"

    # capacita': EMA anche tra run successivi (continuita')
    if cap:
        prev_cap = (prev.get("capacity") or {}).get("value_ah")
        if prev_cap is not None:
            cap["value_ah"] = round(_ema(prev_cap, cap["value_ah"], 0.3), 1)
            cap["vs_nominal_pct"] = round(100.0 * cap["value_ah"] / cfg["nominal_ah"], 1)
        out["capacity"] = cap
        if cap["vs_nominal_pct"] < 90:
            recs.append(f"Capacita' reale ~{cap['value_ah']:.0f} Ah ({cap['vs_nominal_pct']:.0f}% del nominale): "
                        f"segni di invecchiamento, valuta nominal_ah={cap['value_ah']:.0f}.")
        else:
            recs.append(f"Capacita' reale ~{cap['value_ah']:.0f} Ah ({cap['vs_nominal_pct']:.0f}% del nominale): in salute.")
    else:
        out["capacity"] = {"value_ah": None, "n_cycles": 0,
                           "note": "servono cicli pieno->vuoto completi per misurarla",
                           "confidence": "n/d"}

    if hall:
        out["hall_bias"] = hall
        if abs(hall["bias_a"]) > 3.0:
            recs.append(f"Bias Hall vs inverter {hall['bias_a']:+.1f} A: possibile deriva dei sensori, "
                        f"valuta una ri-taratura degli offset.")
        else:
            recs.append(f"Sensori Hall allineati all'inverter (bias {hall['bias_a']:+.1f} A).")
    else:
        out["hall_bias"] = {"bias_a": None, "note": "dati i2c insufficienti", "confidence": "n/d"}

    if thr:
        out["thresholds"] = thr
        if "full_v_observed" in thr and abs(thr["full_v_observed"] - thr["full_v_config"]) > 1.5:
            recs.append(f"Pieno reale osservato ~{thr['full_v_observed']:.1f} V vs soglia {thr['full_v_config']:.0f} V.")
        if "empty_v_observed" in thr and abs(thr["empty_v_observed"] - thr["empty_v_config"]) > 1.5:
            recs.append(f"Vuoto reale osservato ~{thr['empty_v_observed']:.1f} V vs soglia {thr['empty_v_config']:.0f} V.")

    if fc:
        out["forecast"] = fc
        if fc.get("full_eta"):
            recs.append(f"Previsione: batteria piena ~{fc['full_eta']}.")
        if fc.get("empty_eta"):
            recs.append(f"Previsione: batteria scarica ~{fc['empty_eta']}.")
    else:
        out["forecast"] = {"note": "previsione non disponibile (storico insufficiente)"}

    if bal:
        out["balance"] = bal
        cd = bal.get("current_diff_v")
        if cd is not None and abs(cd) >= bal["start_diff_v"]:
            recs.append(f"Squilibrio banchi {cd:+.2f} V: {bal['chronic_weaker']} piu' basso, andrebbe bilanciato.")
        if (not bal["auto_enabled"]) and bal.get("max_abs_diff_v", 0) >= bal["start_diff_v"]:
            recs.append(f"Auto-bilanciamento OFF ma lo squilibrio ha toccato {bal['max_abs_diff_v']:.2f} V: valuta balance.enabled=true.")
    else:
        out["balance"] = {"note": "dati banchi insufficienti"}

    if grid:
        out["grid"] = grid
        if grid.get("should_enel_now") and grid.get("reasons"):
            recs.append("Rete ENEL consigliata ORA: " + grid["reasons"][0] + " (shadow).")
    else:
        out["grid"] = {"note": "dati rete/banchi insufficienti"}

    out["coulombic_efficiency"] = {
        "value": round(cfg["charge_eff"], 3),
        "source": "config",
        "note": "verra' appresa dai cicli completi (fase successiva)",
    }
    out["recommendations"] = recs

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LEARNED_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[learn] {LEARNED_PATH.name}: cap={out['capacity'].get('value_ah')} Ah "
          f"hall_bias={out['hall_bias'].get('bias_a')} A recs={len(recs)}", flush=True)


if __name__ == "__main__":
    main()

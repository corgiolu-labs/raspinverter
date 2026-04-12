# -*- coding: utf-8 -*-
"""Finestre temporali e aggregazioni per `/api/history`, `/api/energy`, `/api/totals/today`."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Tuple


def normalize_energy_unit(unit_raw: str) -> Tuple[str, float, str]:
    """Restituisce (unit normalizzata, fattore scala Wh→output, suffisso chiave JSON)."""
    u = unit_raw.lower()
    if u not in {"wh", "kwh"}:
        u = "kwh"
    scale = 1.0 / 1000.0 if u == "kwh" else 1.0
    suffix = "_kWh" if u == "kwh" else "_Wh"
    return u, scale, suffix


def parse_energy_window(
    gran: str,
    now_dt: datetime,
    *,
    date_str: Optional[str],
    from_str: Optional[str],
    min_year_from_samples: Optional[int],
) -> Tuple[datetime, datetime, str]:
    """
    Calcola (start, end, step) per granularità hour|day|month|year.
    `min_year_from_samples` è usato solo per gran != hour/day/month (default anno da DB).
    """
    if gran == "hour":
        base = datetime.strptime(date_str or now_dt.strftime("%Y-%m-%d"), "%Y-%m-%d")
        start = base.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now_dt if base.date() == now_dt.date() else start + timedelta(days=1) - timedelta(seconds=1)
        return start, end, "hour"

    if gran == "day":
        base = datetime.strptime(from_str or now_dt.strftime("%Y-%m-%d"), "%Y-%m-%d")
        start = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if base.month == now_dt.month and base.year == now_dt.year:
            end = now_dt
        else:
            month_next = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = month_next - timedelta(seconds=1)
        return start, end, "day"

    if gran == "month":
        base = datetime.strptime(from_str or now_dt.strftime("%Y-%m-%d"), "%Y-%m-%d")
        start = base.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now_dt if base.year == now_dt.year else start.replace(year=start.year + 1) - timedelta(seconds=1)
        return start, end, "month"

    y0 = int(min_year_from_samples if min_year_from_samples is not None else now_dt.year)
    start = datetime(y0, 1, 1)
    end = now_dt
    return start, end, "year"


def build_minute_history_series(now_dt: datetime, rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Riempie ogni minuto dalla mezzanotte a now con medie da `rows` (stesso contratto di /api/history)."""
    day0_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    agg = {
        r["ts_min"]: {
            "pv_w": r["pv_w"],
            "battery_w": r["battery_w"],
            "load_w": r["load_w"],
            "grid_w": r["grid_w"],
        }
        for r in rows
    }
    out: List[Dict[str, Any]] = []
    t = day0_dt
    while t <= now_dt:
        key = t.strftime("%Y-%m-%d %H:%M:00")
        if key in agg:
            v = agg[key]
            out.append({
                "timestamp": key,
                "pv_w": v["pv_w"],
                "battery_w": v["battery_w"],
                "load_w": v["load_w"],
                "grid_w": v["grid_w"],
            })
        else:
            out.append({
                "timestamp": key,
                "pv_w": None,
                "battery_w": None,
                "load_w": None,
                "grid_w": None,
            })
        t += timedelta(minutes=1)
    return out


def _energy_rec(bucket: str, pv: float, ld: float, gr: float, bi: float, bo: float, scale: float, suffix: str) -> Dict[str, Any]:
    return {
        "bucket": bucket,
        f"pv{suffix}": pv * scale,
        f"load{suffix}": ld * scale,
        f"grid{suffix}": gr * scale,
        f"batt_in{suffix}": bi * scale,
        f"batt_out{suffix}": bo * scale,
        f"batt_net{suffix}": (bi - bo) * scale,
    }


def build_energy_hourly_data(
    start: datetime,
    end: datetime,
    rows: List[Mapping[str, Any]],
    scale: float,
    suffix: str,
) -> List[Dict[str, Any]]:
    have = {r["bucket"]: r for r in rows}
    out: List[Dict[str, Any]] = []
    cur = start.replace(minute=0, second=0, microsecond=0)
    while cur <= end:
        b = cur.strftime("%Y-%m-%d %H:00")
        r = have.get(b)
        if r:
            bi = float(r["batt_in_Wh"] or 0.0)
            bo = float(r["batt_out_Wh"] or 0.0)
            out.append({
                "bucket": b,
                f"pv{suffix}": float(r["pv_Wh"] or 0.0) * scale,
                f"load{suffix}": float(r["load_Wh"] or 0.0) * scale,
                f"grid{suffix}": float(r["grid_Wh"] or 0.0) * scale,
                f"batt_in{suffix}": bi * scale,
                f"batt_out{suffix}": bo * scale,
                f"batt_net{suffix}": (bi - bo) * scale,
            })
        else:
            out.append({
                "bucket": b,
                f"pv{suffix}": 0.0,
                f"load{suffix}": 0.0,
                f"grid{suffix}": 0.0,
                f"batt_in{suffix}": 0.0,
                f"batt_out{suffix}": 0.0,
                f"batt_net{suffix}": 0.0,
            })
        cur += timedelta(hours=1)
    return out


def merge_energy_day_totals(
    s_rows: List[Mapping[str, Any]],
    a_rows: List[Mapping[str, Any]],
) -> Dict[str, Dict[str, float]]:
    acc: Dict[str, Dict[str, float]] = defaultdict(lambda: {"pv": 0.0, "load": 0.0, "grid": 0.0, "bi": 0.0, "bo": 0.0})

    for r in s_rows:
        d = r["day"]
        acc[d]["pv"] += float(r["pv_Wh"] or 0.0)
        acc[d]["load"] += float(r["load_Wh"] or 0.0)
        acc[d]["grid"] += float(r["grid_Wh"] or 0.0)
        acc[d]["bi"] += float(r["batt_in_Wh"] or 0.0)
        acc[d]["bo"] += float(r["batt_out_Wh"] or 0.0)

    for r in a_rows:
        d = r["day"]
        acc[d]["pv"] += float(r["pv_Wh"] or 0.0)
        acc[d]["load"] += float(r["load_Wh"] or 0.0)
        acc[d]["grid"] += float(r["grid_Wh"] or 0.0)
        acc[d]["bi"] += float(r["batt_in_Wh"] or 0.0)
        acc[d]["bo"] += float(r["batt_out_Wh"] or 0.0)

    return acc


def build_energy_non_hour_series(
    step: str,
    start: datetime,
    end: datetime,
    acc: Mapping[str, Dict[str, float]],
    scale: float,
    suffix: str,
) -> List[Dict[str, Any]]:
    """Costruisce serie day/month/year da accumulo giornaliero."""
    out: List[Dict[str, Any]] = []

    if step == "day":
        cur = start
        while cur <= end:
            d = cur.strftime("%Y-%m-%d")
            v = acc.get(d)
            if v:
                out.append(_energy_rec(d, v["pv"], v["load"], v["grid"], v["bi"], v["bo"], scale, suffix))
            else:
                out.append(_energy_rec(d, 0.0, 0.0, 0.0, 0.0, 0.0, scale, suffix))
            cur += timedelta(days=1)

    elif step == "month":
        bym: Dict[str, Dict[str, float]] = defaultdict(lambda: {"pv": 0.0, "load": 0.0, "grid": 0.0, "bi": 0.0, "bo": 0.0})
        for d, v in acc.items():
            m = datetime.strptime(d, "%Y-%m-%d").strftime("%Y-%m")
            for k in bym[m]:
                bym[m][k] += v[k]
        cur = start.replace(day=1)
        while cur <= end:
            m = cur.strftime("%Y-%m")
            v = bym.get(m)
            if v:
                out.append(_energy_rec(m, v["pv"], v["load"], v["grid"], v["bi"], v["bo"], scale, suffix))
            else:
                out.append(_energy_rec(m, 0.0, 0.0, 0.0, 0.0, 0.0, scale, suffix))
            cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)

    else:
        byy: Dict[str, Dict[str, float]] = defaultdict(lambda: {"pv": 0.0, "load": 0.0, "grid": 0.0, "bi": 0.0, "bo": 0.0})
        for d, v in acc.items():
            y = datetime.strptime(d, "%Y-%m-%d").strftime("%Y")
            for k in byy[y]:
                byy[y][k] += v[k]
        cur = start.replace(month=1, day=1)
        while cur <= end:
            y = cur.strftime("%Y")
            v = byy.get(y)
            if v:
                out.append(_energy_rec(y, v["pv"], v["load"], v["grid"], v["bi"], v["bo"], scale, suffix))
            else:
                out.append(_energy_rec(y, 0.0, 0.0, 0.0, 0.0, 0.0, scale, suffix))
            cur = cur.replace(year=cur.year + 1)

    return out


def build_totals_today_payload(
    row: Mapping[str, Any],
    battery_counter: Optional[Dict[str, Any]],
    unit: str,
    scale: float,
    suffix: str,
) -> Dict[str, Any]:
    """Payload JSON per `/api/totals/today` da riga aggregata SQL + contatore batteria."""
    pv = float(row["pv_Wh"] or 0.0) * scale
    ld = float(row["load_Wh"] or 0.0) * scale
    gr = float(row["grid_Wh"] or 0.0) * scale
    bi = float(row["batt_in_Wh"] or 0.0) * scale
    bo = float(row["batt_out_Wh"] or 0.0) * scale
    battery_net = float(battery_counter.get("total_batt_net_Wh", 0.0)) if battery_counter else 0.0
    batt_net = battery_net * scale

    return {
        "unit": unit,
        f"pv{suffix}": pv,
        f"load{suffix}": ld,
        f"grid{suffix}": gr,
        f"batt_in{suffix}": bi,
        f"batt_out{suffix}": bo,
        f"batt_net{suffix}": batt_net,
        "battery_counter_info": {
            "start_timestamp": battery_counter.get("start_timestamp") if battery_counter else None,
            "reset_reason": battery_counter.get("reset_reason") if battery_counter else None,
            "total_batt_net_Wh": battery_counter.get("total_batt_net_Wh", 0.0) if battery_counter else 0.0,
        },
    }

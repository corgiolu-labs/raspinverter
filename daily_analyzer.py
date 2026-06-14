#!/usr/bin/env python3
"""
Daily Analyzer — analisi giornaliera tarata su impianto OFF-GRID
(rete usata SOLO come backup, nessuna immissione/vendita in rete).

Quattro temi:
  1) Autonomia & prelievo rete  — quanto sei off-grid, quanta energia prelevi e quando
  2) Salute batteria LiFePO4    — SOC, profondità di scarica, cicli, piena carica (bilanciamento)
  3) Surplus PV                 — quando la batteria è piena e sprechi sole → spostare i carichi
  4) Diagnostica & anomalie     — temperature, produzione notturna anomala, soglia relè

Espone:
  - analyze_daily_data(date) -> dict   (consumato da /api/analysis/daily, salvato in tabella daily_analysis)
  - cleanup_old_samples(date, keep_analysis=True)

Convenzioni di segno (verificate sull'inverter):
  battery_w > 0 = carica (energia IN batteria);   battery_w < 0 = scarica
  grid_w   > 0 = prelievo dalla rete (import);    grid_w   < 0 = export (~0 in off-grid)
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DailyAnalyzer:
    """Analizzatore giornaliero off-grid per dati inverter."""

    def __init__(self, db_path: str = "data/inverter_history.db", config_path: Optional[str] = None):
        self.db_path = db_path
        self.cfg = self._load_config(config_path)
        batt = self.cfg.get("battery", {})
        soc = batt.get("soc", {})
        # Soglie SOC voltage-based (default = config tipico RASPYNVERTER)
        self.v_min = float(soc.get("vmin_v", 46.0))   # 0% SOC
        self.v_max = float(soc.get("vmax_v", 54.0))   # 100% SOC
        self.batt_capacity_kwh = (float(batt.get("nominal_ah", 500))
                                  * float(batt.get("nominal_voltage", 51.2)) / 1000.0)
        self.relay_on_v = float(self.cfg.get("relay", {}).get("on_v", self.v_min))

    def _load_config(self, config_path: Optional[str]) -> Dict:
        """Carica inverter_config.json (se possibile) per soglie SOC/capacità/relè."""
        try:
            if config_path is None:
                # db_path = <base>/data/inverter_history.db  ->  <base>/config/inverter_config.json
                config_path = Path(self.db_path).resolve().parent.parent / "config" / "inverter_config.json"
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    # =====================================================================
    # Helper di base (riusati dalla versione precedente, invariati)
    # =====================================================================
    def _get_daily_samples(self, conn: sqlite3.Connection, date: str) -> List[Dict]:
        """Recupera tutti i campioni di una giornata (decima se sovracampionato)."""
        query = "SELECT * FROM samples WHERE DATE(timestamp) = ? ORDER BY timestamp ASC"
        cursor = conn.execute(query, (date,))
        columns = [d[0] for d in cursor.description]
        samples = [dict(zip(columns, row)) for row in cursor.fetchall()]
        if len(samples) > 17280:          # più di 1 campione/minuto su 24h
            samples = samples[::12]       # riduci a ~1 campione/minuto
        return samples

    def _aggregate_samples_by_interval(self, samples: List[Dict], interval_minutes: int) -> List[Dict]:
        """Raggruppa i campioni in intervalli di N minuti (media) per calcoli stabili."""
        if not samples:
            return []
        aggregated: List[Dict] = []
        current_interval = None
        current_samples: List[Dict] = []
        for sample in samples:
            ts = datetime.fromisoformat(sample['timestamp'])
            interval_start = ts.replace(minute=(ts.minute // interval_minutes) * interval_minutes,
                                        second=0, microsecond=0)
            if current_interval != interval_start:
                if current_samples:
                    aggregated.append(self._average_samples(current_samples))
                current_interval = interval_start
                current_samples = [sample]
            else:
                current_samples.append(sample)
        if current_samples:
            aggregated.append(self._average_samples(current_samples))
        return aggregated

    def _average_samples(self, samples: List[Dict]) -> Dict:
        """Media di un gruppo di campioni (solo campi potenza/tensione utili)."""
        if not samples:
            return {}
        result: Dict[str, Any] = {}
        numeric_fields = ['pv_w', 'battery_w', 'grid_w', 'load_w', 'pv_v', 'battery_v', 'grid_v', 'load_v']
        for field in numeric_fields:
            values = [s.get(field, 0) for s in samples if s.get(field) is not None]
            if values:
                result[field] = sum(values) / len(values)
        result['timestamp'] = samples[0]['timestamp']
        return result

    def _calculate_energy_from_power(self, samples: List[Dict], field: str, interval_minutes: int) -> float:
        """Energia kWh da potenza W: somma |P|·Δt sui campioni (aggregati) dati."""
        if not samples:
            return 0.0
        total_wh = 0.0
        for sample in samples:
            p = sample.get(field, 0)
            if p is not None:
                total_wh += abs(p) * (interval_minutes / 60.0)
        return total_wh / 1000.0

    def _get_season(self, date: datetime) -> str:
        m = date.month
        if m in (12, 1, 2):
            return "winter"
        if m in (3, 4, 5):
            return "spring"
        if m in (6, 7, 8):
            return "summer"
        return "autumn"

    # =====================================================================
    # Nuovi helper off-grid
    # =====================================================================
    def _soc(self, v: Optional[float]) -> Optional[float]:
        """SOC % voltage-based, clampato 0..100."""
        if v is None:
            return None
        if self.v_max <= self.v_min:
            return None
        return max(0.0, min(100.0, (float(v) - self.v_min) / (self.v_max - self.v_min) * 100.0))

    def _energy_split(self, samples: List[Dict], field: str, interval_minutes: int = 5):
        """(kWh con P>0, kWh con P<0) integrando separatamente i due segni."""
        agg = self._aggregate_samples_by_interval(samples, interval_minutes)
        pos_wh = neg_wh = 0.0
        for s in agg:
            p = s.get(field)
            if p is None:
                continue
            wh = abs(p) * (interval_minutes / 60.0)
            if p > 0:
                pos_wh += wh
            elif p < 0:
                neg_wh += wh
        return pos_wh / 1000.0, neg_wh / 1000.0

    def _energy_by_hour(self, samples: List[Dict], field: str, positive_only: bool = False) -> Dict[str, float]:
        """kWh per ora-del-giorno (0..23) per i grafici."""
        by_hour = {h: 0.0 for h in range(24)}
        for s in self._aggregate_samples_by_interval(samples, 5):
            p = s.get(field)
            if p is None:
                continue
            if positive_only and p <= 0:
                continue
            h = datetime.fromisoformat(s['timestamp']).hour
            by_hour[h] += abs(p) * (5 / 60.0) / 1000.0
        return {str(h): round(by_hour[h], 3) for h in range(24)}

    def _coverage(self, samples: List[Dict]) -> Dict:
        if not samples:
            return {"hours_covered": 0.0}
        t0 = datetime.fromisoformat(samples[0]['timestamp'])
        t1 = datetime.fromisoformat(samples[-1]['timestamp'])
        hours = max(0.0, (t1 - t0).total_seconds() / 3600.0)
        return {"first": samples[0]['timestamp'], "last": samples[-1]['timestamp'],
                "hours_covered": round(hours, 2)}

    @staticmethod
    def _hhmm(ts: str) -> str:
        try:
            return datetime.fromisoformat(ts).strftime("%H:%M")
        except Exception:
            return "--:--"

    # =====================================================================
    # TEMA 1 — Autonomia & prelievo rete
    # =====================================================================
    def _analyze_autonomy(self, samples: List[Dict]) -> Dict:
        load_kwh = self._calculate_energy_from_power(
            self._aggregate_samples_by_interval([s for s in samples if s.get('load_w') is not None], 5), 'load_w', 5)
        grid_samples = [s for s in samples if s.get('grid_w') is not None]
        grid_import_kwh, _grid_export_kwh = self._energy_split(grid_samples, 'grid_w')

        autonomy_pct = round((load_kwh - grid_import_kwh) / load_kwh * 100, 1) if load_kwh > 0 else None

        # Stima fonte del carico (kWh): rete (misurata) + scarica batteria (stima) + PV diretto (resto)
        _charge_kwh, batt_discharge_kwh = self._energy_split(
            [s for s in samples if s.get('battery_w') is not None], 'battery_w')
        load_from_grid = min(grid_import_kwh, load_kwh)
        load_from_battery = min(batt_discharge_kwh, max(0.0, load_kwh - load_from_grid))
        load_from_pv_direct = max(0.0, load_kwh - load_from_grid - load_from_battery)

        # Finestre contigue di prelievo (grid_w > soglia)
        windows = self._import_windows(grid_samples, threshold_w=50.0)
        main_window = max(windows, key=lambda w: w['kwh']) if windows else None

        peak_import_w = max((s['grid_w'] for s in grid_samples if s['grid_w'] is not None and s['grid_w'] > 0), default=0)

        return {
            "autonomy_pct": autonomy_pct,
            "grid_import_kwh": round(grid_import_kwh, 3),
            "grid_import_peak_kw": round(peak_import_w / 1000, 3),
            "full_autonomy_day": grid_import_kwh < 0.05,
            "grid_import_by_hour": self._energy_by_hour(grid_samples, 'grid_w', positive_only=True),
            "import_windows": windows,
            "main_import_window": (f"{main_window['start']}–{main_window['end']}" if main_window else None),
            "load_consumption_kwh": round(load_kwh, 3),
            "load_from_grid_kwh": round(load_from_grid, 3),
            "load_from_battery_kwh": round(load_from_battery, 3),
            "load_from_pv_direct_kwh": round(load_from_pv_direct, 3),
        }

    def _import_windows(self, grid_samples: List[Dict], threshold_w: float = 50.0) -> List[Dict]:
        """Trova le finestre orarie contigue in cui si preleva dalla rete."""
        agg = self._aggregate_samples_by_interval(grid_samples, 5)
        windows: List[Dict] = []
        cur: Optional[Dict] = None
        for s in agg:
            p = s.get('grid_w') or 0
            drawing = p > threshold_w
            if drawing:
                wh = p * (5 / 60.0)
                if cur is None:
                    cur = {"start_ts": s['timestamp'], "end_ts": s['timestamp'], "wh": wh}
                else:
                    cur["end_ts"] = s['timestamp']
                    cur["wh"] += wh
            else:
                if cur is not None:
                    windows.append(cur)
                    cur = None
        if cur is not None:
            windows.append(cur)
        out = []
        for w in windows:
            if w["wh"] / 1000.0 >= 0.02:  # ignora finestre trascurabili (<20 Wh)
                out.append({"start": self._hhmm(w["start_ts"]), "end": self._hhmm(w["end_ts"]),
                            "kwh": round(w["wh"] / 1000.0, 3)})
        return out

    # =====================================================================
    # TEMA 2 — Salute batteria LiFePO4
    # =====================================================================
    def _analyze_battery_offgrid(self, samples: List[Dict]) -> Dict:
        vsamples = [s for s in samples if s.get('battery_v') is not None]
        if not vsamples:
            return {"status": "no_data"}

        voltages = [s['battery_v'] for s in vsamples]
        min_v, max_v = min(voltages), max(voltages)
        avg_v = sum(voltages) / len(voltages)
        min_sample = min(vsamples, key=lambda s: s['battery_v'])
        max_sample = max(vsamples, key=lambda s: s['battery_v'])
        min_soc = self._soc(min_v)
        max_soc = self._soc(max_v)

        bsamples = [s for s in samples if s.get('battery_w') is not None]
        charge_kwh, discharge_kwh = self._energy_split(bsamples, 'battery_w')
        throughput = charge_kwh + discharge_kwh
        equiv_cycles = (discharge_kwh / self.batt_capacity_kwh) if self.batt_capacity_kwh > 0 else 0

        reached_full = (max_v >= self.v_max - 0.3) or ((max_soc or 0) >= 98)
        full_charge_time = None
        for s in vsamples:
            if self._soc(s['battery_v']) >= 98:
                full_charge_time = s['timestamp']
                break

        # Tempo sotto soglia relè (proxy attività relè, il relè non è nei sample del DB)
        cov = self._coverage(samples)
        below = [s for s in vsamples if s['battery_v'] <= self.relay_on_v]
        below_min = round(cov.get("hours_covered", 0) * 60 * (len(below) / len(vsamples)), 1) if vsamples else 0

        # SOC medio per ora (per il grafico)
        soc_by_hour: Dict[str, list] = {str(h): [] for h in range(24)}
        for s in vsamples:
            h = datetime.fromisoformat(s['timestamp']).hour
            soc_by_hour[str(h)].append(self._soc(s['battery_v']))
        soc_hourly = {h: (round(sum(v) / len(v), 1) if v else None) for h, v in soc_by_hour.items()}

        return {
            "status": "data_available",
            "min_voltage": round(min_v, 2), "max_voltage": round(max_v, 2), "avg_voltage": round(avg_v, 2),
            "min_soc_pct": round(min_soc, 1) if min_soc is not None else None,
            "max_soc_pct": round(max_soc, 1) if max_soc is not None else None,
            "min_soc_time": self._hhmm(min_sample['timestamp']),
            "max_soc_time": self._hhmm(max_sample['timestamp']),
            "max_dod_pct": round(100 - min_soc, 1) if min_soc is not None else None,
            "charge_kwh": round(charge_kwh, 3),
            "discharge_kwh": round(discharge_kwh, 3),
            "throughput_kwh": round(throughput, 3),
            "equiv_cycles": round(equiv_cycles, 2),
            "reached_full": reached_full,
            "full_charge_time": self._hhmm(full_charge_time) if full_charge_time else None,
            "time_below_relay_v_min": below_min,
            "relay_on_v": self.relay_on_v,
            "capacity_kwh": round(self.batt_capacity_kwh, 2),
            "soc_by_hour": soc_hourly,
        }

    # =====================================================================
    # TEMA 3 — PV: produzione + surplus sprecato
    # =====================================================================
    def _analyze_pv_offgrid(self, samples: List[Dict], battery: Dict) -> Dict:
        pv_samples = [s for s in samples if s.get('pv_w') is not None]
        if not pv_samples:
            return {"status": "no_data"}

        production_kwh = self._calculate_energy_from_power(
            self._aggregate_samples_by_interval(pv_samples, 5), 'pv_w', 5)
        peak = max(pv_samples, key=lambda s: s['pv_w'] or 0)
        peak_kw = round((peak['pv_w'] or 0) / 1000, 3)

        significant = [s for s in pv_samples if (s.get('pv_w') or 0) > 100]
        sun_hours = 0.0
        if significant:
            t0 = datetime.fromisoformat(min(significant, key=lambda s: s['timestamp'])['timestamp'])
            t1 = datetime.fromisoformat(max(significant, key=lambda s: s['timestamp'])['timestamp'])
            sun_hours = round(min((t1 - t0).total_seconds() / 3600.0, 14.0), 2)

        # SURPLUS (stima): intervalli con batteria ~piena (SOC>=95%) e PV>carico durante il giorno.
        # NB stima conservativa: quando la batteria è piena l'MPPT taglia, quindi pv_w è già ridotto.
        surplus_wh = 0.0
        surplus_pts = []
        agg = self._aggregate_samples_by_interval(samples, 5)
        for s in agg:
            v = s.get('battery_v')
            pv = s.get('pv_w') or 0
            load = s.get('load_w') or 0
            soc = self._soc(v) if v is not None else None
            if soc is not None and soc >= 95 and pv > load:
                surplus_wh += (pv - load) * (5 / 60.0)
                surplus_pts.append(s['timestamp'])
        surplus_window = None
        if surplus_pts:
            surplus_window = f"{self._hhmm(surplus_pts[0])}–{self._hhmm(surplus_pts[-1])}"

        return {
            "status": "data_available",
            "production_kwh": round(production_kwh, 3),
            "peak_kw": peak_kw,
            "peak_time": self._hhmm(peak['timestamp']),
            "sun_hours": sun_hours,
            "surplus_estimate_kwh": round(surplus_wh / 1000.0, 3),
            "surplus_window": surplus_window,
            "production_by_hour": self._energy_by_hour(pv_samples, 'pv_w'),
        }

    # =====================================================================
    # Carico
    # =====================================================================
    def _analyze_load(self, samples: List[Dict]) -> Dict:
        load_samples = [s for s in samples if s.get('load_w') is not None]
        if not load_samples:
            return {"status": "no_data"}
        total = self._calculate_energy_from_power(
            self._aggregate_samples_by_interval(load_samples, 5), 'load_w', 5)
        peak = max(load_samples, key=lambda s: s['load_w'] or 0)
        avg_w = sum((s['load_w'] or 0) for s in load_samples) / len(load_samples)
        pf = [s['load_pf'] for s in load_samples if s.get('load_pf') is not None]
        night = [s for s in load_samples if (datetime.fromisoformat(s['timestamp']).hour >= 22
                                             or datetime.fromisoformat(s['timestamp']).hour < 6)]
        night_kwh = self._calculate_energy_from_power(
            self._aggregate_samples_by_interval(night, 5), 'load_w', 5) if night else 0.0
        return {
            "status": "data_available",
            "consumption_kwh": round(total, 3),
            "peak_kw": round((peak['load_w'] or 0) / 1000, 3),
            "peak_time": self._hhmm(peak['timestamp']),
            "avg_kw": round(avg_w / 1000, 3),
            "night_consumption_kwh": round(night_kwh, 3),
            "avg_power_factor": round(sum(pf) / len(pf), 3) if pf else None,
            "by_hour": self._energy_by_hour(load_samples, 'load_w'),
        }

    # =====================================================================
    # TEMA 4 — Diagnostica & anomalie
    # =====================================================================
    def _analyze_diagnostics(self, samples: List[Dict]) -> Dict:
        diag = self._detect_anomalies(samples)

        def _max(field):
            vals = [s.get(field) for s in samples if s.get(field) is not None]
            return round(max(vals), 1) if vals else None

        max_inv = _max('inverter_temp')
        max_heat = _max('heatsink_temp')
        max_dc = _max('dc_temp')
        temp_warn = any(t is not None and t >= 70 for t in (max_inv, max_heat, max_dc))
        diag.update({
            "max_inverter_temp": max_inv,
            "max_heatsink_temp": max_heat,
            "max_dc_temp": max_dc,
            "temp_warning": temp_warn,
        })
        return diag

    def _detect_pv_night_production(self, samples: List[Dict]) -> List[Dict]:
        """Produzione PV notturna anomala (es. lampione/illuminazione che colpisce il pannello)."""
        night = [s for s in samples
                 if s.get('pv_w') is not None
                 and (datetime.fromisoformat(s['timestamp']).hour >= 22
                      or datetime.fromisoformat(s['timestamp']).hour <= 5)
                 and (s.get('pv_w') or 0) > 20]
        if not night:
            return []
        energy = self._calculate_energy_from_power(self._aggregate_samples_by_interval(night, 5), 'pv_w', 5)
        if energy > 0.1:
            return [{
                "type": "pv_night_production_anomaly",
                "timestamp": night[0]['timestamp'],
                "energy_kwh": round(energy, 3),
                "severity": "medium",
                "note": "Produzione PV notturna anomala (possibile illuminazione artificiale sul pannello)",
            }]
        return []

    def _detect_anomalies(self, samples: List[Dict]) -> Dict:
        """Rileva picchi anomali, variazioni improvvise e produzione notturna."""
        anomalies: List[Dict] = []
        aggregated = self._aggregate_samples_by_interval(samples, 5)

        for field in ['pv_w', 'battery_w', 'grid_w', 'load_w']:
            if not aggregated:
                continue
            values = [s.get(field, 0) for s in aggregated if s.get(field) is not None]
            filtered = [v for v in values if abs(v) < 10000]
            if not filtered:
                continue
            mean_val = sum(filtered) / len(filtered)
            std_dev = (sum((x - mean_val) ** 2 for x in filtered) / len(filtered)) ** 0.5
            if field == 'pv_w':
                threshold, min_threshold = mean_val + 4 * std_dev, 500
                filtered = [v for v in filtered if v > 100]
                if not filtered:
                    continue
            elif field == 'grid_w':
                threshold, min_threshold = mean_val + 3.5 * std_dev, 500
            elif field == 'load_w':
                threshold, min_threshold = mean_val + 3.5 * std_dev, 800
            else:
                threshold, min_threshold = mean_val + 3 * std_dev, 300
            threshold = max(threshold, min_threshold)
            for s in [a for a in aggregated if a.get(field, 0) > threshold]:
                severity = "high" if s[field] > mean_val + 5 * std_dev else "medium"
                anomalies.append({"type": f"power_peak_{field}", "timestamp": s['timestamp'],
                                  "value": round(s[field] / 1000, 3), "threshold": round(threshold / 1000, 3),
                                  "severity": severity})

        for i in range(1, len(aggregated)):
            prev, curr = aggregated[i - 1], aggregated[i]
            for field in ['pv_w', 'battery_w', 'grid_w', 'load_w']:
                if field in prev and field in curr:
                    change = abs(curr[field] - prev[field])
                    if change > 8000:
                        anomalies.append({"type": f"sudden_change_{field}", "timestamp": curr['timestamp'],
                                          "change_kw": round(change / 1000, 3), "severity": "medium"})

        pv_night = self._detect_pv_night_production(samples)
        anomalies.extend(pv_night)
        return {
            "total_anomalies": len(anomalies),
            "anomalies": anomalies,
            "high_severity": len([a for a in anomalies if a.get('severity') == 'high']),
            "medium_severity": len([a for a in anomalies if a.get('severity') == 'medium']),
            "pv_night_anomalies": len(pv_night),
        }

    # =====================================================================
    # Stagionale (riusato, invariato)
    # =====================================================================
    def _extract_seasonal_data(self, samples: List[Dict]) -> Dict:
        if not samples:
            return {}
        MIN_SUNRISE_HOUR, MAX_SUNSET_HOUR = 6, 20
        pv_samples = [s for s in samples
                      if (s.get('pv_w', 0) or 0) > 100
                      and MIN_SUNRISE_HOUR <= datetime.fromisoformat(s['timestamp']).hour <= MAX_SUNSET_HOUR]
        if not pv_samples:
            return {}
        first_light = min(pv_samples, key=lambda x: x['timestamp'])
        last_light = max(pv_samples, key=lambda x: x['timestamp'])
        first_time = datetime.fromisoformat(first_light['timestamp'])
        last_time = datetime.fromisoformat(last_light['timestamp'])
        daylight_hours = min((last_time - first_time).total_seconds() / 3600, 14.0)
        return {
            "daylight_start": first_light['timestamp'],
            "daylight_end": last_light['timestamp'],
            "daylight_hours": round(daylight_hours, 2),
            "season": self._get_season(first_time),
            "day_of_year": first_time.timetuple().tm_yday,
        }

    # =====================================================================
    # Riepilogo (KPI di testa) + insight azionabili
    # =====================================================================
    def _build_summary(self, autonomy: Dict, battery: Dict, pv: Dict, load: Dict) -> Dict:
        return {
            "autonomy_pct": autonomy.get("autonomy_pct"),
            "full_autonomy_day": autonomy.get("full_autonomy_day", False),
            "grid_import_kwh": autonomy.get("grid_import_kwh", 0),
            "pv_production_kwh": pv.get("production_kwh", 0),
            "load_consumption_kwh": load.get("consumption_kwh", 0),
            "battery_charge_kwh": battery.get("charge_kwh", 0),
            "battery_discharge_kwh": battery.get("discharge_kwh", 0),
            "battery_min_soc_pct": battery.get("min_soc_pct"),
            "battery_reached_full": battery.get("reached_full", False),
            "pv_surplus_estimate_kwh": pv.get("surplus_estimate_kwh", 0),
        }

    def _build_insights(self, autonomy: Dict, battery: Dict, pv: Dict, load: Dict, diagnostics: Dict) -> List[Dict]:
        ins: List[Dict] = []

        # Autonomia / prelievo
        gi = autonomy.get("grid_import_kwh", 0) or 0
        if autonomy.get("full_autonomy_day"):
            ins.append({"level": "good", "icon": "🌿",
                        "text": "Giornata in piena autonomia: nessun prelievo significativo dalla rete."})
        elif gi > 0:
            win = autonomy.get("main_import_window")
            extra = f" concentrato in {win}" if win else ""
            ins.append({"level": "warn", "icon": "⚡",
                        "text": f"Prelevati {gi:.2f} kWh dalla rete{extra}: in quelle fasce PV+batteria non bastano."})

        # Batteria
        msoc = battery.get("min_soc_pct")
        if msoc is not None and msoc < 20:
            ins.append({"level": "warn", "icon": "🔋",
                        "text": f"Batteria scesa al {msoc:.0f}% (alle {battery.get('min_soc_time')}): scarica profonda, "
                                f"riduci i carichi notturni o aumenta la capacità."})
        if battery.get("status") == "data_available" and not battery.get("reached_full"):
            ins.append({"level": "info", "icon": "🪫",
                        "text": "Oggi la batteria non ha raggiunto il 100%: se capita per più giorni di fila, "
                                "valuta una ricarica di bilanciamento delle celle LiFePO4."})
        if (battery.get("time_below_relay_v_min") or 0) > 0:
            ins.append({"level": "info", "icon": "🔌",
                        "text": f"~{battery.get('time_below_relay_v_min')} min sotto la soglia relè "
                                f"({battery.get('relay_on_v')}V): il relè sarebbe intervenuto in quelle fasi."})

        # Surplus PV → azione
        if pv.get("surplus_window"):
            se = pv.get("surplus_estimate_kwh", 0) or 0
            ins.append({"level": "action", "icon": "🌞",
                        "text": f"Surplus PV stimato ~{se:.2f} kWh con batteria piena in {pv.get('surplus_window')}: "
                                f"sposta lì i carichi rimandabili (boiler, pompa, lavatrice) per non sprecarlo "
                                f"ed evitare il prelievo serale."})

        # Diagnostica
        if diagnostics.get("temp_warning"):
            ins.append({"level": "warn", "icon": "🌡️",
                        "text": f"Temperature elevate (inverter max {diagnostics.get('max_inverter_temp')}°C, "
                                f"dissipatore {diagnostics.get('max_heatsink_temp')}°C): verifica ventilazione."})
        if diagnostics.get("pv_night_anomalies", 0) > 0:
            ins.append({"level": "info", "icon": "🌙",
                        "text": "Rilevata produzione PV notturna anomala (possibile illuminazione sul pannello)."})

        if not ins:
            ins.append({"level": "good", "icon": "✅", "text": "Nessuna criticità rilevata nella giornata."})
        return ins

    # =====================================================================
    # Entry point
    # =====================================================================
    def analyze_daily_data(self, date: str) -> Dict:
        """Analizza una giornata e salva il risultato in tabella daily_analysis."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                samples = self._get_daily_samples(conn, date)
                if not samples:
                    logger.warning(f"Nessun campione trovato per {date}")
                    return {}

                autonomy = self._analyze_autonomy(samples)
                battery = self._analyze_battery_offgrid(samples)
                pv = self._analyze_pv_offgrid(samples, battery)
                load = self._analyze_load(samples)
                diagnostics = self._analyze_diagnostics(samples)
                seasonal = self._extract_seasonal_data(samples)
                summary = self._build_summary(autonomy, battery, pv, load)
                insights = self._build_insights(autonomy, battery, pv, load, diagnostics)

                analysis = {
                    "date": date,
                    "total_samples": len(samples),
                    "timestamp": datetime.now().isoformat(),
                    "data_coverage": self._coverage(samples),
                    "summary": summary,
                    "autonomy": autonomy,
                    "battery": battery,
                    "pv": pv,
                    "load": load,
                    "diagnostics": diagnostics,
                    "insights": insights,
                    "seasonal_insights": seasonal,
                }
                self._save_analysis(conn, analysis)
                logger.info(f"Analisi off-grid completata per {date}")
                return analysis
        except Exception as e:
            logger.error(f"Errore durante analisi {date}: {e}")
            return {}

    def _save_analysis(self, conn: sqlite3.Connection, analysis: Dict):
        """Salva l'analisi nella tabella daily_analysis (per la pagina e l'andamento stagionale)."""
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_analysis (
                    date TEXT PRIMARY KEY,
                    analysis_data TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                INSERT OR REPLACE INTO daily_analysis (date, analysis_data, created_at)
                VALUES (?, ?, ?)
            """, (analysis['date'], json.dumps(analysis, ensure_ascii=False), datetime.now().isoformat()))
            conn.commit()
        except Exception as e:
            logger.error(f"Errore nel salvare analisi: {e}")

    def cleanup_old_samples(self, date: str, keep_analysis: bool = True):
        """Cancella i campioni grezzi di una giornata (l'analisi resta in daily_analysis)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM samples WHERE DATE(timestamp) = ?", (date,))
                if not keep_analysis:
                    conn.execute("DELETE FROM daily_analysis WHERE date = ?", (date,))
                conn.commit()
                logger.info(f"Campioni cancellati per {date} (analisi {'mantenuta' if keep_analysis else 'rimossa'})")
        except Exception as e:
            logger.error(f"Errore durante pulizia {date}: {e}")


# Esempio di utilizzo manuale
if __name__ == "__main__":
    analyzer = DailyAnalyzer()
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    print(json.dumps(analyzer.analyze_daily_data(yesterday), indent=2, ensure_ascii=False))

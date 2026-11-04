#!/usr/bin/env python3
"""
Daily Analyzer - Analisi intelligente dati giornalieri
Analizza i campioni della giornata per estrarre insights prima della cancellazione
"""

import sqlite3
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Optional, Tuple
import json

# Configurazione logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DailyAnalyzer:
    """Analizzatore giornaliero per dati inverter"""
    
    def __init__(self, db_path: str = "data/inverter_history.db"):
        self.db_path = db_path
        
    def analyze_daily_data(self, date: str) -> Dict:
        """
        Analizza tutti i dati di una giornata specifica
        Returns: Dizionario con tutte le metriche analizzate
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Ottieni tutti i campioni della giornata
                samples = self._get_daily_samples(conn, date)
                if not samples:
                    logger.warning(f"Nessun campione trovato per {date}")
                    return {}
                
                # Analisi completa con struttura tematica
                analysis = {
                    "date": date,
                    "total_samples": len(samples),
                    "timestamp": datetime.now().isoformat(),
                    
                                         # 🌞 TEMATICA FOTOVOLTAICO (ESSENZIALE OFF-GRID)
                     "photovoltaic": {
                         "daily_summary": self._analyze_pv(samples),
                         "hourly_patterns": self._extract_hourly_pv_patterns(samples)
                     },
                     
                     # 🔋 TEMATICA BATTERIA (ESSENZIALE OFF-GRID)
                     "battery": {
                         "daily_summary": self._analyze_battery(samples)
                     },
                     
                     # ⚡ TEMATICA RETE ELETTRICA (SOLO IMPORT OFF-GRID)
                     "grid": {
                         "daily_summary": self._analyze_grid(samples),
                         "import_timing": self._analyze_grid_import_timing(samples)
                     },
                     
                     # 🏠 TEMATICA CASA/CARICO (ESSENZIALE)
                     "household": {
                         "daily_summary": self._analyze_load(samples)
                     },
                    
                    # 🚨 TEMATICA MONITORAGGIO
                    "monitoring": {
                        "anomaly_detection": self._detect_anomalies(samples)
                    },
                    
                    # 🌍 TEMATICA AMBIENTALE
                    "environmental": {
                        "seasonal_insights": self._extract_seasonal_data(samples)
                    },
                    
                    # Riepilogo generale (per compatibilità)
                    "daily_summary": self._calculate_daily_totals(samples)
                }
                
                # Salva analisi nel database
                self._save_analysis(conn, analysis)
                
                logger.info(f"Analisi completata per {date}: {len(analysis)} metriche calcolate")
                return analysis
                
        except Exception as e:
            logger.error(f"Errore durante analisi {date}: {e}")
            return {}
    
    def _get_daily_samples(self, conn: sqlite3.Connection, date: str) -> List[Dict]:
        """Recupera tutti i campioni di una giornata"""
        query = """
        SELECT * FROM samples 
        WHERE DATE(timestamp) = ? 
        ORDER BY timestamp ASC
        """
        cursor = conn.execute(query, (date,))
        columns = [description[0] for description in cursor.description]
        samples = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        # Riduci sovracampionamento - prendi 1 campione ogni 5 secondi
        if len(samples) > 17280:  # Più di 1 campione al minuto
            samples = samples[::12]  # 1 campione ogni minuto
        
        return samples
    
    def _aggregate_samples_by_interval(self, samples: List[Dict], interval_minutes: int) -> List[Dict]:
        """Raggruppa campioni per intervalli di tempo per calcoli più accurati"""
        if not samples:
            return []
        
        # Raggruppa per intervalli di N minuti
        aggregated = []
        current_interval = None
        current_samples = []
        
        for sample in samples:
            timestamp = datetime.fromisoformat(sample['timestamp'])
            interval_start = timestamp.replace(minute=(timestamp.minute // interval_minutes) * interval_minutes, second=0, microsecond=0)
            
            if current_interval != interval_start:
                if current_samples:
                    # Calcola media per l'intervallo
                    aggregated_sample = self._average_samples(current_samples)
                    aggregated.append(aggregated_sample)
                
                current_interval = interval_start
                current_samples = [sample]
            else:
                current_samples.append(sample)
        
        # Aggiungi l'ultimo intervallo
        if current_samples:
            aggregated_sample = self._average_samples(current_samples)
            aggregated.append(aggregated_sample)
        
        return aggregated
    
    def _average_samples(self, samples: List[Dict]) -> Dict:
        """Calcola la media di un gruppo di campioni"""
        if not samples:
            return {}
        
        result = {}
        numeric_fields = ['pv_w', 'battery_w', 'grid_w', 'load_w', 'pv_v', 'battery_v', 'grid_v', 'load_v']
        
        for field in numeric_fields:
            values = [s.get(field, 0) for s in samples if s.get(field) is not None]
            if values:
                result[field] = sum(values) / len(values)
        
        # Mantieni il timestamp del primo campione dell'intervallo
        result['timestamp'] = samples[0]['timestamp']
        
        return result
    
    def _calculate_energy_from_power(self, samples: List[Dict], field: str, interval_minutes: int) -> float:
        """Calcola energia in kWh da potenza in Watt usando aggregazione intelligente"""
        if not samples:
            return 0.0
        
        # Calcola energia totale: (media potenza × tempo intervallo) / 1000
        total_energy_wh = 0
        
        for sample in samples:
            power_w = sample.get(field, 0)
            if power_w is not None:
                # Energia = Potenza × Tempo (in Wh)
                energy_wh = abs(power_w) * (interval_minutes / 60.0)
                total_energy_wh += energy_wh
        
        # Converti in kWh
        return total_energy_wh / 1000.0
    
    def _analyze_pv_production_pattern(self, pv_samples: List[Dict]) -> Dict:
        """Analizza il pattern di produzione fotovoltaica per identificare anomalie"""
        if not pv_samples:
            return {}
        
        # Raggruppa per ore del giorno
        hourly_production = {}
        for sample in pv_samples:
            timestamp = datetime.fromisoformat(sample['timestamp'])
            hour = timestamp.hour
            if hour not in hourly_production:
                hourly_production[hour] = []
            hourly_production[hour].append(sample['pv_w'])
        
        # Calcola produzione media per ora
        hourly_avg = {}
        for hour, powers in hourly_production.items():
            hourly_avg[hour] = sum(powers) / len(powers)
        
        # Trova ore di produzione significativa (> 100W)
        significant_hours = [hour for hour, avg_power in hourly_avg.items() if avg_power > 100]
        
        # Identifica pattern diurno vs notturno
        daytime_hours = [h for h in range(6, 20)]  # 6:00 - 20:00
        nighttime_hours = [h for h in range(20, 24)] + [h for h in range(0, 6)]
        
        daytime_production = sum(hourly_avg.get(h, 0) for h in daytime_hours)
        nighttime_production = sum(hourly_avg.get(h, 0) for h in nighttime_hours)
        
        # Calcola rapporto produzione diurna vs notturna
        total_production = daytime_production + nighttime_production
        daytime_ratio = (daytime_production / total_production * 100) if total_production > 0 else 0
        
        return {
            "significant_hours": sorted(significant_hours),
            "daytime_production_ratio": round(daytime_ratio, 1),
            "peak_hour": max(hourly_avg.items(), key=lambda x: x[1])[0] if hourly_avg else None,
            "peak_hour_power": max(hourly_avg.values()) if hourly_avg else 0,
            "production_hours_count": len(significant_hours),
            "is_mostly_diurnal": daytime_ratio > 80  # Se >80% produzione è diurna
        }
    
    def _detect_pv_night_production(self, samples: List[Dict]) -> List[Dict]:
        """Rileva produzione PV notturna anomala (probabilmente lampioni)"""
        anomalies = []
        
        # Definisci ore notturne (22:00 - 6:00)
        night_hours = list(range(22, 24)) + list(range(0, 6))
        
        # Trova campioni PV notturni con produzione > 100W
        night_pv_samples = []
        for sample in samples:
            if sample.get('pv_w') and sample['pv_w'] > 100:
                timestamp = datetime.fromisoformat(sample['timestamp'])
                if timestamp.hour in night_hours:
                    night_pv_samples.append(sample)
        
        if night_pv_samples:
            # Calcola produzione notturna totale
            night_production_kwh = self._calculate_energy_from_power(night_pv_samples, 'pv_w', 5)
            
            # Se produzione notturna > 0.5 kWh, è probabilmente anomala
            if night_production_kwh > 0.5:
                                 anomalies.append({
                     "type": "pv_night_production_anomaly",
                     "timestamp": night_pv_samples[0]['timestamp'],
                     "night_production_kwh": round(night_production_kwh, 3),
                     "samples_count": len(night_pv_samples),
                     "max_night_power_kw": round(max(s['pv_w'] for s in night_pv_samples) / 1000, 3),
                     "severity": "medium",
                     "note": "Produzione PV notturna > 0.5 kWh (probabilmente lampioni)"
                 })
        
        return anomalies
    
    def _analyze_pv(self, samples: List[Dict]) -> Dict:
        """Analisi dettagliata fotovoltaico con soglie significative e temporali"""
        # Soglia significativa: solo produzione > 100W (0.1 kW) per evitare lampioni notturni
        SIGNIFICANT_PRODUCTION_THRESHOLD = 100  # Watt
        
        # Soglia temporale: solo produzione tra 6:00 e 20:00 (ore di luce realistiche)
        MIN_SUNRISE_HOUR = 6   # 6:00
        MAX_SUNSET_HOUR = 20   # 20:00
        
        # Filtra solo campioni con produzione significativa E nelle ore di luce
        significant_pv_samples = []
        for s in samples:
            if s.get('pv_w') and s['pv_w'] > SIGNIFICANT_PRODUCTION_THRESHOLD:
                timestamp = datetime.fromisoformat(s['timestamp'])
                if MIN_SUNRISE_HOUR <= timestamp.hour <= MAX_SUNSET_HOUR:
                    significant_pv_samples.append(s)
        
        if not significant_pv_samples:
            return {"status": "no_significant_production", "total_energy_kwh": 0, "note": "Produzione < 100W o fuori ore di luce"}
        
        # Analisi produzione significativa nelle ore di luce
        aggregated_samples = self._aggregate_samples_by_interval(significant_pv_samples, 5)
        total_energy_kwh = self._calculate_energy_from_power(aggregated_samples, 'pv_w', 5)
        
        # Trova inizio e fine produzione significativa (solo ore di luce)
        first_production = min(significant_pv_samples, key=lambda x: x['timestamp'])
        last_production = max(significant_pv_samples, key=lambda x: x['timestamp'])
        
        # Trova picco di produzione significativa
        peak_sample = max(significant_pv_samples, key=lambda x: x['pv_w'])
        
        # Calcola durata produzione significativa (solo ore di luce)
        start_time = datetime.fromisoformat(first_production['timestamp'])
        end_time = datetime.fromisoformat(last_production['timestamp'])
        duration_hours = (end_time - start_time).total_seconds() / 3600
        
        # Limita durata massima a 14 ore (realistico per produzione solare)
        duration_hours = min(duration_hours, 14.0)
        
        # Analisi pattern di produzione
        production_pattern = self._analyze_pv_production_pattern(significant_pv_samples)
        
        return {
            "status": "significant_production_detected",
            "total_energy_kwh": round(total_energy_kwh, 3),
            "production_start": first_production['timestamp'],
            "production_end": last_production['timestamp'],
            "duration_hours": round(duration_hours, 2),
            "peak_power_kw": round(peak_sample['pv_w'] / 1000, 3),
            "peak_time": peak_sample['timestamp'],
            "peak_voltage": peak_sample.get('pv_v'),
            "peak_current": peak_sample.get('pv_a'),
            "avg_power_kw": round(total_energy_kwh / duration_hours if duration_hours > 0 else 0, 3),
            "significant_threshold_w": SIGNIFICANT_PRODUCTION_THRESHOLD,
            "sunrise_hour": MIN_SUNRISE_HOUR,
            "sunset_hour": MAX_SUNSET_HOUR,
            "production_pattern": production_pattern,
            "note": f"Produzione > {SIGNIFICANT_PRODUCTION_THRESHOLD}W tra {MIN_SUNRISE_HOUR}:00-{MAX_SUNSET_HOUR}:00"
        }
    
    def _analyze_battery(self, samples: List[Dict]) -> Dict:
        """Analisi dettagliata batteria"""
        battery_samples = [s for s in samples if s.get('battery_w') is not None]
        
        if not battery_samples:
            return {"status": "no_data", "total_energy_kwh": 0}
        
        # Calcolo energia netta batteria - AGGREGAZIONE INTELLIGENTE
        # Raggruppa per intervalli di 5 minuti
        aggregated_samples = self._aggregate_samples_by_interval(battery_samples, 5)
        
        # Calcola energia totale in kWh
        total_energy_kwh = self._calculate_energy_from_power(aggregated_samples, 'battery_w', 5)
        
        # Trova momenti chiave
        charging_samples = [s for s in battery_samples if s['battery_w'] > 0]
        discharging_samples = [s for s in battery_samples if s['battery_w'] < 0]
        
        # Analisi tensione per trovare reset (44V)
        voltage_samples = [s for s in samples if s.get('battery_v')]
        reset_events = [s for s in voltage_samples if s['battery_v'] <= 44.0]
        
        # Calcola energie carica/scarica aggregate
        charging_energy_kwh = self._calculate_energy_from_power(
            self._aggregate_samples_by_interval(charging_samples, 5), 'battery_w', 5
        ) if charging_samples else 0
        
        discharging_energy_kwh = self._calculate_energy_from_power(
            self._aggregate_samples_by_interval(discharging_samples, 5), 'battery_w', 5
        ) if discharging_samples else 0
        
        analysis = {
            "status": "data_available",
            "total_energy_kwh": round(total_energy_kwh, 3),
            "charging_energy_kwh": round(charging_energy_kwh, 3),
            "discharging_energy_kwh": round(discharging_energy_kwh, 3),
            "reset_events_count": len(reset_events),
            "reset_times": [s['timestamp'] for s in reset_events],
            "avg_voltage": round(sum(s['battery_v'] for s in voltage_samples) / len(voltage_samples), 2),
            "min_voltage": min(s['battery_v'] for s in voltage_samples),
            "max_voltage": max(s['battery_v'] for s in voltage_samples)
        }
        
        # Trova inizio carica batteria (prima transizione positiva)
        if charging_samples:
            first_charge = min(charging_samples, key=lambda x: x['timestamp'])
            analysis["first_charge_time"] = first_charge['timestamp']
            analysis["first_charge_voltage"] = first_charge['battery_v']
        
        # Trova fine carica batteria (ultima transizione positiva)
        if charging_samples:
            last_charge = max(charging_samples, key=lambda x: x['timestamp'])
            analysis["last_charge_time"] = last_charge['timestamp']
            analysis["last_charge_voltage"] = last_charge['battery_v']
        
        return analysis
    
    def _analyze_grid(self, samples: List[Dict]) -> Dict:
        """Analisi dettagliata rete elettrica"""
        grid_samples = [s for s in samples if s.get('grid_w') is not None]
        
        if not grid_samples:
            return {"status": "no_data", "total_energy_kwh": 0}
        
        # Calcolo energia totale dalla rete - AGGREGAZIONE INTELLIGENTE
        aggregated_samples = self._aggregate_samples_by_interval(grid_samples, 5)
        total_energy_kwh = self._calculate_energy_from_power(aggregated_samples, 'grid_w', 5)
        
        # Trova primo assorbimento mattutino (dopo reset batteria)
        morning_consumption = []
        for i, sample in enumerate(grid_samples):
            if sample['grid_w'] > 0:  # Assorbimento dalla rete
                # Verifica se è mattina (prima delle 12:00)
                sample_time = datetime.fromisoformat(sample['timestamp'])
                if sample_time.hour < 12:
                    morning_consumption.append(sample)
        
        # Analisi pattern notturno
        night_samples = [s for s in grid_samples if datetime.fromisoformat(s['timestamp']).hour >= 22 or datetime.fromisoformat(s['timestamp']).hour <= 6]
        night_consumption = self._calculate_energy_from_power(
            self._aggregate_samples_by_interval(night_samples, 5), 'grid_w', 5
        ) if night_samples else 0
        
        # Calcola energie import/export aggregate
        import_samples = [s for s in grid_samples if s['grid_w'] > 0]
        export_samples = [s for s in grid_samples if s['grid_w'] < 0]
        
        import_energy_kwh = self._calculate_energy_from_power(
            self._aggregate_samples_by_interval(import_samples, 5), 'grid_w', 5
        ) if import_samples else 0
        
        export_energy_kwh = self._calculate_energy_from_power(
            self._aggregate_samples_by_interval(export_samples, 5), 'grid_w', 5
        ) if export_samples else 0
        
        morning_consumption_kwh = self._calculate_energy_from_power(
            self._aggregate_samples_by_interval(morning_consumption, 5), 'grid_w', 5
        ) if morning_consumption else 0
        
        return {
            "status": "data_available",
            "total_energy_kwh": round(total_energy_kwh, 3),
            "import_energy_kwh": round(import_energy_kwh, 3),
            "export_energy_kwh": round(export_energy_kwh, 3),
            "first_morning_consumption": morning_consumption[0]['timestamp'] if morning_consumption else None,
            "morning_consumption_kwh": round(morning_consumption_kwh, 3),
            "night_consumption_kwh": round(night_consumption, 3),
            "peak_import_kw": round(max(s['grid_w'] for s in grid_samples if s['grid_w'] > 0) / 1000, 3) if any(s['grid_w'] > 0 for s in grid_samples) else 0,
            "peak_export_kw": round(abs(min(s['grid_w'] for s in grid_samples if s['grid_w'] < 0)) / 1000, 3) if any(s['grid_w'] < 0 for s in grid_samples) else 0
        }
    
    def _analyze_grid_import_timing(self, samples: List[Dict]) -> Dict:
        """Analizza i tempi di import dalla rete (OFF-GRID: solo import)"""
        if not samples:
            return {}
        
        grid_samples = [s for s in samples if s.get('grid_w') is not None and s['grid_w'] > 0]
        if not grid_samples:
            return {"import_timing": "Nessun import dalla rete"}
        
        # Trova ora di massimo import
        max_import_hour = 0
        max_import_energy = 0
        
        for sample in grid_samples:
            timestamp = datetime.fromisoformat(sample['timestamp'])
            hour = timestamp.hour
            energy_kwh = (sample['grid_w'] * 5 / 60) / 1000
            
            if energy_kwh > max_import_energy:
                max_import_energy = energy_kwh
                max_import_hour = hour
        
        return {
            "max_import_hour": f"{max_import_hour:02d}:00",
            "max_import_energy_kwh": round(max_import_energy, 3)
        }
    

    

    
    def _analyze_load(self, samples: List[Dict]) -> Dict:
        """Analisi dettagliata carico casa con consumo notturno e mattutino"""
        load_samples = [s for s in samples if s.get('load_w') is not None]
        
        if not load_samples:
            return {"status": "no_data", "total_energy_kwh": 0}
        
        # Soglie temporali per produzione solare
        MIN_SUNRISE_HOUR = 6   # 6:00
        MAX_SUNSET_HOUR = 20   # 20:00
        
        # CALCOLO ENERGIA TOTALE CORRETTO - usa aggregazione intelligente
        aggregated_load_samples = self._aggregate_samples_by_interval(load_samples, 5)
        total_energy = self._calculate_energy_from_power(aggregated_load_samples, 'load_w', 5)
        
        # Trova picchi di consumo
        peak_load = max(s['load_w'] for s in load_samples)
        peak_load_sample = next(s for s in load_samples if s['load_w'] == peak_load)
        
        # Calcola potenza media (corretta)
        avg_load_w = sum(s['load_w'] for s in load_samples) / len(load_samples) if load_samples else 0
        
        # Analisi fattore di potenza
        pf_samples = [s for s in load_samples if s.get('load_pf') is not None]
        avg_pf = sum(s['load_pf'] for s in pf_samples) / len(pf_samples) if pf_samples else None
        
        # ANALISI CONSUMO NOTTURNO (quando solare non produce)
        night_load_samples = []
        morning_load_samples = []
        
        for s in load_samples:
            timestamp = datetime.fromisoformat(s['timestamp'])
            hour = timestamp.hour
            
            # Ore notturne: 22:00 - 6:00 (quando solare non produce)
            if hour >= 22 or hour < MIN_SUNRISE_HOUR:
                night_load_samples.append(s)
            
            # Ore mattutine: 6:00 - 12:00 (prima che solare produca significativamente)
            if MIN_SUNRISE_HOUR <= hour < 12:
                morning_load_samples.append(s)
        
        # Calcola consumi notturni e mattutini - CORREZIONE: usa solo campioni significativi
        night_consumption_kwh = 0
        morning_consumption_kwh = 0
        
        if night_load_samples:
            # Filtra solo campioni con consumo significativo (> 100W) per evitare errori
            significant_night_samples = [s for s in night_load_samples if s.get('load_w', 0) > 100]
            if significant_night_samples:
                aggregated_night = self._aggregate_samples_by_interval(significant_night_samples, 5)
                night_consumption_kwh = self._calculate_energy_from_power(aggregated_night, 'load_w', 5)
        
        if morning_load_samples:
            # Filtra solo campioni con consumo significativo (> 100W) per evitare errori
            significant_morning_samples = [s for s in morning_load_samples if s.get('load_w', 0) > 100]
            if significant_morning_samples:
                aggregated_morning = self._aggregate_samples_by_interval(significant_morning_samples, 5)
                morning_consumption_kwh = self._calculate_energy_from_power(aggregated_morning, 'load_w', 5)
        
        # Calcola consumo totale durante ore buie (notturno + mattutino)
        total_dark_hours_consumption = night_consumption_kwh + morning_consumption_kwh
        
        # Calcola percentuale consumo ore buie vs totale
        dark_hours_percentage = (total_dark_hours_consumption / total_energy * 100) if total_energy > 0 else 0
        
        # Trova orari di inizio e fine consumo notturno
        night_start_time = None
        night_end_time = None
        if night_load_samples:
            night_start_time = min(night_load_samples, key=lambda x: x['timestamp'])['timestamp']
            night_end_time = max(night_load_samples, key=lambda x: x['timestamp'])['timestamp']
        
        # Trova orario primo consumo mattutino
        morning_start_time = None
        if morning_load_samples:
            morning_start_time = min(morning_load_samples, key=lambda x: x['timestamp'])['timestamp']
        
        return {
            "status": "data_available",
            "total_energy_kwh": round(total_energy, 3),
            "peak_load_kw": round(peak_load / 1000, 3),
            "peak_load_time": peak_load_sample['timestamp'],
            "avg_load_kw": round(avg_load_w / 1000, 3),  # Potenza media in kW
            "avg_power_factor": round(avg_pf, 3) if avg_pf else None,
            "load_samples_count": len(load_samples),
            
            # NUOVO: Consumo durante ore buie
            "night_consumption_kwh": round(night_consumption_kwh, 3),
            "morning_consumption_kwh": round(morning_consumption_kwh, 3),
            "total_dark_hours_consumption_kwh": round(total_dark_hours_consumption, 3),
            "dark_hours_percentage": round(dark_hours_percentage, 1),
            
            # Orari consumo ore buie
            "night_start_time": night_start_time,
            "night_end_time": night_end_time,
            "morning_start_time": morning_start_time,
            
            # Note informative
            "note": f"Consumo ore buie: {round(dark_hours_percentage, 1)}% del totale (22:00-6:00 + 6:00-12:00)"
        }
    
    def _detect_anomalies(self, samples: List[Dict]) -> Dict:
        """Rileva anomalie nei dati con soglie intelligenti"""
        anomalies = []
        
        # Usa campioni aggregati per calcoli più stabili
        aggregated_samples = self._aggregate_samples_by_interval(samples, 5)
        
        # Controlla picchi anomali di potenza
        for field in ['pv_w', 'battery_w', 'grid_w', 'load_w']:
            if not aggregated_samples:
                continue
                
            values = [s.get(field, 0) for s in aggregated_samples if s.get(field) is not None]
            if not values:
                continue
            
            # Filtra valori estremi per calcolo statistiche
            filtered_values = [v for v in values if abs(v) < 10000]  # Esclude valori > 10kW
            if not filtered_values:
                continue
                
            mean_val = sum(filtered_values) / len(filtered_values)
            std_dev = (sum((x - mean_val) ** 2 for x in filtered_values) / len(filtered_values)) ** 0.5
            
            # Soglie intelligenti basate sul tipo di campo
            if field == 'pv_w':
                # PV: soglia più alta (variazioni normali) + filtro produzione significativa
                threshold = mean_val + (4 * std_dev)
                min_threshold = 500   # Minimo 500W per anomalia (più basso per PV)
                # Filtra solo campioni con produzione significativa (> 100W)
                filtered_values = [v for v in filtered_values if v > 100]
                if not filtered_values:
                    continue
            elif field == 'grid_w':
                # Rete: soglia moderata (consumo domestico)
                threshold = mean_val + (3.5 * std_dev)
                min_threshold = 500   # Minimo 500W per anomalia
            elif field == 'load_w':
                # Carico: soglia moderata
                threshold = mean_val + (3.5 * std_dev)
                min_threshold = 800   # Minimo 800W per anomalia
            else:
                # Batteria: soglia standard
                threshold = mean_val + (3 * std_dev)
                min_threshold = 300   # Minimo 300W per anomalia
            
            # Usa la soglia più alta tra statistica e minima
            threshold = max(threshold, min_threshold)
            
            # Trova valori che superano la soglia
            anomaly_samples = [s for s in aggregated_samples if s.get(field, 0) > threshold]
            
            for sample in anomaly_samples:
                severity = "high" if sample[field] > mean_val + (5 * std_dev) else "medium"
                anomalies.append({
                    "type": f"power_peak_{field}",
                    "timestamp": sample['timestamp'],
                    "value": round(sample[field] / 1000, 3),  # Converti in kW
                    "threshold": round(threshold / 1000, 3),   # Converti in kW
                    "severity": severity
                })
        
        # Controlla variazioni improvvise (solo su campioni aggregati)
        for i in range(1, len(aggregated_samples)):
            prev = aggregated_samples[i-1]
            curr = aggregated_samples[i]
            
            for field in ['pv_w', 'battery_w', 'grid_w', 'load_w']:
                if field in prev and field in curr:
                    change = abs(curr[field] - prev[field])
                    # Soglia più alta per variazioni (5 minuti di aggregazione)
                    if change > 8000:  # Variazione > 8kW
                        anomalies.append({
                            "type": f"sudden_change_{field}",
                            "timestamp": curr['timestamp'],
                            "change_kw": round(change / 1000, 3),  # Converti in kW
                            "from": round(prev[field] / 1000, 3),  # Converti in kW
                            "to": round(curr[field] / 1000, 3),    # Converti in kW
                            "severity": "medium"
                        })
        
        # Controllo specifico per produzione PV notturna anomala
        pv_night_anomalies = self._detect_pv_night_production(samples)
        anomalies.extend(pv_night_anomalies)
        
        return {
            "total_anomalies": len(anomalies),
            "anomalies": anomalies,
            "high_severity": len([a for a in anomalies if a['severity'] == 'high']),
            "medium_severity": len([a for a in anomalies if a['severity'] == 'medium']),
            "pv_night_anomalies": len(pv_night_anomalies)
        }
    
    def _calculate_daily_totals(self, samples: List[Dict]) -> Dict:
        """Calcola totali giornalieri per tutte le energie usando aggregazione intelligente"""
        totals = {}
        
        # Usa campioni aggregati per calcoli più accurati
        aggregated_samples = self._aggregate_samples_by_interval(samples, 5)
        
        for field in ['pv_w', 'battery_w', 'grid_w', 'load_w']:
            # Calcola energia usando la funzione helper
            total_kwh = self._calculate_energy_from_power(aggregated_samples, field, 5)
            totals[f"{field}_total_kwh"] = round(total_kwh, 3)
        
        # Calcola efficienza sistema totale
        if 'pv_w_total_kwh' in totals and 'load_w_total_kwh' in totals:
            pv_total = totals['pv_w_total_kwh']
            load_total = totals['load_w_total_kwh']
            if pv_total > 0:
                totals['system_efficiency'] = round((load_total / pv_total) * 100, 2)
        
        # NUOVO: Calcola efficienza durante ore di luce vs ore buie
        # Separa campioni per ore di luce e ore buie
        MIN_SUNRISE_HOUR = 6
        MAX_SUNSET_HOUR = 20
        
        daylight_samples = []
        dark_hours_samples = []
        
        for s in samples:
            if s.get('load_w') is not None:  # Solo campioni con carico
                timestamp = datetime.fromisoformat(s['timestamp'])
                hour = timestamp.hour
                
                if MIN_SUNRISE_HOUR <= hour <= MAX_SUNSET_HOUR:
                    daylight_samples.append(s)
                else:
                    dark_hours_samples.append(s)
        
        # Calcola energia carico durante ore di luce
        daylight_load_kwh = 0
        if daylight_samples:
            aggregated_daylight = self._aggregate_samples_by_interval(daylight_samples, 5)
            daylight_load_kwh = self._calculate_energy_from_power(aggregated_daylight, 'load_w', 5)
        
        # Calcola energia carico durante ore buie
        dark_hours_load_kwh = 0
        if dark_hours_samples:
            aggregated_dark = self._aggregate_samples_by_interval(dark_hours_samples, 5)
            dark_hours_load_kwh = self._calculate_energy_from_power(aggregated_dark, 'load_w', 5)
        
        # Calcola efficienza durante ore di luce (quando PV produce)
        daylight_efficiency = 0
        if 'pv_w_total_kwh' in totals and daylight_load_kwh > 0:
            daylight_efficiency = round((daylight_load_kwh / totals['pv_w_total_kwh']) * 100, 2)
        
        # Calcola rapporto consumo ore buie vs ore di luce
        consumption_ratio = 0
        if daylight_load_kwh > 0:
            consumption_ratio = round((dark_hours_load_kwh / daylight_load_kwh) * 100, 2)
        
        # Aggiungi metriche di efficienza temporale
        totals.update({
            "daylight_load_kwh": round(daylight_load_kwh, 3),
            "dark_hours_load_kwh": round(dark_hours_load_kwh, 3),
            "daylight_efficiency": daylight_efficiency,
            "consumption_ratio_dark_vs_light": consumption_ratio,
            "note": f"Efficienza ore luce: {daylight_efficiency}%, Consumo ore buie: {consumption_ratio}% vs ore luce"
        })
        
        return totals
    
    def _extract_seasonal_data(self, samples: List[Dict]) -> Dict:
        """Estrae dati utili per analisi stagionale con soglie temporali realistiche"""
        if not samples:
            return {}
        
        # Soglie temporali realistiche per produzione solare
        MIN_SUNRISE_HOUR = 6   # 6:00
        MAX_SUNSET_HOUR = 20   # 20:00
        
        # Trova sunrise e sunset approssimativi (solo ore di luce realistiche)
        pv_samples = []
        for s in samples:
            if s.get('pv_w', 0) > 100:  # Solo produzione significativa
                timestamp = datetime.fromisoformat(s['timestamp'])
                if MIN_SUNRISE_HOUR <= timestamp.hour <= MAX_SUNSET_HOUR:
                    pv_samples.append(s)
        
        if pv_samples:
            first_light = min(pv_samples, key=lambda x: x['timestamp'])
            last_light = max(pv_samples, key=lambda x: x['timestamp'])
            
            first_time = datetime.fromisoformat(first_light['timestamp'])
            last_time = datetime.fromisoformat(last_light['timestamp'])
            
            daylight_hours = (last_time - first_time).total_seconds() / 3600
            
            # Limita ore di luce a massimo 14 ore (realistico)
            daylight_hours = min(daylight_hours, 14.0)
            
            return {
                "daylight_start": first_light['timestamp'],
                "daylight_end": last_light['timestamp'],
                "daylight_hours": round(daylight_hours, 2),
                "season": self._get_season(first_time),
                "day_of_year": first_time.timetuple().tm_yday,
                "sunrise_hour": MIN_SUNRISE_HOUR,
                "sunset_hour": MAX_SUNSET_HOUR,
                "note": f"Analisi limitata a ore {MIN_SUNRISE_HOUR}:00-{MAX_SUNSET_HOUR}:00"
            }
        
        return {}
    
    # ===== FUNZIONI HELPER PER STRUTTURA TEMATICA =====
    
    def _extract_hourly_pv_patterns(self, samples: List[Dict]) -> Dict:
        """Estrae pattern orari della produzione fotovoltaica"""
        if not samples:
            return {}
        
        # Filtra solo campioni con produzione significativa (>100W)
        pv_samples = [s for s in samples if s.get('pv_w', 0) > 100]
        
        # Analizza ogni ora
        hourly_data = {}
        for hour in range(24):
            hour_samples = [s for s in pv_samples if datetime.fromisoformat(s['timestamp']).hour == hour]
            if hour_samples:
                avg_power = sum(s['pv_w'] for s in hour_samples) / len(hour_samples)
                total_energy = sum(s['pv_w'] * 5/60 for s in hour_samples) / 1000  # kWh
                hourly_data[hour] = {
                    "avg_power_kw": round(avg_power / 1000, 3),
                    "energy_kwh": round(total_energy, 3),
                    "samples_count": len(hour_samples)
                }
        
        # Calcola metriche aggregate
        significant_hours = len([h for h, d in hourly_data.items() if d['energy_kwh'] > 0.01])
        total_energy = sum(d['energy_kwh'] for d in hourly_data.values())
        utilization_rate = (significant_hours / 24) * 100 if total_energy > 0 else 0
        
        # Trova ora di picco
        peak_hour = max(hourly_data.items(), key=lambda x: x[1]['avg_power_kw']) if hourly_data else None
        
        return {
            "significant_hours": significant_hours,
            "utilization_rate": round(utilization_rate, 1),
            "peak_hour": f"{peak_hour[0]:02d}:00" if peak_hour else "N/A",
            "hourly_breakdown": hourly_data,
            "total_energy_kwh": round(total_energy, 3)
        }
    

    
    def _calculate_pv_efficiency_metrics(self, samples: List[Dict]) -> Dict:
        """Calcola metriche di efficienza fotovoltaica"""
        if not samples:
            return {}
        
        # Filtra campioni con produzione significativa
        pv_samples = [s for s in samples if s.get('pv_w', 0) > 100]
        
        if not pv_samples:
            return {
                "efficiency_score": 0,
                "efficiency_grade": "N/A",
                "temporal_efficiency": 0,
                "intensity_efficiency": 0
            }
        
        # Calcola efficienza temporale (ore di produzione vs ore di luce)
        production_hours = len(set(datetime.fromisoformat(s['timestamp']).hour for s in pv_samples))
        temporal_efficiency = (production_hours / 14) * 100  # 14 ore max di luce
        
        # Calcola efficienza di intensità (media potenza vs picco teorico)
        avg_power = sum(s['pv_w'] for s in pv_samples) / len(pv_samples)
        max_power = max(s['pv_w'] for s in pv_samples)
        intensity_efficiency = (avg_power / max_power) * 100 if max_power > 0 else 0
        
        # Score complessivo (media delle due efficienze)
        efficiency_score = (temporal_efficiency + intensity_efficiency) / 2
        
        # Assegna classe di efficienza
        if efficiency_score >= 80:
            grade = "Eccellente"
        elif efficiency_score >= 60:
            grade = "Buona"
        elif efficiency_score >= 40:
            grade = "Discreta"
        else:
            grade = "Bassa"
        
        return {
            "efficiency_score": round(efficiency_score, 1),
            "efficiency_grade": grade,
            "temporal_efficiency": round(temporal_efficiency, 1),
            "intensity_efficiency": round(intensity_efficiency, 1)
        }
    
    
    
    def _analyze_battery_voltage_patterns(self, samples: List[Dict]) -> Dict:
        """Analizza i pattern di tensione della batteria"""
        if not samples:
            return {}
        
        voltages = [s.get('battery_v', 0) for s in samples if s.get('battery_v') is not None]
        
        if not voltages:
            return {
                "voltage_stability": 0,
                "voltage_grade": "N/A",
                "voltage_range": 0,
                "reset_events": 0
            }
        
        # Calcola statistiche di tensione
        avg_voltage = sum(voltages) / len(voltages)
        min_voltage = min(voltages)
        max_voltage = max(voltages)
        voltage_range = max_voltage - min_voltage
        
        # Calcola stabilità (deviazione standard normalizzata)
        variance = sum((v - avg_voltage) ** 2 for v in voltages) / len(voltages)
        std_dev = variance ** 0.5
        voltage_stability = max(0, 100 - (std_dev / avg_voltage) * 100)
        
        # Assegna classe di stabilità
        if voltage_stability >= 90:
            grade = "Eccellente"
        elif voltage_stability >= 75:
            grade = "Buona"
        elif voltage_stability >= 60:
            grade = "Discreta"
        else:
            grade = "Bassa"
        
        # Conta eventi di reset (tensione <= 44V)
        reset_events = len([v for v in voltages if v <= 44])
        
        return {
            "voltage_stability": round(voltage_stability, 1),
            "voltage_grade": grade,
            "voltage_range": round(voltage_range, 2),
            "reset_events": reset_events,
            "avg_voltage": round(avg_voltage, 2),
            "min_voltage": round(min_voltage, 2),
            "max_voltage": round(max_voltage, 2)
        }
    

    

    

    

    

    
    def _get_season(self, date: datetime) -> str:
        """Determina la stagione basata sulla data"""
        month = date.month
        if month in [12, 1, 2]:
            return "winter"
        elif month in [3, 4, 5]:
            return "spring"
        elif month in [6, 7, 8]:
            return "summer"
        else:
            return "autumn"
    
    def _save_analysis(self, conn: sqlite3.Connection, analysis: Dict):
        """Salva l'analisi nel database per riferimento futuro"""
        try:
            # Crea tabella se non esiste
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_analysis (
                    date TEXT PRIMARY KEY,
                    analysis_data TEXT,
                    created_at TEXT
                )
            """)
            
            # Converti datetime in stringhe per serializzazione JSON
            def convert_datetime(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                elif isinstance(obj, dict):
                    return {k: convert_datetime(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [convert_datetime(item) for item in obj]
                else:
                    return obj
            
            # Applica conversione a tutto l'oggetto analysis
            serializable_analysis = convert_datetime(analysis)
            
            # Inserisci o aggiorna analisi
            conn.execute("""
                INSERT OR REPLACE INTO daily_analysis (date, analysis_data, created_at)
                VALUES (?, ?, ?)
            """, (
                analysis['date'],
                json.dumps(serializable_analysis, indent=2),
                datetime.now().isoformat()
            ))
            
            conn.commit()
            logger.info(f"Analisi salvata nel database per {analysis['date']}")
            
        except Exception as e:
            logger.error(f"Errore nel salvare analisi: {e}")
    
    def cleanup_old_samples(self, date: str, keep_analysis: bool = True):
        """
        Pulisce i campioni vecchi dopo aver salvato l'analisi
        Args:
            date: Data dei campioni da cancellare
            keep_analysis: Se mantenere l'analisi nel database
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                if keep_analysis:
                    # Mantieni solo l'analisi, cancella i campioni
                    conn.execute("DELETE FROM samples WHERE DATE(timestamp) = ?", (date,))
                    logger.info(f"Campioni cancellati per {date}, analisi mantenuta")
                else:
                    # Cancella tutto
                    conn.execute("DELETE FROM samples WHERE DATE(timestamp) = ?", (date,))
                    conn.execute("DELETE FROM daily_analysis WHERE date = ?", (date,))
                    logger.info(f"Tutti i dati cancellati per {date}")
                
                conn.commit()
                
        except Exception as e:
            logger.error(f"Errore durante pulizia {date}: {e}")

# Esempio di utilizzo
if __name__ == "__main__":
    analyzer = DailyAnalyzer()
    
    # Analizza ieri
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    analysis = analyzer.analyze_daily_data(yesterday)
    
    if analysis:
        print(f"Analisi completata per {yesterday}")
        print(f"Produzione PV: {analysis['photovoltaic']['daily_summary'].get('total_energy_kwh', 0)} kWh")
        print(f"Energia batteria: {analysis['battery']['daily_summary'].get('total_energy_kwh', 0)} kWh")
        print(f"Anomalie rilevate: {analysis['monitoring']['anomaly_detection']['total_anomalies']}")
        
        # Opzionale: cancella campioni dopo analisi
        # analyzer.cleanup_old_samples(yesterday, keep_analysis=True)
    else:
        print(f"Nessun dato trovato per {yesterday}")

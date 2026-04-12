#!/usr/bin/env python3
"""
Auto-Graph Generator per Inverter Dashboard
Genera automaticamente grafici dalle analisi intelligenti
Versione: 1.0
Autore: Alessandro
"""

import sqlite3
import json
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta
import pandas as pd
import os
from pathlib import Path

# Configurazione grafici
plt.style.use('dark_background')
sns.set_palette("husl")

class AutoGraphGenerator:
    def __init__(self, db_path="data/inverter_history.db"):
        self.db_path = db_path
        self.output_dir = Path("graphs")
        self.output_dir.mkdir(exist_ok=True)
        
        # Crea sottocartelle per organizzazione
        self.monthly_dir = self.output_dir / "monthly"
        self.yearly_dir = self.output_dir / "yearly"
        self.monthly_dir.mkdir(exist_ok=True)
        self.yearly_dir.mkdir(exist_ok=True)
        
        # Colori tematici
        self.colors = {
            'pv': '#22c55e',      # Verde fotovoltaico
            'battery': '#3b82f6',  # Blu batteria
            'grid': '#a855f7',     # Viola rete
            'load': '#f59e0b',     # Arancione carico
            'efficiency': '#10b981' # Verde efficienza
        }
        
        # Mesi per confronti
        self.months = {
            '01': 'Gennaio', '02': 'Febbraio', '03': 'Marzo', '04': 'Aprile',
            '05': 'Maggio', '06': 'Giugno', '07': 'Luglio', '08': 'Agosto',
            '09': 'Settembre', '10': 'Ottobre', '11': 'Novembre', '12': 'Dicembre'
        }
        
    def generate_all_graphs(self):
        """Genera tutti i grafici principali"""
        print("🚀 Generazione grafici automatici in corso...")
        
        try:
            # 1. Grafici mensili (ultimi 3 mesi)
            self.generate_monthly_graphs()
            
            # 2. Grafici annuali
            self.generate_yearly_graphs()
            
            # 3. Grafici di confronto mensile
            self.generate_monthly_comparison()
            
            # 4. Grafici di confronto annuale
            self.generate_yearly_comparison()
            
            print("✅ Tutti i grafici generati con successo!")
            
        except Exception as e:
            print(f"❌ Errore durante generazione grafici: {e}")
    
    def generate_monthly_graphs(self):
        """Genera grafici per TUTTI i mesi disponibili nel database"""
        print("📅 Generando grafici mensili...")
        
        # Trova tutti i mesi disponibili nel database
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT DISTINCT strftime('%Y-%m', date) as month 
            FROM daily_analysis 
            ORDER BY month
            """
            months = pd.read_sql_query(query, conn)
        
        if months.empty:
            print("⚠️ Nessun mese disponibile nel database")
            return
            
        print(f"📊 Trovati {len(months)} mesi nel database: {', '.join(months['month'].tolist())}")
        
        for _, row in months.iterrows():
            month = row['month']
            month_name = self.months[month[-2:]]  # Estrai il mese (es: '08' -> 'Agosto')
            year = month[:4]
            
            print(f"📊 Generando grafici per {month_name} {year}...")
            
            # Produzione PV mensile
            self.plot_monthly_pv_production(month, month_name)
            
            # Consumo casa mensile
            self.plot_monthly_household_consumption(month, month_name)
            
            # Efficienza sistema mensile
            self.plot_monthly_system_efficiency(month, month_name)
            
            # Cicli batteria mensili
            self.plot_monthly_battery_cycles(month, month_name)
            
            # GRAFICI GIORNALIERI MENSILI - NUOVI!
            self.plot_monthly_daily_pv_comparison(month, month_name)
            self.plot_monthly_daily_consumption_comparison(month, month_name)
            self.plot_monthly_daily_efficiency_comparison(month, month_name)
    
    def generate_yearly_graphs(self):
        """Genera grafici annuali"""
        print("📈 Generando grafici annuali...")
        
        current_year = datetime.now().year
        
        # Produzione PV annuale
        self.plot_yearly_pv_production(current_year)
        
        # Consumo casa annuale
        self.plot_yearly_household_consumption(current_year)
        
        # Efficienza sistema annuale
        self.plot_yearly_system_efficiency(current_year)
        
        # Confronto mesi dell'anno
        self.plot_yearly_monthly_comparison(current_year)
    
    def generate_monthly_comparison(self):
        """Genera grafici di confronto mensile"""
        print("🔄 Generando confronti mensili...")
        
        # Confronto produzione PV ultimi 6 mesi
        self.plot_monthly_pv_comparison()
        
        # Confronto consumo ultimi 6 mesi
        self.plot_monthly_consumption_comparison()
        
        # Confronto efficienza ultimi 6 mesi
        self.plot_monthly_efficiency_comparison()
    
    def generate_yearly_comparison(self):
        """Genera grafici di confronto annuale"""
        print("🌍 Generando confronti annuali...")
        
        # Confronto anni (se disponibili)
        self.plot_yearly_comparison()
        
        # Trend stagionali
        self.plot_seasonal_trends()
        
        # Monitoraggio anomalie annuale
        self.plot_yearly_anomaly_monitoring()
    
    def plot_pv_daily_production(self):
        """Grafico produzione fotovoltaica giornaliera"""
        print("📊 Generando grafico produzione PV...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.photovoltaic.daily_summary.total_energy_kwh') as pv_energy,
                json_extract(analysis_data, '$.photovoltaic.daily_summary.peak_power_kw') as peak_power
            FROM daily_analysis 
            WHERE date >= DATE('now', '-30 days')
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato PV disponibile")
            return
            
        # Crea grafico a barre
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        # Grafico energia giornaliera
        bars1 = ax1.bar(df['date'], df['pv_energy'], color=self.colors['pv'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title('☀️ Produzione Fotovoltaica Giornaliera (Ultimi 30 giorni)', fontsize=16, fontweight='bold')
        ax1.set_xlabel('Data', fontsize=12)
        ax1.set_ylabel('Energia (kWh)', fontsize=12)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.1f}', ha='center', va='bottom', fontsize=8)
        
        # Grafico picco potenza
        bars2 = ax2.bar(df['date'], df['peak_power'], color=self.colors['efficiency'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title('⚡ Picco Potenza Giornaliero (kW)', fontsize=16, fontweight='bold')
        ax2.set_xlabel('Data', fontsize=12)
        ax2.set_ylabel('Potenza Picco (kW)', fontsize=12)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.05,
                    f'{height:.2f}', ha='center', va='bottom', fontsize=8)
        
        plt.tight_layout()
        plt.savefig(self.output_dir / 'pv_daily_production.png', dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Grafico produzione PV salvato")
    
    def plot_system_efficiency(self):
        """Grafico efficienza sistema"""
        print("📊 Generando grafico efficienza sistema...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.daily_summary.system_efficiency') as efficiency,
                json_extract(analysis_data, '$.daily_summary.daylight_efficiency') as daylight_eff
            FROM daily_analysis 
            WHERE date >= DATE('now', '-30 days')
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato efficienza disponibile")
            return
            
        fig, ax = plt.subplots(figsize=(14, 8))
        
        # Linea efficienza complessiva
        line1 = ax.plot(df['date'], df['efficiency'], marker='o', linewidth=3, 
                       color=self.colors['efficiency'], label='Efficienza Sistema', markersize=6)
        
        # Linea efficienza ore luce
        line2 = ax.plot(df['date'], df['daylight_eff'], marker='s', linewidth=3, 
                       color=self.colors['pv'], label='Efficienza Ore Luce', markersize=6)
        
        ax.set_title('⚡ Efficienza Sistema Complessiva (Ultimi 30 giorni)', fontsize=16, fontweight='bold')
        ax.set_xlabel('Data', fontsize=12)
        ax.set_ylabel('Efficienza (%)', fontsize=12)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=12)
        ax.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sui punti
        for i, (x, y1, y2) in enumerate(zip(df['date'], df['efficiency'], df['daylight_eff'])):
            ax.annotate(f'{y1:.1f}%', (x, y1), textcoords="offset points", 
                       xytext=(0,10), ha='center', fontsize=8, color=self.colors['efficiency'])
            ax.annotate(f'{y2:.1f}%', (x, y2), textcoords="offset points", 
                       xytext=(0,-15), ha='center', fontsize=8, color=self.colors['pv'])
        
        plt.tight_layout()
        plt.savefig(self.output_dir / 'system_efficiency.png', dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Grafico efficienza sistema salvato")
    
    def plot_household_consumption(self):
        """Grafico consumo casa"""
        print("📊 Generando grafico consumo casa...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.household.daily_summary.total_energy_kwh') as total_consumption,
                json_extract(analysis_data, '$.household.daily_summary.peak_load_kw') as peak_load,
                json_extract(analysis_data, '$.household.daily_summary.avg_load_kw') as avg_load
            FROM daily_analysis 
            WHERE date >= DATE('now', '-30 days')
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato consumo disponibile")
            return
            
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        # Grafico consumo totale
        bars1 = ax1.bar(df['date'], df['total_consumption'], color=self.colors['load'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title('🏠 Consumo Casa Giornaliero (Ultimi 30 giorni)', fontsize=16, fontweight='bold')
        ax1.set_xlabel('Data', fontsize=12)
        ax1.set_ylabel('Energia (kWh)', fontsize=12)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.1f}', ha='center', va='bottom', fontsize=8)
        
        # Grafico picco carico
        bars2 = ax2.bar(df['date'], df['peak_load'], color=self.colors['grid'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title('⚡ Picco Carico Giornaliero (kW)', fontsize=16, fontweight='bold')
        ax2.set_xlabel('Data', fontsize=12)
        ax2.set_ylabel('Potenza Picco (kW)', fontsize=12)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.2f}', ha='center', va='bottom', fontsize=8)
        
        plt.tight_layout()
        plt.savefig(self.output_dir / 'household_consumption.png', dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Grafico consumo casa salvato")
    
    def plot_battery_cycles(self):
        """Grafico cicli batteria"""
        print("📊 Generando grafico cicli batteria...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.battery.daily_summary.total_energy_kwh') as total_energy,
                json_extract(analysis_data, '$.battery.daily_summary.charging_energy_kwh') as charging,
                json_extract(analysis_data, '$.battery.daily_summary.avg_voltage') as avg_voltage
            FROM daily_analysis 
            WHERE date >= DATE('now', '-30 days')
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato batteria disponibile")
            return
            
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        # Grafico energia batteria
        x = range(len(df))
        width = 0.35
        
        bars1 = ax1.bar([i - width/2 for i in x], df['charging'], width, label='Carica', 
                        color=self.colors['battery'], alpha=0.8, edgecolor='white', linewidth=0.5)
        bars2 = ax1.bar([i + width/2 for i in x], df['total_energy'], width, label='Energia Netta', 
                        color=self.colors['efficiency'], alpha=0.8, edgecolor='white', linewidth=0.5)
        
        ax1.set_title('🔋 Cicli Batteria Giornalieri (Ultimi 30 giorni)', fontsize=16, fontweight='bold')
        ax1.set_xlabel('Data', fontsize=12)
        ax1.set_ylabel('Energia (kWh)', fontsize=12)
        ax1.set_xticks(x)
        ax1.set_xticklabels(df['date'], rotation=45)
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Grafico tensione media
        line = ax2.plot(df['date'], df['avg_voltage'], marker='o', linewidth=3, 
                       color=self.colors['battery'], markersize=6)
        ax2.set_title('⚡ Tensione Media Batteria (V)', fontsize=16, fontweight='bold')
        ax2.set_xlabel('Data', fontsize=12)
        ax2.set_ylabel('Tensione (V)', fontsize=12)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sui punti
        for i, (x, y) in enumerate(zip(df['date'], df['avg_voltage'])):
            ax2.annotate(f'{y:.1f}V', (x, y), textcoords="offset points", 
                        xytext=(0,10), ha='center', fontsize=8, color=self.colors['battery'])
        
        plt.tight_layout()
        plt.savefig(self.output_dir / 'battery_cycles.png', dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Grafico cicli batteria salvato")
    
    def plot_pv_hourly_patterns(self):
        """Grafico pattern orari PV"""
        print("📊 Generando grafico pattern orari PV...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date,
                json_extract(analysis_data, '$.photovoltaic.hourly_patterns.hourly_breakdown') as hourly_data
            FROM daily_analysis 
            WHERE date >= DATE('now', '-7 days')
            ORDER BY date DESC
            LIMIT 1
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato pattern orari disponibile")
            return
            
        # Parsing JSON per pattern orari
        hourly_data = json.loads(df.iloc[0]['hourly_data'])
        hours = list(hourly_data.keys())
        avg_powers = [hourly_data[hour]['avg_power_kw'] for hour in hours]
        
        fig, ax = plt.subplots(figsize=(14, 8))
        
        bars = ax.bar(hours, avg_powers, color=self.colors['pv'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax.set_title('☀️ Pattern Orari Produzione Fotovoltaica (Ultimi 7 giorni)', fontsize=16, fontweight='bold')
        ax.set_xlabel('Ora del Giorno', fontsize=12)
        ax.set_ylabel('Potenza Media (kW)', fontsize=12)
        ax.grid(True, alpha=0.3)
        
        # Aggiungi valori sulle barre
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height + 0.01,
                    f'{height:.2f}', ha='center', va='bottom', fontsize=8)
        
        plt.tight_layout()
        plt.savefig(self.output_dir / 'pv_hourly_patterns.png', dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Grafico pattern orari PV salvato")
    
    def plot_dark_vs_light_consumption(self):
        """Grafico consumo ore buie vs luce"""
        print("📊 Generando grafico consumo ore buie vs luce...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.household.daily_summary.dark_hours_percentage') as dark_percentage,
                json_extract(analysis_data, '$.household.daily_summary.total_energy_kwh') as total_consumption
            FROM daily_analysis 
            WHERE date >= DATE('now', '-30 days')
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato consumo ore buie disponibile")
            return
            
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        # Grafico percentuale ore buie
        bars1 = ax1.bar(df['date'], df['dark_percentage'], color=self.colors['grid'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title('🌙 Consumo Ore Buie vs Ore Luce (Ultimi 30 giorni)', fontsize=16, fontweight='bold')
        ax1.set_xlabel('Data', fontsize=12)
        ax1.set_ylabel('Percentuale Ore Buie (%)', fontsize=12)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.5,
                    f'{height:.1f}%', ha='center', va='bottom', fontsize=8)
        
        # Grafico consumo totale
        bars2 = ax2.bar(df['date'], df['total_consumption'], color=self.colors['load'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title('🏠 Consumo Totale Casa (kWh)', fontsize=16, fontweight='bold')
        ax2.set_xlabel('Data', fontsize=12)
        ax2.set_ylabel('Energia (kWh)', fontsize=12)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.1f}', ha='center', va='bottom', fontsize=8)
        
        plt.tight_layout()
        plt.savefig(self.output_dir / 'dark_vs_light_consumption.png', dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Grafico consumo ore buie vs luce salvato")
    
    def plot_seasonal_trends(self):
        """Grafico trend stagionali"""
        print("📊 Generando grafico trend stagionali...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.environmental.seasonal_insights.daylight_hours') as daylight_hours,
                json_extract(analysis_data, '$.photovoltaic.daily_summary.total_energy_kwh') as pv_energy
            FROM daily_analysis 
            WHERE date >= DATE('now', '-90 days')
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato stagionale disponibile")
            return
            
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        # Grafico ore di luce
        line1 = ax1.plot(df['date'], df['daylight_hours'], marker='o', linewidth=3, 
                         color=self.colors['efficiency'], markersize=6)
        ax1.set_title('🌍 Trend Stagionale Ore di Luce (Ultimi 90 giorni)', fontsize=16, fontweight='bold')
        ax1.set_xlabel('Data', fontsize=12)
        ax1.set_ylabel('Ore di Luce', fontsize=12)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sui punti
        for i, (x, y) in enumerate(zip(df['date'], df['daylight_hours'])):
            ax1.annotate(f'{y:.1f}h', (x, y), textcoords="offset points", 
                        xytext=(0,10), ha='center', fontsize=8, color=self.colors['efficiency'])
        
        # Grafico produzione PV correlata
        line2 = ax2.plot(df['date'], df['pv_energy'], marker='s', linewidth=3, 
                         color=self.colors['pv'], markersize=6)
        ax2.set_title('☀️ Produzione PV Correlata (kWh)', fontsize=16, fontweight='bold')
        ax2.set_xlabel('Data', fontsize=12)
        ax2.set_ylabel('Energia (kWh)', fontsize=12)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sui punti
        for i, (x, y) in enumerate(zip(df['date'], df['pv_energy'])):
            ax2.annotate(f'{y:.1f}', (x, y), textcoords="offset points", 
                        xytext=(0,10), ha='center', fontsize=8, color=self.colors['pv'])
        
        plt.tight_layout()
        plt.savefig(self.output_dir / 'seasonal_trends.png', dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Grafico trend stagionali salvato")
    
    def plot_anomaly_monitoring(self):
        """Grafico monitoraggio anomalie"""
        print("📊 Generando grafico monitoraggio anomalie...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.monitoring.anomaly_detection.total_anomalies') as total_anomalies,
                json_extract(analysis_data, '$.monitoring.anomaly_detection.high_severity') as high_severity,
                json_extract(analysis_data, '$.monitoring.anomaly_detection.medium_severity') as medium_severity
            FROM daily_analysis 
            WHERE date >= DATE('now', '-90 days')
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato anomalie disponibile")
            return
            
        fig, ax = plt.subplots(figsize=(14, 8))
        
        x = range(len(df))
        width = 0.25
        
        bars1 = ax.bar([i - width for i in x], df['total_anomalies'], width, label='Anomalie Totali', 
                       color='#ef4444', alpha=0.8, edgecolor='white', linewidth=0.5)
        bars2 = ax.bar(x, df['high_severity'], width, label='Alta Severità', 
                       color='#dc2626', alpha=0.8, edgecolor='white', linewidth=0.5)
        bars3 = ax.bar([i + width for i in x], df['medium_severity'], width, label='Media Severità', 
                       color='#f59e0b', alpha=0.8, edgecolor='white', linewidth=0.5)
        
        ax.set_title('🚨 Monitoraggio Anomalie Sistema (Ultimi 90 giorni)', fontsize=16, fontweight='bold')
        ax.set_xlabel('Data', fontsize=12)
        ax.set_ylabel('Numero Anomalie', fontsize=12)
        ax.set_xticks(x)
        ax.set_xticklabels(df['date'], rotation=45)
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig(self.output_dir / 'anomaly_monitoring.png', dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Grafico monitoraggio anomalie salvato")

    def plot_monthly_pv_production(self, month, month_name):
        """Grafico produzione PV mensile"""
        print(f"📊 Generando grafico PV per {month_name}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.photovoltaic.daily_summary.total_energy_kwh') as pv_energy,
                json_extract(analysis_data, '$.photovoltaic.daily_summary.peak_power_kw') as peak_power
            FROM daily_analysis 
            WHERE strftime('%Y-%m', date) = ?
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn, params=(month,))
        
        if df.empty:
            print(f"⚠️ Nessun dato PV disponibile per {month_name}")
            return
            
        # Crea grafico a barre
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        # Grafico energia giornaliera
        bars1 = ax1.bar(df['date'], df['pv_energy'], color=self.colors['pv'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title(f'☀️ Produzione Fotovoltaica {month_name} {month[:4]}', fontsize=16, fontweight='bold')
        ax1.set_xlabel('Data', fontsize=12)
        ax1.set_ylabel('Energia (kWh)', fontsize=12)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.1f}', ha='center', va='bottom', fontsize=8)
        
        # Grafico picco potenza
        bars2 = ax2.bar(df['date'], df['peak_power'], color=self.colors['efficiency'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title(f'⚡ Picco Potenza {month_name} {month[:4]} (kW)', fontsize=16, fontweight='bold')
        ax2.set_xlabel('Data', fontsize=12)
        ax2.set_ylabel('Potenza Picco (kW)', fontsize=12)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.05,
                    f'{height:.2f}', ha='center', va='bottom', fontsize=8)
        
        plt.tight_layout()
        filename = f'pv_production_{month}.png'
        plt.savefig(self.monthly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Grafico PV {month_name} salvato")
    
    def plot_monthly_household_consumption(self, month, month_name):
        """Grafico consumo casa mensile"""
        print(f"📊 Generando grafico consumo per {month_name}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.household.daily_summary.total_energy_kwh') as total_consumption,
                json_extract(analysis_data, '$.household.daily_summary.peak_load_kw') as peak_load
            FROM daily_analysis 
            WHERE strftime('%Y-%m', date) = ?
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn, params=(month,))
        
        if df.empty:
            print(f"⚠️ Nessun dato consumo disponibile per {month_name}")
            return
            
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        # Grafico consumo totale
        bars1 = ax1.bar(df['date'], df['total_consumption'], color=self.colors['load'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title(f'🏠 Consumo Casa {month_name} {month[:4]}', fontsize=16, fontweight='bold')
        ax1.set_xlabel('Data', fontsize=12)
        ax1.set_ylabel('Energia (kWh)', fontsize=12)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.1f}', ha='center', va='bottom', fontsize=8)
        
        # Grafico picco carico
        bars2 = ax2.bar(df['date'], df['peak_load'], color=self.colors['grid'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title(f'⚡ Picco Carico {month_name} {month[:4]} (kW)', fontsize=16, fontweight='bold')
        ax2.set_xlabel('Data', fontsize=12)
        ax2.set_ylabel('Potenza Picco (kW)', fontsize=12)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.2f}', ha='center', va='bottom', fontsize=8)
        
        plt.tight_layout()
        filename = f'household_consumption_{month}.png'
        plt.savefig(self.monthly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Grafico consumo {month_name} salvato")
    
    def plot_monthly_system_efficiency(self, month, month_name):
        """Grafico efficienza sistema mensile"""
        print(f"📊 Generando grafico efficienza per {month_name}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.daily_summary.system_efficiency') as efficiency,
                json_extract(analysis_data, '$.daily_summary.daylight_efficiency') as daylight_eff
            FROM daily_analysis 
            WHERE strftime('%Y-%m', date) = ?
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn, params=(month,))
        
        if df.empty:
            print(f"⚠️ Nessun dato efficienza disponibile per {month_name}")
            return
            
        fig, ax = plt.subplots(figsize=(14, 8))
        
        # Linea efficienza complessiva
        line1 = ax.plot(df['date'], df['efficiency'], marker='o', linewidth=3, 
                       color=self.colors['efficiency'], label='Efficienza Sistema', markersize=6)
        
        # Linea efficienza ore luce
        line2 = ax.plot(df['date'], df['daylight_eff'], marker='s', linewidth=3, 
                       color=self.colors['pv'], label='Efficienza Ore Luce', markersize=6)
        
        ax.set_title(f'⚡ Efficienza Sistema {month_name} {month[:4]}', fontsize=16, fontweight='bold')
        ax.set_xlabel('Data', fontsize=12)
        ax.set_ylabel('Efficienza (%)', fontsize=12)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=12)
        ax.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sui punti
        for i, (x, y1, y2) in enumerate(zip(df['date'], df['efficiency'], df['daylight_eff'])):
            ax.annotate(f'{y1:.1f}%', (x, y1), textcoords="offset points", 
                       xytext=(0,10), ha='center', fontsize=8, color=self.colors['efficiency'])
            ax.annotate(f'{y2:.1f}%', (x, y2), textcoords="offset points", 
                       xytext=(0,-15), ha='center', fontsize=8, color=self.colors['pv'])
        
        plt.tight_layout()
        filename = f'system_efficiency_{month}.png'
        plt.savefig(self.monthly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Grafico efficienza {month_name} salvato")
    
    def plot_monthly_battery_cycles(self, month, month_name):
        """Grafico cicli batteria mensili"""
        print(f"📊 Generando grafico batteria per {month_name}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                date, 
                json_extract(analysis_data, '$.battery.daily_summary.total_energy_kwh') as total_energy,
                json_extract(analysis_data, '$.battery.daily_summary.charging_energy_kwh') as charging,
                json_extract(analysis_data, '$.battery.daily_summary.avg_voltage') as avg_voltage
            FROM daily_analysis 
            WHERE strftime('%Y-%m', date) = ?
            ORDER BY date
            """
            df = pd.read_sql_query(query, conn, params=(month,))
        
        if df.empty:
            print(f"⚠️ Nessun dato batteria disponibile per {month_name}")
            return
            
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
        
        # Grafico energia batteria
        x = range(len(df))
        width = 0.35
        
        bars1 = ax1.bar([i - width/2 for i in x], df['charging'], width, label='Carica', 
                        color=self.colors['battery'], alpha=0.8, edgecolor='white', linewidth=0.5)
        bars2 = ax1.bar([i + width/2 for i in x], df['total_energy'], width, label='Energia Netta', 
                        color=self.colors['efficiency'], alpha=0.8, edgecolor='white', linewidth=0.5)
        
        ax1.set_title(f'🔋 Cicli Batteria {month_name} {month[:4]}', fontsize=16, fontweight='bold')
        ax1.set_xlabel('Data', fontsize=12)
        ax1.set_ylabel('Energia (kWh)', fontsize=12)
        ax1.set_xticks(x)
        ax1.set_xticklabels(df['date'], rotation=45)
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Grafico tensione media
        line = ax2.plot(df['date'], df['avg_voltage'], marker='o', linewidth=3, 
                       color=self.colors['battery'], markersize=6)
        ax2.set_title(f'⚡ Tensione Media Batteria {month_name} {month[:4]} (V)', fontsize=16, fontweight='bold')
        ax2.set_xlabel('Data', fontsize=12)
        ax2.set_ylabel('Tensione (V)', fontsize=12)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sui punti
        for i, (x, y) in enumerate(zip(df['date'], df['avg_voltage'])):
            ax2.annotate(f'{y:.1f}V', (x, y), textcoords="offset points", 
                        xytext=(0,10), ha='center', fontsize=8, color=self.colors['battery'])
        
        plt.tight_layout()
        filename = f'battery_cycles_{month}.png'
        plt.savefig(self.monthly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Grafico batteria {month_name} salvato")

    def plot_yearly_pv_production(self, year):
        """Grafico produzione PV annuale"""
        print(f"📊 Generando grafico PV annuale {year}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%m', date) as month,
                AVG(json_extract(analysis_data, '$.photovoltaic.daily_summary.total_energy_kwh')) as avg_pv_energy,
                SUM(json_extract(analysis_data, '$.photovoltaic.daily_summary.total_energy_kwh')) as total_pv_energy,
                MAX(json_extract(analysis_data, '$.photovoltaic.daily_summary.peak_power_kw')) as max_peak_power
            FROM daily_analysis 
            WHERE strftime('%Y', date) = ?
            GROUP BY strftime('%m', date)
            ORDER BY month
            """
            df = pd.read_sql_query(query, conn, params=(str(year),))
        
        if df.empty:
            print(f"⚠️ Nessun dato PV disponibile per l'anno {year}")
            return
            
        # Converti mesi in nomi
        df['month_name'] = df['month'].map(self.months)
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        
        # Grafico energia media mensile
        bars1 = ax1.bar(df['month_name'], df['avg_pv_energy'], color=self.colors['pv'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title(f'☀️ Produzione Fotovoltaica Media Mensile {year}', fontsize=18, fontweight='bold')
        ax1.set_xlabel('Mese', fontsize=14)
        ax1.set_ylabel('Energia Media (kWh)', fontsize=14)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.1f}', ha='center', va='bottom', fontsize=10)
        
        # Grafico energia totale mensile
        bars2 = ax2.bar(df['month_name'], df['total_pv_energy'], color=self.colors['efficiency'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title(f'⚡ Energia Totale Mensile {year} (kWh)', fontsize=18, fontweight='bold')
        ax2.set_xlabel('Mese', fontsize=14)
        ax2.set_ylabel('Energia Totale (kWh)', fontsize=14)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.0f}', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        filename = f'pv_production_yearly_{year}.png'
        plt.savefig(self.yearly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Grafico PV annuale {year} salvato")
    
    def plot_yearly_household_consumption(self, year):
        """Grafico consumo casa annuale"""
        print(f"📊 Generando grafico consumo annuale {year}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%m', date) as month,
                AVG(json_extract(analysis_data, '$.household.daily_summary.total_energy_kwh')) as avg_consumption,
                SUM(json_extract(analysis_data, '$.household.daily_summary.total_energy_kwh')) as total_consumption,
                MAX(json_extract(analysis_data, '$.household.daily_summary.peak_load_kw')) as max_peak_load
            FROM daily_analysis 
            WHERE strftime('%Y', date) = ?
            GROUP BY strftime('%m', date)
            ORDER BY month
            """
            df = pd.read_sql_query(query, conn, params=(str(year),))
        
        if df.empty:
            print(f"⚠️ Nessun dato consumo disponibile per l'anno {year}")
            return
            
        # Converti mesi in nomi
        df['month_name'] = df['month'].map(self.months)
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        
        # Grafico consumo medio mensile
        bars1 = ax1.bar(df['month_name'], df['avg_consumption'], color=self.colors['load'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title(f'🏠 Consumo Casa Medio Mensile {year}', fontsize=18, fontweight='bold')
        ax1.set_xlabel('Mese', fontsize=14)
        ax1.set_ylabel('Consumo Medio (kWh)', fontsize=14)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.1f}', ha='center', va='bottom', fontsize=10)
        
        # Grafico consumo totale mensile
        bars2 = ax2.bar(df['month_name'], df['total_consumption'], color=self.colors['grid'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title(f'⚡ Consumo Totale Mensile {year} (kWh)', fontsize=18, fontweight='bold')
        ax2.set_xlabel('Mese', fontsize=14)
        ax2.set_ylabel('Consumo Totale (kWh)', fontsize=14)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.0f}', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        filename = f'household_consumption_yearly_{year}.png'
        plt.savefig(self.yearly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Grafico consumo annuale {year} salvato")
    
    def plot_yearly_system_efficiency(self, year):
        """Grafico efficienza sistema annuale"""
        print(f"📊 Generando grafico efficienza annuale {year}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%m', date) as month,
                AVG(json_extract(analysis_data, '$.daily_summary.system_efficiency')) as avg_efficiency,
                AVG(json_extract(analysis_data, '$.daily_summary.daylight_efficiency')) as avg_daylight_eff
            FROM daily_analysis 
            WHERE strftime('%Y', date) = ?
            GROUP BY strftime('%m', date)
            ORDER BY month
            """
            df = pd.read_sql_query(query, conn, params=(str(year),))
        
        if df.empty:
            print(f"⚠️ Nessun dato efficienza disponibile per l'anno {year}")
            return
            
        # Converti mesi in nomi
        df['month_name'] = df['month'].map(self.months)
        
        fig, ax = plt.subplots(figsize=(16, 10))
        
        # Linea efficienza complessiva
        line1 = ax.plot(df['month_name'], df['avg_efficiency'], marker='o', linewidth=4, 
                       color=self.colors['efficiency'], label='Efficienza Sistema Media', markersize=8)
        
        # Linea efficienza ore luce
        line2 = ax.plot(df['month_name'], df['avg_daylight_eff'], marker='s', linewidth=4, 
                       color=self.colors['pv'], label='Efficienza Ore Luce Media', markersize=8)
        
        ax.set_title(f'⚡ Efficienza Sistema Annuale {year}', fontsize=18, fontweight='bold')
        ax.set_xlabel('Mese', fontsize=14)
        ax.set_ylabel('Efficienza (%)', fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=12)
        ax.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sui punti
        for i, (x, y1, y2) in enumerate(zip(df['month_name'], df['avg_efficiency'], df['avg_daylight_eff'])):
            ax.annotate(f'{y1:.1f}%', (x, y1), textcoords="offset points", 
                       xytext=(0,15), ha='center', fontsize=10, color=self.colors['efficiency'])
            ax.annotate(f'{y2:.1f}%', (x, y2), textcoords="offset points", 
                       xytext=(0,-20), ha='center', fontsize=10, color=self.colors['pv'])
        
        plt.tight_layout()
        filename = f'system_efficiency_yearly_{year}.png'
        plt.savefig(self.yearly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Grafico efficienza annuale {year} salvato")
    
    def plot_yearly_monthly_comparison(self, year):
        """Grafico confronto mensile annuale"""
        print(f"📊 Generando confronto mensile annuale {year}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%m', date) as month,
                SUM(json_extract(analysis_data, '$.photovoltaic.daily_summary.total_energy_kwh')) as pv_total,
                SUM(json_extract(analysis_data, '$.household.daily_summary.total_energy_kwh')) as consumption_total,
                AVG(json_extract(analysis_data, '$.daily_summary.system_efficiency')) as avg_efficiency
            FROM daily_analysis 
            WHERE strftime('%Y', date) = ?
            GROUP BY strftime('%m', date)
            ORDER BY month
            """
            df = pd.read_sql_query(query, conn, params=(str(year),))
        
        if df.empty:
            print(f"⚠️ Nessun dato disponibile per l'anno {year}")
            return
            
        # Converti mesi in nomi
        df['month_name'] = df['month'].map(self.months)
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        
        # Grafico produzione vs consumo
        x = range(len(df))
        width = 0.35
        
        bars1 = ax1.bar([i - width/2 for i in x], df['pv_total'], width, label='Produzione PV', 
                        color=self.colors['pv'], alpha=0.8, edgecolor='white', linewidth=0.5)
        bars2 = ax1.bar([i + width/2 for i in x], df['consumption_total'], width, label='Consumo Casa', 
                        color=self.colors['load'], alpha=0.8, edgecolor='white', linewidth=0.5)
        
        ax1.set_title(f'☀️ Produzione vs Consumo Mensile {year}', fontsize=18, fontweight='bold')
        ax1.set_xlabel('Mese', fontsize=14)
        ax1.set_ylabel('Energia (kWh)', fontsize=14)
        ax1.set_xticks(x)
        ax1.set_xticklabels(df['month_name'], rotation=45)
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Grafico efficienza mensile
        bars3 = ax2.bar(df['month_name'], df['avg_efficiency'], color=self.colors['efficiency'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title(f'⚡ Efficienza Media Mensile {year}', fontsize=18, fontweight='bold')
        ax2.set_xlabel('Mese', fontsize=14)
        ax2.set_ylabel('Efficienza (%)', fontsize=14)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars3:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.5,
                    f'{height:.1f}%', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        filename = f'monthly_comparison_yearly_{year}.png'
        plt.savefig(self.yearly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Confronto mensile annuale {year} salvato")

    def plot_monthly_pv_comparison(self):
        """Grafico confronto produzione PV ultimi 6 mesi"""
        print("📊 Generando confronto PV ultimi 6 mesi...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%Y-%m', date) as month,
                SUM(json_extract(analysis_data, '$.photovoltaic.daily_summary.total_energy_kwh')) as total_pv_energy,
                AVG(json_extract(analysis_data, '$.photovoltaic.daily_summary.peak_power_kw')) as avg_peak_power
            FROM daily_analysis 
            WHERE date >= DATE('now', '-6 months')
            GROUP BY strftime('%Y-%m', date)
            ORDER BY month
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato PV disponibile per confronto mensile")
            return
            
        # Converti mesi in nomi
        df['month_name'] = df['month'].apply(lambda x: f"{self.months[x[-2:]]} {x[:4]}")
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        
        # Grafico energia totale mensile
        bars1 = ax1.bar(df['month_name'], df['total_pv_energy'], color=self.colors['pv'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title('☀️ Produzione PV Totale Ultimi 6 Mesi', fontsize=18, fontweight='bold')
        ax1.set_xlabel('Mese', fontsize=14)
        ax1.set_ylabel('Energia Totale (kWh)', fontsize=14)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.0f}', ha='center', va='bottom', fontsize=10)
        
        # Grafico picco potenza medio mensile
        bars2 = ax2.bar(df['month_name'], df['avg_peak_power'], color=self.colors['efficiency'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title('⚡ Picco Potenza Medio Ultimi 6 Mesi (kW)', fontsize=18, fontweight='bold')
        ax2.set_xlabel('Mese', fontsize=14)
        ax2.set_ylabel('Potenza Media (kW)', fontsize=14)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.01,
                    f'{height:.2f}', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        filename = 'monthly_pv_comparison.png'
        plt.savefig(self.output_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Confronto PV mensile salvato")
    
    def plot_monthly_consumption_comparison(self):
        """Grafico confronto consumo ultimi 6 mesi"""
        print("📊 Generando confronto consumo ultimi 6 mesi...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%Y-%m', date) as month,
                SUM(json_extract(analysis_data, '$.household.daily_summary.total_energy_kwh')) as total_consumption,
                AVG(json_extract(analysis_data, '$.household.daily_summary.peak_load_kw')) as avg_peak_load
            FROM daily_analysis 
            WHERE date >= DATE('now', '-6 months')
            GROUP BY strftime('%Y-%m', date)
            ORDER BY month
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato consumo disponibile per confronto mensile")
            return
            
        # Converti mesi in nomi
        df['month_name'] = df['month'].apply(lambda x: f"{self.months[x[-2:]]} {x[:4]}")
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        
        # Grafico consumo totale mensile
        bars1 = ax1.bar(df['month_name'], df['total_consumption'], color=self.colors['load'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title('🏠 Consumo Casa Totale Ultimi 6 Mesi', fontsize=18, fontweight='bold')
        ax1.set_xlabel('Mese', fontsize=14)
        ax1.set_ylabel('Consumo Totale (kWh)', fontsize=14)
        ax1.grid(True, alpha=0.3)
        ax1.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.0f}', ha='center', va='bottom', fontsize=10)
        
        # Grafico picco carico medio mensile
        bars2 = ax2.bar(df['month_name'], df['avg_peak_load'], color=self.colors['grid'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title('⚡ Picco Carico Medio Ultimi 6 Mesi (kW)', fontsize=18, fontweight='bold')
        ax2.set_xlabel('Mese', fontsize=14)
        ax2.set_ylabel('Potenza Media (kW)', fontsize=14)
        ax2.grid(True, alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.01,
                    f'{height:.2f}', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        filename = 'monthly_consumption_comparison.png'
        plt.savefig(self.output_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Confronto consumo mensile salvato")
    
    def plot_monthly_efficiency_comparison(self):
        """Grafico confronto efficienza ultimi 6 mesi"""
        print("📊 Generando confronto efficienza ultimi 6 mesi...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%Y-%m', date) as month,
                AVG(json_extract(analysis_data, '$.daily_summary.system_efficiency')) as avg_efficiency,
                AVG(json_extract(analysis_data, '$.daily_summary.daylight_efficiency')) as avg_daylight_eff
            FROM daily_analysis 
            WHERE date >= DATE('now', '-6 months')
            GROUP BY strftime('%Y-%m', date)
            ORDER BY month
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato efficienza disponibile per confronto mensile")
            return
            
        # Converti mesi in nomi
        df['month_name'] = df['month'].apply(lambda x: f"{self.months[x[-2:]]} {x[:4]}")
        
        fig, ax = plt.subplots(figsize=(16, 10))
        
        # Linea efficienza complessiva
        line1 = ax.plot(df['month_name'], df['avg_efficiency'], marker='o', linewidth=4, 
                       color=self.colors['efficiency'], label='Efficienza Sistema Media', markersize=8)
        
        # Linea efficienza ore luce
        line2 = ax.plot(df['month_name'], df['avg_daylight_eff'], marker='s', linewidth=4, 
                       color=self.colors['pv'], label='Efficienza Ore Luce Media', markersize=8)
        
        ax.set_title('⚡ Confronto Efficienza Ultimi 6 Mesi', fontsize=18, fontweight='bold')
        ax.set_xlabel('Mese', fontsize=14)
        ax.set_ylabel('Efficienza (%)', fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=12)
        ax.tick_params(axis='x', rotation=45)
        
        # Aggiungi valori sui punti
        for i, (x, y1, y2) in enumerate(zip(df['month_name'], df['avg_efficiency'], df['avg_daylight_eff'])):
            ax.annotate(f'{y1:.1f}%', (x, y1), textcoords="offset points", 
                       xytext=(0,15), ha='center', fontsize=10, color=self.colors['efficiency'])
            ax.annotate(f'{y2:.1f}%', (x, y2), textcoords="offset points", 
                       xytext=(0,-20), ha='center', fontsize=10, color=self.colors['pv'])
        
        plt.tight_layout()
        filename = 'monthly_efficiency_comparison.png'
        plt.savefig(self.output_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Confronto efficienza mensile salvato")
    
    def plot_yearly_comparison(self):
        """Grafico confronto annuale (se disponibili più anni)"""
        print("🌍 Generando confronto annuale...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%Y', date) as year,
                SUM(json_extract(analysis_data, '$.photovoltaic.daily_summary.total_energy_kwh')) as total_pv_energy,
                SUM(json_extract(analysis_data, '$.household.daily_summary.total_energy_kwh')) as total_consumption,
                AVG(json_extract(analysis_data, '$.daily_summary.system_efficiency')) as avg_efficiency
            FROM daily_analysis 
            GROUP BY strftime('%Y', date)
            ORDER BY year
            """
            df = pd.read_sql_query(query, conn)
        
        if len(df) < 2:
            print("⚠️ Dati insufficienti per confronto annuale (servono almeno 2 anni)")
            return
            
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        
        # Grafico produzione vs consumo annuale
        x = range(len(df))
        width = 0.35
        
        bars1 = ax1.bar([i - width/2 for i in x], df['total_pv_energy'], width, label='Produzione PV', 
                        color=self.colors['pv'], alpha=0.8, edgecolor='white', linewidth=0.5)
        bars2 = ax1.bar([i + width/2 for i in x], df['total_consumption'], width, label='Consumo Casa', 
                        color=self.colors['load'], alpha=0.8, edgecolor='white', linewidth=0.5)
        
        ax1.set_title('☀️ Produzione vs Consumo Annuale', fontsize=18, fontweight='bold')
        ax1.set_xlabel('Anno', fontsize=14)
        ax1.set_ylabel('Energia (kWh)', fontsize=14)
        ax1.set_xticks(x)
        ax1.set_xticklabels(df['year'])
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Grafico efficienza annuale
        bars3 = ax2.bar(df['year'], df['avg_efficiency'], color=self.colors['efficiency'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title('⚡ Efficienza Media Annuale', fontsize=18, fontweight='bold')
        ax2.set_xlabel('Anno', fontsize=14)
        ax2.set_ylabel('Efficienza (%)', fontsize=14)
        ax2.grid(True, alpha=0.3)
        
        # Aggiungi valori sulle barre
        for bar in bars3:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.5,
                    f'{height:.1f}%', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        filename = 'yearly_comparison.png'
        plt.savefig(self.output_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Confronto annuale salvato")
    
    def plot_yearly_anomaly_monitoring(self):
        """Grafico monitoraggio anomalie annuale"""
        print("🚨 Generando monitoraggio anomalie annuale...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%Y-%m', date) as month,
                SUM(json_extract(analysis_data, '$.monitoring.anomaly_detection.total_anomalies')) as total_anomalies,
                SUM(json_extract(analysis_data, '$.monitoring.anomaly_detection.high_severity')) as high_severity,
                SUM(json_extract(analysis_data, '$.monitoring.anomaly_detection.medium_severity')) as medium_severity
            FROM daily_analysis 
            WHERE date >= DATE('now', '-12 months')
            GROUP BY strftime('%Y-%m', date)
            ORDER BY month
            """
            df = pd.read_sql_query(query, conn)
        
        if df.empty:
            print("⚠️ Nessun dato anomalie disponibile per monitoraggio annuale")
            return
            
        # Converti mesi in nomi
        df['month_name'] = df['month'].apply(lambda x: f"{self.months[x[-2:]]} {x[:4]}")
        
        fig, ax = plt.subplots(figsize=(16, 10))
        
        x = range(len(df))
        width = 0.25
        
        bars1 = ax.bar([i - width for i in x], df['total_anomalies'], width, label='Anomalie Totali', 
                       color='#ef4444', alpha=0.8, edgecolor='white', linewidth=0.5)
        bars2 = ax.bar(x, df['high_severity'], width, label='Alta Severità', 
                       color='#dc2626', alpha=0.8, edgecolor='white', linewidth=0.5)
        bars3 = ax.bar([i + width for i in x], df['medium_severity'], width, label='Media Severità', 
                       color='#f59e0b', alpha=0.8, edgecolor='white', linewidth=0.5)
        
        ax.set_title('🚨 Monitoraggio Anomalie Annuale', fontsize=18, fontweight='bold')
        ax.set_xlabel('Mese', fontsize=14)
        ax.set_ylabel('Numero Anomalie', fontsize=14)
        ax.set_xticks(x)
        ax.set_xticklabels(df['month_name'], rotation=45)
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        filename = 'yearly_anomaly_monitoring.png'
        plt.savefig(self.output_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print("✅ Monitoraggio anomalie annuale salvato")

    def plot_monthly_daily_pv_comparison(self, month, month_name):
        """Grafico confronto giornaliero PV per mese specifico"""
        print(f"📊 Generando confronto giornaliero PV per {month_name}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%d', date) as day,
                json_extract(analysis_data, '$.photovoltaic.daily_summary.total_energy_kwh') as pv_energy,
                json_extract(analysis_data, '$.photovoltaic.daily_summary.peak_power_kw') as peak_power,
                json_extract(analysis_data, '$.photovoltaic.daily_summary.avg_power_kw') as avg_power
            FROM daily_analysis 
            WHERE strftime('%Y-%m', date) = ?
            ORDER BY day
            """
            df = pd.read_sql_query(query, conn, params=(month,))
        
        if df.empty:
            print(f"⚠️ Nessun dato PV disponibile per {month_name}")
            return
            
        # Converti giorni in numeri
        df['day'] = df['day'].astype(int)
        df = df.sort_values('day')
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        
        # Grafico energia giornaliera
        bars1 = ax1.bar(df['day'], df['pv_energy'], color=self.colors['pv'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title(f'☀️ Produzione PV Giornaliera {month_name} {month[:4]}', fontsize=18, fontweight='bold')
        ax1.set_xlabel('Giorno del Mese', fontsize=14)
        ax1.set_ylabel('Energia (kWh)', fontsize=14)
        ax1.grid(True, alpha=0.3)
        ax1.set_xticks(df['day'])
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.1f}', ha='center', va='bottom', fontsize=10)
        
        # Grafico potenza picco e media
        bars2 = ax2.bar(df['day'], df['peak_power'], color=self.colors['efficiency'], alpha=0.8, edgecolor='white', linewidth=0.5, label='Picco Potenza')
        line = ax2.plot(df['day'], df['avg_power'], marker='o', linewidth=3, color=self.colors['battery'], label='Potenza Media', markersize=6)
        ax2.set_title(f'⚡ Potenza PV Giornaliera {month_name} {month[:4]} (kW)', fontsize=18, fontweight='bold')
        ax2.set_xlabel('Giorno del Mese', fontsize=14)
        ax2.set_ylabel('Potenza (kW)', fontsize=14)
        ax2.grid(True, alpha=0.3)
        ax2.set_xticks(df['day'])
        ax2.legend()
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.01,
                    f'{height:.2f}', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        filename = f'daily_pv_comparison_{month}.png'
        plt.savefig(self.monthly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Confronto giornaliero PV {month_name} salvato")
    
    def plot_monthly_daily_consumption_comparison(self, month, month_name):
        """Grafico confronto giornaliero consumo per mese specifico"""
        print(f"📊 Generando confronto giornaliero consumo per {month_name}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%d', date) as day,
                json_extract(analysis_data, '$.household.daily_summary.total_energy_kwh') as total_consumption,
                json_extract(analysis_data, '$.household.daily_summary.peak_load_kw') as peak_load,
                json_extract(analysis_data, '$.household.daily_summary.avg_load_kw') as avg_load
            FROM daily_analysis 
            WHERE strftime('%Y-%m', date) = ?
            ORDER BY day
            """
            df = pd.read_sql_query(query, conn, params=(month,))
        
        if df.empty:
            print(f"⚠️ Nessun dato consumo disponibile per {month_name}")
            return
            
        # Converti giorni in numeri
        df['day'] = df['day'].astype(int)
        df = df.sort_values('day')
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        
        # Grafico consumo totale giornaliero
        bars1 = ax1.bar(df['day'], df['total_consumption'], color=self.colors['load'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax1.set_title(f'🏠 Consumo Casa Giornaliero {month_name} {month[:4]}', fontsize=18, fontweight='bold')
        ax1.set_xlabel('Giorno del Mese', fontsize=14)
        ax1.set_ylabel('Energia (kWh)', fontsize=14)
        ax1.grid(True, alpha=0.3)
        ax1.set_xticks(df['day'])
        
        # Aggiungi valori sulle barre
        for bar in bars1:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.1,
                    f'{height:.1f}', ha='center', va='bottom', fontsize=10)
        
        # Grafico carico picco e medio
        bars2 = ax2.bar(df['day'], df['peak_load'], color=self.colors['grid'], alpha=0.8, edgecolor='white', linewidth=0.5, label='Picco Carico')
        line = ax2.plot(df['day'], df['avg_load'], marker='o', linewidth=3, color=self.colors['efficiency'], label='Carico Medio', markersize=6)
        ax2.set_title(f'⚡ Carico Casa Giornaliero {month_name} {month[:4]} (kW)', fontsize=18, fontweight='bold')
        ax2.set_xlabel('Giorno del Mese', fontsize=14)
        ax2.set_ylabel('Potenza (kW)', fontsize=14)
        ax2.grid(True, alpha=0.3)
        ax2.set_xticks(df['day'])
        ax2.legend()
        
        # Aggiungi valori sulle barre
        for bar in bars2:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.01,
                    f'{height:.2f}', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        filename = f'daily_consumption_comparison_{month}.png'
        plt.savefig(self.monthly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Confronto giornaliero consumo {month_name} salvato")
    
    def plot_monthly_daily_efficiency_comparison(self, month, month_name):
        """Grafico confronto giornaliero efficienza per mese specifico"""
        print(f"📊 Generando confronto giornaliero efficienza per {month_name}...")
        
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                strftime('%d', date) as day,
                json_extract(analysis_data, '$.daily_summary.system_efficiency') as efficiency,
                json_extract(analysis_data, '$.daily_summary.daylight_efficiency') as daylight_eff,
                json_extract(analysis_data, '$.daily_summary.self_consumption_rate') as self_consumption
            FROM daily_analysis 
            WHERE strftime('%Y-%m', date) = ?
            ORDER BY day
            """
            df = pd.read_sql_query(query, conn, params=(month,))
        
        if df.empty:
            print(f"⚠️ Nessun dato efficienza disponibile per {month_name}")
            return
            
        # Converti giorni in numeri
        df['day'] = df['day'].astype(int)
        df = df.sort_values('day')
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12))
        
        # Grafico efficienza sistema
        line1 = ax1.plot(df['day'], df['efficiency'], marker='o', linewidth=3, 
                         color=self.colors['efficiency'], label='Efficienza Sistema', markersize=6)
        line2 = ax1.plot(df['day'], df['daylight_eff'], marker='s', linewidth=3, 
                         color=self.colors['pv'], label='Efficienza Ore Luce', markersize=6)
        ax1.set_title(f'⚡ Efficienza Sistema Giornaliera {month_name} {month[:4]}', fontsize=18, fontweight='bold')
        ax1.set_xlabel('Giorno del Mese', fontsize=14)
        ax1.set_ylabel('Efficienza (%)', fontsize=14)
        ax1.grid(True, alpha=0.3)
        ax1.set_xticks(df['day'])
        ax1.legend()
        
        # Aggiungi valori sui punti
        for i, (x, y1, y2) in enumerate(zip(df['day'], df['efficiency'], df['daylight_eff'])):
            ax1.annotate(f'{y1:.1f}%', (x, y1), textcoords="offset points", 
                        xytext=(0,10), ha='center', fontsize=8, color=self.colors['efficiency'])
            ax1.annotate(f'{y2:.1f}%', (x, y2), textcoords="offset points", 
                        xytext=(0,-15), ha='center', fontsize=8, color=self.colors['pv'])
        
        # Grafico tasso autoconsumo
        bars = ax2.bar(df['day'], df['self_consumption'], color=self.colors['battery'], alpha=0.8, edgecolor='white', linewidth=0.5)
        ax2.set_title(f'🔋 Tasso Autoconsumo Giornaliero {month_name} {month[:4]} (%)', fontsize=18, fontweight='bold')
        ax2.set_xlabel('Giorno del Mese', fontsize=14)
        ax2.set_ylabel('Tasso Autoconsumo (%)', fontsize=14)
        ax2.grid(True, alpha=0.3)
        ax2.set_xticks(df['day'])
        
        # Aggiungi valori sulle barre
        for bar in bars:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.5,
                    f'{height:.1f}%', ha='center', va='bottom', fontsize=10)
        
        plt.tight_layout()
        filename = f'daily_efficiency_comparison_{month}.png'
        plt.savefig(self.monthly_dir / filename, dpi=300, bbox_inches='tight', facecolor='#0f172a')
        plt.close()
        print(f"✅ Confronto giornaliero efficienza {month_name} salvato")

def main():
    """Funzione principale"""
    print("🎯 Auto-Graph Generator per Inverter Dashboard")
    print("=" * 50)
    
    # Verifica dipendenze
    try:
        import matplotlib
        import seaborn
        import pandas
        print("✅ Tutte le dipendenze sono disponibili")
    except ImportError as e:
        print(f"❌ Dipendenza mancante: {e}")
        print("Installa con: pip install matplotlib seaborn pandas")
        return
    
    # Inizializza generatore
    generator = AutoGraphGenerator()
    
    # Genera tutti i grafici
    generator.generate_all_graphs()
    
    print("\n🎉 Generazione completata!")
    print(f"📁 Grafici salvati in:")
    print(f"  📅 Mensili: {generator.monthly_dir}")
    print(f"  📈 Annuali: {generator.yearly_dir}")
    print(f"  🔄 Confronti: {generator.output_dir}")
    print("\n📊 Grafici generati:")
    print("  📅 GRAFICI MENSILI (ultimi 3 mesi):")
    print("    - ☀️ Produzione PV mensile")
    print("    - 🏠 Consumo casa mensile")
    print("    - ⚡ Efficienza sistema mensile")
    print("    - 🔋 Cicli batteria mensili")
    print("  📊 GRAFICI GIORNALIERI MENSILI:")
    print("    - ☀️ Confronto PV giornaliero per mese")
    print("    - 🏠 Confronto consumo giornaliero per mese")
    print("    - ⚡ Confronto efficienza giornaliera per mese")
    print("  📈 GRAFICI ANNUALI:")
    print("    - ☀️ Produzione PV annuale")
    print("    - 🏠 Consumo casa annuale")
    print("    - ⚡ Efficienza sistema annuale")
    print("    - 🔄 Confronto mensile annuale")
    print("  🔄 CONFRONTI MENSILI (ultimi 6 mesi):")
    print("    - ☀️ Confronto produzione PV")
    print("    - 🏠 Confronto consumo")
    print("    - ⚡ Confronto efficienza")
    print("  🌍 CONFRONTI ANNUALI:")
    print("    - 🔄 Confronto tra anni")
    print("    - 🌍 Trend stagionali")
    print("    - 🚨 Monitoraggio anomalie annuale")

if __name__ == "__main__":
    main()

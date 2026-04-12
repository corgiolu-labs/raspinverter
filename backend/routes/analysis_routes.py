# -*- coding: utf-8 -*-
"""Route analisi giornaliera / stagionale (`daily_analyzer`)."""
from __future__ import annotations

import json
import logging
from typing import Any

from flask import Flask, jsonify

from db import db

logger = logging.getLogger(__name__)


def register_analysis_routes(app: Flask, daily_analyzer: Any) -> None:
    @app.route("/api/analysis/daily/<date>")
    def get_daily_analysis(date):
        """Ottiene analisi giornaliera per una data specifica"""
        try:
            analysis = daily_analyzer.analyze_daily_data(date)
            if analysis:
                return jsonify(analysis)
            return jsonify({"error": "Nessun dato trovato per questa data"}), 404
        except Exception as e:
            logger.warning("analysis daily error for %s: %s", date, e)
            return jsonify({"error": f"Errore analisi: {str(e)}"}), 500

    @app.route("/api/analysis/cleanup/<date>", methods=["POST"])
    def cleanup_daily_data(date):
        """Pulisce i campioni giornalieri dopo aver salvato l'analisi"""
        try:
            analysis = daily_analyzer.analyze_daily_data(date)
            if not analysis:
                return jsonify({"error": "Nessun dato da analizzare"}), 404

            daily_analyzer.cleanup_old_samples(date, keep_analysis=True)

            return jsonify({
                "success": True,
                "message": f"Analisi salvata e campioni cancellati per {date}",
                "analysis_summary": {
                    "total_samples": analysis.get("total_samples", 0),
                    "pv_energy": analysis.get("pv_analysis", {}).get("total_energy_kwh", 0),
                    "battery_energy": analysis.get("battery_analysis", {}).get("total_energy_kwh", 0),
                    "anomalies": analysis.get("anomaly_detection", {}).get("total_anomalies", 0)
                }
            })

        except Exception as e:
            logger.warning("analysis cleanup error for %s: %s", date, e)
            return jsonify({"error": f"Errore pulizia: {str(e)}"}), 500

    @app.route("/api/analysis/seasonal")
    def get_seasonal_insights():
        """Ottiene insights stagionali dalle analisi salvate"""
        try:
            with db() as con:
                rows = con.execute("""
                    SELECT date, analysis_data FROM daily_analysis
                    WHERE date >= DATE('now', '-30 days')
                    ORDER BY date DESC
                """).fetchall()

                seasonal_data = []
                for row in rows:
                    try:
                        analysis = json.loads(row[1])
                        seasonal = analysis.get("seasonal_insights", {})
                        if seasonal:
                            seasonal_data.append({
                                "date": row[0],
                                "daylight_hours": seasonal.get("daylight_hours", 0),
                                "season": seasonal.get("season", "unknown"),
                                "pv_energy": analysis.get("pv_analysis", {}).get("total_energy_kwh", 0)
                            })
                    except Exception:
                        logger.debug("seasonal skip row parse failed date=%s", row[0] if row else None)
                        continue

                return jsonify({
                    "period": "30_days",
                    "data_points": len(seasonal_data),
                    "seasonal_data": seasonal_data
                })

        except Exception as e:
            logger.warning("seasonal insights error: %s", e)
            return jsonify({"error": f"Errore insights stagionali: {str(e)}"}), 500

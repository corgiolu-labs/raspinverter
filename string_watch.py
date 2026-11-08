"""Rileva una batteria che smette di condurre (BMS intervenuto o batteria scollegata).

Non basta "corrente a zero": con la rete magliata le correnti si ridistribuiscono e un canale
sano puo' portare quasi nulla (BATT5). Il segno distintivo e' un canale PIATTO mentre la corrente
del pacco cambia: dev. std sotto 0,1 A contro 0,5-3 A dei canali sani.

Regola: finestre da window_s; una finestra vale se il pacco porta almeno min_pack_a con dev. std
di almeno min_pack_std_a. Canale piatto = dev. std < flat_std_a e non saturo (uscita sotto
saturation_mv: un Hall in saturazione e' piatto ma conduce). Allarme dopo need_windows finestre
valide piatte di fila; si chiude alla prima finestra valida in cui il canale si muove.

Lo stato e' in RAM: dopo un riavvio serve di nuovo need_windows per segnalare.
"""
import statistics
import time
from typing import Any, Dict, Optional

CHANNELS = ("BATT1", "BATT2", "BATT3", "BATT4", "BATT5")
# Uscita oltre la quale il Hall e' saturo in scarica (misurato nov 2026: BATT1/5 ~1330 mV, gli altri ~1975).
SATURATION_MV = {"BATT1": 1300, "BATT2": 1900, "BATT3": 1900, "BATT4": 1900, "BATT5": 1300}
# Soglia per mostrare "saturo" in dashboard: vicino al tetto reale, non prudente come quella sopra.
CLIP_MV = {"BATT1": 1315, "BATT2": 1955, "BATT3": 1955, "BATT4": 1955, "BATT5": 1315}


class StringWatch:
    def __init__(self):
        self.t0: Optional[float] = None
        self.pack: list = []
        self.buf: Dict[str, list] = {c: [] for c in CHANNELS}
        self.state: Dict[str, Dict[str, Any]] = {c: self._blank() for c in CHANNELS}
        self.last_window: Optional[Dict[str, Any]] = None

    @staticmethod
    def _blank() -> Dict[str, Any]:
        return {"streak": 0, "since": None, "alert": False, "mean_a": None}

    def _flush(self):
        self.t0 = None
        self.pack = []
        self.buf = {c: [] for c in CHANNELS}

    def update(self, now: float, i2c, pack_a, cfg: Optional[dict] = None):
        cfg = cfg or {}
        if not bool(cfg.get("enabled", True)):
            self._flush()
            self.state = {c: self._blank() for c in CHANNELS}
            return
        if pack_a is None or not isinstance(i2c, dict):
            return
        chans: Dict[str, Any] = {}
        for dev in ("adc_mod1", "adc_mod2"):
            if isinstance(i2c.get(dev), dict):
                chans.update(i2c[dev])
        if self.t0 is None:
            self.t0 = now
        self.pack.append(float(pack_a))
        for c in CHANNELS:
            v = chans.get(c)
            if isinstance(v, dict) and v.get("current_a") is not None:
                self.buf[c].append((float(v["current_a"]), v.get("raw_mv")))
        if now - self.t0 >= float(cfg.get("window_s", 600)):
            self._close(cfg)

    def _close(self, cfg: dict):
        n = len(self.pack)
        if n >= int(cfg.get("min_samples", 40)):
            mean = statistics.mean(self.pack)
            std = statistics.pstdev(self.pack)
            valid = abs(mean) >= float(cfg.get("min_pack_a", 15)) and std >= float(cfg.get("min_pack_std_a", 1.5))
            self.last_window = {"at": time.strftime("%d/%m %H:%M"), "pack_a": round(mean, 1),
                                "pack_std_a": round(std, 2), "valid": valid}
            if valid:
                self._evaluate(n, cfg)
        self._flush()

    def _evaluate(self, n: int, cfg: dict):
        for c in CHANNELS:
            samples = self.buf[c]
            if len(samples) < 0.8 * n:
                continue
            amps = [s[0] for s in samples]
            raws = [float(s[1]) for s in samples if s[1] is not None]
            st = self.state[c]
            sat_mv = float((cfg.get("saturation_mv") or SATURATION_MV).get(c, 1900))
            saturated = bool(raws) and statistics.mean(raws) >= sat_mv
            if statistics.pstdev(amps) < float(cfg.get("flat_std_a", 0.25)) and not saturated:
                st["streak"] += 1
                st["mean_a"] = round(statistics.mean(amps), 1)
                if st["streak"] == 1:
                    st["since"] = time.strftime("%d/%m %H:%M")
                if st["streak"] >= int(cfg.get("need_windows", 3)):
                    if not st["alert"]:
                        print(f"[strings] ALLARME {c}: piatto a {st['mean_a']} A da {st['since']}", flush=True)
                    st["alert"] = True
            else:
                if st["alert"]:
                    print(f"[strings] {c} di nuovo in linea", flush=True)
                self.state[c] = self._blank()

    def status(self) -> Dict[str, Any]:
        alerts = []
        for c in CHANNELS:
            st = self.state[c]
            if st["alert"]:
                if abs(st["mean_a"] or 0.0) < 1.0:
                    msg = f"{c}: nessuna corrente dal {st['since']}. BMS intervenuto o batteria scollegata?"
                else:
                    msg = f"{c}: corrente ferma a {st['mean_a']} A dal {st['since']}. Controllare batteria e sensore."
                alerts.append({"channel": c, "since": st["since"], "mean_a": st["mean_a"], "message": msg})
        return {"ok": True, "alerts": alerts, "last_window": self.last_window,
                "streaks": {c: self.state[c]["streak"] for c in CHANNELS}}

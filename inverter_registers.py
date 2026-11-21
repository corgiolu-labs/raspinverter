"""Catalogo dei registri Modbus dell'inverter EASUN/ISolar SMG-II (holding register, FC03).

Fonte principale: tabella dei registri del progetto syssi/esphome-smg-ii (protocollo SMG-II),
confrontata con makstt232/EASUN-ISOLAR-SMG-II-CONTROL e alcestide/easun-smg-ii-11kw.
La numerazione coincide con quella gia' letta dal software (202 tensione rete, 215 tensione
batteria, ...). "fonte":
  doc     = descritto nella documentazione
  dedotto = non documentato: significato ricavato dal valore letto, da confermare
I registri vengono solo LETTI: nessuna impostazione viene modificata da qui.
"""
from typing import Any, Dict, List, Optional

# Blocchi letti in una scansione (indirizzo iniziale, quantita'): restano sotto i 125 registri.
BLOCKS = ((100, 16), (171, 64), (300, 60), (400, 46), (500, 33), (638, 7))

GROUPS = (
    ("stato", "Stato e allarmi"),
    ("batteria", "Batteria"),
    ("fv", "Fotovoltaico"),
    ("rete", "Rete ENEL"),
    ("uscita", "Inverter e uscita verso casa"),
    ("temp", "Temperature"),
    ("soglie", "Impostazioni: tensioni della batteria"),
    ("carica", "Impostazioni: carica ed equalizzazione"),
    ("modi", "Impostazioni: funzionamento"),
    ("id", "Identificazione e orologio"),
)

WORK_MODE = {0: "Accensione", 1: "Standby", 2: "Da rete", 3: "Off-grid (a batteria)", 4: "Bypass", 5: "In carica", 6: "Guasto"}
# SOL e SUB nei manuali hanno descrizioni quasi identiche (solare, poi rete; batteria solo se manca
# la rete): le fonti non concordano sulla differenza, quindi qui non se ne inventa una.
OUTPUT_PRIORITY = {0: "UTI: prima la rete; solare e batteria solo se la rete manca",
                   1: "SOL: prima il solare, poi la rete; batteria solo se la rete manca",
                   2: "SBU: prima il solare, poi la batteria; rete solo a batteria scarica",
                   3: "SUB: prima il solare, poi la rete; batteria solo se la rete manca (quasi uguale a SOL)"}
CHARGE_PRIORITY = {0: "Priorità alla rete", 1: "Priorità al solare", 2: "Solare e rete insieme", 3: "Solo solare"}
ON_OFF = {0: "Disattivato", 1: "Attivo"}

WARNING_BITS = {
    0: "Perso il passaggio per lo zero della rete", 1: "Forma d'onda della rete anomala", 2: "Tensione di rete troppo alta",
    3: "Tensione di rete troppo bassa", 4: "Frequenza di rete troppo alta", 5: "Frequenza di rete troppo bassa",
    6: "Tensione del fotovoltaico bassa", 7: "Temperatura alta", 8: "Tensione della batteria bassa",
    9: "Batteria non collegata", 10: "Sovraccarico", 11: "Equalizzazione della batteria in corso",
    12: "Batteria scaricata e non ancora ricaricata al punto di ripristino", 13: "Potenza di uscita ridotta",
    14: "Ventola bloccata", 15: "Energia solare troppo bassa per essere usata",
    19: "Comunicazione con la batteria al litio anomala", 20: "Corrente di scarica della batteria troppo alta",
}
FAULT_BITS = {
    0: "Inverter surriscaldato", 1: "Fotovoltaico surriscaldato", 2: "DC-DC surriscaldato", 3: "Sovratensione della batteria",
    5: "Uscita in cortocircuito", 6: "Sovratensione dell'inverter", 7: "Uscita in sovraccarico", 8: "Sovratensione del bus",
    9: "Avvio del bus fallito", 10: "Sovracorrente del fotovoltaico", 11: "Sovratensione del fotovoltaico",
    12: "Sovracorrente DC-DC", 13: "Sovracorrente dell'inverter", 14: "Tensione del bus troppo bassa",
    16: "Tensione continua sull'uscita AC", 22: "Tensione dell'inverter bassa", 23: "Potenza negativa dell'inverter",
    26: "Tipo di batteria non compatibile",
}

def _r(addr, key, group, label, desc, unit="", scale=1.0, signed=False, enum=None, fonte="doc", nota=None, decimals=None):
    return dict(addr=addr, key=key, group=group, label=label, desc=desc, unit=unit, scale=scale, signed=signed,
                enum=enum, fonte=fonte, nota=nota, decimals=decimals)

CATALOG: List[Dict[str, Any]] = [
    # --- stato
    _r(201, "work_mode", "stato", "Modalità di funzionamento", "Da dove l'inverter alimenta la casa in questo momento.", enum=WORK_MODE),
    # 100 e 108 sono trattati a parte (32 bit, campi di bit)
    # --- batteria
    _r(215, "battery_v", "batteria", "Tensione della batteria", "Tensione dell'intero pacco misurata dall'inverter.", "V", 0.1, decimals=1),
    _r(216, "battery_a", "batteria", "Corrente della batteria", "Positiva in carica, negativa in scarica. È la corrente che il Raspberry usa per il SOC.", "A", 0.1, True, decimals=1),
    _r(232, "battery_a_avg", "batteria", "Corrente media della batteria", "Come la precedente, mediata dall'inverter. Positiva in carica, negativa in scarica.", "A", 0.1, True, decimals=1),
    _r(217, "battery_w", "batteria", "Potenza della batteria", "Positiva in carica, negativa in scarica.", "W", 1, True),
    _r(229, "battery_soc", "batteria", "SOC stimato dall'inverter", "Stato di carica calcolato dall'inverter, di solito dalla tensione. È indipendente dal SOC del Raspberry, che conta l'energia.", "%", 1),
    _r(233, "inv_charge_a", "batteria", "Corrente di carica dalla rete", "Parte della carica che arriva dalla rete attraverso l'inverter.", "A", 0.1, True, decimals=1),
    _r(234, "pv_charge_a", "batteria", "Corrente di carica dal solare", "Parte della carica che arriva dal fotovoltaico.", "A", 0.1, True, decimals=1),
    # --- fotovoltaico
    _r(219, "pv_v", "fv", "Tensione dei pannelli", "Tensione della stringa fotovoltaica all'ingresso dell'inverter.", "V", 0.1, decimals=1),
    _r(220, "pv_a", "fv", "Corrente dei pannelli", "Corrente che arriva dai pannelli.", "A", 0.1, decimals=1),
    _r(223, "pv_w", "fv", "Potenza dei pannelli", "Potenza prodotta dal fotovoltaico.", "W", 1),
    _r(224, "pv_charge_w", "fv", "Potenza solare verso la batteria", "Quota della produzione solare usata per caricare la batteria.", "W", 1, True),
    # --- rete
    _r(202, "grid_v", "rete", "Tensione di rete", "Tensione della rete ENEL vista dall'inverter. È presente anche quando l'inverter non la usa.", "V", 0.1, decimals=1),
    _r(203, "grid_hz", "rete", "Frequenza di rete", "Frequenza della rete ENEL.", "Hz", 0.01, decimals=2),
    _r(204, "grid_w", "rete", "Potenza prelevata dalla rete", "Potenza che l'inverter sta prendendo dalla rete in questo momento.", "W", 1, True),
    # --- uscita
    _r(205, "inv_v", "uscita", "Tensione generata dall'inverter", "Tensione prodotta dallo stadio inverter.", "V", 0.1, decimals=1),
    _r(206, "inv_a", "uscita", "Corrente dell'inverter", "Corrente erogata dallo stadio inverter.", "A", 0.1, decimals=1),
    _r(207, "inv_hz", "uscita", "Frequenza dell'inverter", "Frequenza generata dall'inverter.", "Hz", 0.01, decimals=2),
    _r(208, "inv_w", "uscita", "Potenza dell'inverter", "Positiva quando eroga verso la casa, negativa quando assorbe dalla rete.", "W", 1, True),
    _r(209, "inv_charge_w", "uscita", "Potenza di carica dell'inverter", "Potenza che l'inverter usa per caricare la batteria dalla rete.", "W", 1, True),
    _r(210, "out_v", "uscita", "Tensione verso la casa", "Tensione presente sull'uscita che alimenta la casa.", "V", 0.1, decimals=1),
    _r(211, "out_a", "uscita", "Corrente verso la casa", "Corrente assorbita dalla casa.", "A", 0.1, decimals=1),
    _r(212, "out_hz", "uscita", "Frequenza verso la casa", "Frequenza sull'uscita.", "Hz", 0.01, decimals=2),
    _r(213, "out_w", "uscita", "Potenza attiva della casa", "Consumo reale della casa.", "W", 1),
    _r(214, "out_va", "uscita", "Potenza apparente della casa", "Consumo comprensivo della parte reattiva: è sempre uguale o superiore alla potenza attiva.", "VA", 1),
    _r(225, "load_pct", "uscita", "Carico dell'inverter", "Percentuale della potenza nominale dell'inverter in uso.", "%", 1),
    _r(218, "dc_bus_v", "uscita", "Tensione del bus interno", "Tensione continua interna da cui l'inverter ricava i 230 V. Non è nella documentazione: il significato viene dal software esistente.", "V", 0.1, fonte="dedotto", decimals=1),
    # --- temperature
    _r(226, "dcdc_temp", "temp", "Temperatura DC-DC", "Temperatura del convertitore lato batteria e pannelli.", "°C", 1, True),
    _r(227, "inv_temp", "temp", "Temperatura inverter", "Temperatura dello stadio che genera i 230 V.", "°C", 1, True),
    _r(228, "temp_228", "temp", "Temperatura del dissipatore", "Non è nella documentazione: il software la tratta come temperatura del dissipatore e il valore è coerente.", "°C", 1, True, fonte="dedotto"),
    # --- soglie batteria
    _r(323, "batt_ovp", "soglie", "Protezione sovratensione batteria", "Sopra questa tensione l'inverter interrompe la carica per proteggere la batteria.", "V", 0.1, decimals=1),
    _r(324, "bulk_v", "soglie", "Tensione massima di carica", "Tensione a cui l'inverter carica la batteria nella fase principale.", "V", 0.1, decimals=1),
    _r(325, "float_v", "soglie", "Tensione di mantenimento", "Tensione a cui tiene la batteria quando è carica.", "V", 0.1, decimals=1),
    _r(327, "back_to_grid_v", "soglie", "Passa alla rete sotto", "Con la rete disponibile, sotto questa tensione l'inverter smette di usare la batteria e prende la casa dalla rete.", "V", 0.1, decimals=1),
    _r(326, "back_to_batt_v", "soglie", "Torna alla batteria sopra", "Dopo essere passato alla rete, torna a usare la batteria quando la tensione risale sopra questo valore.", "V", 0.1, decimals=1),
    _r(329, "low_cutoff_v", "soglie", "Spegnimento per batteria scarica", "Senza rete, sotto questa tensione l'inverter si spegne per non scaricare troppo la batteria.", "V", 0.1, decimals=1),
    # --- carica
    _r(331, "charge_priority", "carica", "Da dove carica la batteria", "Fonte preferita per caricare la batteria.", enum=CHARGE_PRIORITY),
    _r(332, "max_charge_a", "carica", "Corrente massima di carica", "Limite complessivo, solare più rete.", "A", 0.1, decimals=1),
    _r(333, "max_grid_charge_a", "carica", "Corrente massima di carica dalla rete", "Limite della carica presa dalla rete.", "A", 0.1, decimals=1),
    _r(313, "eq_enable", "carica", "Equalizzazione", "Carica periodica a tensione più alta per riallineare le celle. Di solito si usa con batterie al piombo.", enum=ON_OFF),
    _r(334, "eq_v", "carica", "Tensione di equalizzazione", "Tensione usata durante l'equalizzazione.", "V", 0.1, decimals=1),
    _r(335, "eq_time", "carica", "Durata dell'equalizzazione", "Quanto dura l'equalizzazione.", "min", 1),
    _r(336, "eq_timeout", "carica", "Tempo massimo di equalizzazione", "Oltre questo tempo l'equalizzazione si interrompe comunque.", "min", 1),
    _r(337, "eq_interval", "carica", "Intervallo di equalizzazione", "Ogni quanti giorni verrebbe ripetuta.", "giorni", 1),
    # --- modi
    _r(301, "output_priority", "modi", "Priorità di uscita", "Ordine delle fonti con cui l'inverter alimenta la casa.", enum=OUTPUT_PRIORITY),
    _r(300, "output_mode", "modi", "Modo di uscita", "Inverter singolo o collegato in parallelo con altri.",
       enum={0: "Singolo", 1: "Parallelo", 2: "Trifase P1", 3: "Trifase P2", 4: "Trifase P3"}),
    _r(302, "input_range", "modi", "Tolleranza della tensione di rete", "Quanto la rete può discostarsi dai 230 V prima che l'inverter la rifiuti.",
       enum={0: "Ampia (anche generatori)", 1: "Stretta (tipo UPS)"}),
    _r(320, "out_v_set", "modi", "Tensione di uscita impostata", "Tensione che l'inverter genera verso la casa.", "V", 0.1, decimals=1),
    _r(321, "out_hz_set", "modi", "Frequenza di uscita impostata", "Frequenza che l'inverter genera verso la casa.", "Hz", 0.01, decimals=2),
    _r(303, "buzzer", "modi", "Cicalino", "Quando l'inverter emette segnali acustici.",
       enum={0: "Sempre muto", 1: "Cambi di fonte, avvisi e guasti", 2: "Avvisi e guasti", 3: "Solo guasti"}),
    _r(305, "backlight", "modi", "Retroilluminazione del display", "Se il display resta acceso.", enum={0: "Si spegne dopo un po'", 1: "Sempre accesa"}),
    _r(306, "lcd_return", "modi", "Ritorno alla schermata principale", "Se il display torna da solo alla schermata iniziale.", enum={0: "No", 1: "Dopo 1 minuto"}),
    _r(307, "eco_mode", "modi", "Risparmio energetico", "Con carico quasi nullo l'inverter riduce i propri consumi.", enum=ON_OFF),
    _r(308, "restart_overload", "modi", "Riavvio dopo sovraccarico", "Se riparte da solo dopo uno spegnimento per sovraccarico.", enum=ON_OFF),
    _r(309, "restart_overtemp", "modi", "Riavvio dopo surriscaldamento", "Se riparte da solo dopo uno spegnimento per temperatura.", enum=ON_OFF),
    _r(310, "bypass_overload", "modi", "Passaggio alla rete in sovraccarico", "Con un carico eccessivo passa la casa direttamente sulla rete, se c'è.", enum=ON_OFF),
    _r(406, "power_on_mode", "modi", "Comando di accensione", "Da dove si può accendere e spegnere l'inverter.",
       enum={0: "Dal pannello o da remoto", 1: "Solo dal pannello", 2: "Solo da remoto"}),
    _r(420, "remote_switch", "modi", "Interruttore remoto", "Stato del comando di accensione remoto.", enum={0: "Spento da remoto", 1: "Acceso"}),
    # --- identificazione
    _r(643, "rated_w", "id", "Potenza nominale", "Potenza massima continuativa dell'inverter.", "W", 1),
    _r(184, "protocol", "id", "Versione del protocollo", "Variante del protocollo Modbus usata dall'inverter.", ""),
    _r(171, "device_type", "id", "Tipo di apparecchio", "Codice del modello. Il significato dei codici non è pubblicato.", ""),
]

DOCUMENTED = {r["addr"] for r in CATALOG} | {100, 101, 108, 109, 104, 105} | set(range(186, 198)) | set(range(434, 440))


def _signed(v: int) -> int:
    return v - 65536 if v > 32767 else v


def _bits(value: int, names: Dict[int, str]) -> List[str]:
    out = []
    for b in range(32):
        if value >> b & 1:
            out.append(names.get(b, "bit %d (senza descrizione)" % b))
    return out


def describe(raw: Dict[int, int]) -> Dict[str, Any]:
    """Valori leggibili a partire dai registri grezzi {indirizzo: valore 0..65535}."""
    items = []
    for r in CATALOG:
        v = raw.get(r["addr"])
        if v is None:
            continue
        n = _signed(v) if r["signed"] else v
        if r["enum"] is not None:
            text = r["enum"].get(n, "valore %d (senza descrizione)" % n)
            value = n
        else:
            value = n * r["scale"]
            d = r["decimals"] if r["decimals"] is not None else (0 if r["scale"] >= 1 else 2)
            text = ("%." + str(d) + "f") % value
            text = text.replace(".", ",") + ((" " + r["unit"]) if r["unit"] else "")
        items.append({k: r[k] for k in ("addr", "key", "group", "label", "desc", "fonte", "nota")} | {"raw": v, "value": value, "text": text})

    def u32(a):
        if raw.get(a) is None or raw.get(a + 1) is None:
            return None
        return (raw[a] << 16) | raw[a + 1]

    for addr, key, label, names, desc in (
        (100, "faults", "Guasti", FAULT_BITS, "Guasti che bloccano l'inverter. La decodifica dei singoli bit varia tra le fonti: in caso di guasto controlla anche il display."),
        (108, "warnings", "Avvisi", WARNING_BITS, "Avvisi attivi: non fermano l'inverter, segnalano una condizione da tenere d'occhio."),
    ):
        v = u32(addr)
        if v is None:
            continue
        active = _bits(v, names)
        items.append({"addr": addr, "key": key, "group": "stato", "label": label, "desc": desc, "fonte": "doc", "nota": None,
                      "raw": v, "value": v, "text": ", ".join(active) if active else "Nessuno", "active": active})

    serial = "".join(chr(c) for a in range(186, 198) if raw.get(a) for c in (raw[a] >> 8, raw[a] & 255) if 32 <= c < 127).strip()
    if any(a in raw for a in range(186, 198)):
        items.append({"addr": 186, "key": "serial", "group": "id", "label": "Numero di serie", "desc": "Registri 186-197, testo.",
                      "fonte": "doc", "nota": None, "raw": None, "value": serial, "text": serial or "Non impostato"})
    if all(raw.get(a) is not None for a in range(434, 440)) and 2000 < raw[434] < 2100:
        y, mo, d, h, mi, s = (raw[a] for a in range(434, 440))
        items.append({"addr": 434, "key": "clock", "group": "id", "label": "Orologio interno", "fonte": "dedotto",
                      "desc": "Registri 434-439: anno, mese, giorno, ore, minuti, secondi. Non documentati, ma verificati il 20/11/2026: i secondi avanzano in tempo reale. Si regola dalla pagina Imposta inverter.",
                      "nota": "La data è molto indietro: regola l'orologio dalla pagina Imposta inverter." if y < 2025 else None,
                      "raw": None, "value": None, "text": "%02d/%02d/%04d %02d:%02d:%02d" % (d, mo, y, h, mi, s)})

    notes = dynamic_notes(raw)
    for it in items:
        if it["addr"] in notes and it.get("key") != "clock":
            it["nota"] = notes[it["addr"]]

    unknown = [{"addr": a, "raw": v, "signed": _signed(v)} for a, v in sorted(raw.items()) if v and a not in DOCUMENTED]
    return {"items": items, "unknown": unknown, "groups": [{"key": k, "label": l} for k, l in GROUPS]}


def dynamic_notes(raw: dict) -> dict:
    """Note calcolate sui valori attuali, cosi' la pagina Registri resta vera dopo ogni modifica."""
    v = lambda a: raw[a] / 10.0
    it = lambda x, d=1: ("%.*f" % (d, x)).replace(".", ",")
    n = {}
    if raw.get(329) is not None:
        n[329] = ("Sotto lo stacco dei BMS (%s V): senza rete i BMS staccherebbero la batteria prima dell'inverter." % it(BMS_CUTOFF_V)
                  if v(329) < BMS_CUTOFF_V + 0.1 else
                  "Sopra lo stacco dei BMS (%s V): senza rete si spegne prima l'inverter, in modo pulito." % it(BMS_CUTOFF_V))
    if raw.get(324) is not None:
        c = v(324) / 16
        n[324] = "%s V per cella (16 celle)%s." % (it(c, 3), ": oltre 3,65 V le celle si sovraccaricano" if c > 3.65 else "")
    if raw.get(325) is not None:
        c = v(325) / 16
        n[325] = "%s V per cella. Resta sopra i 57,0 V con cui il Raspberry riconosce la carica completa." % it(c, 3) if v(325) >= 57.0 else \
                 "%s V per cella." % it(c, 3)
    if raw.get(327) is not None and raw.get(326) is not None:
        n[327] = n[326] = "Coincide con le soglie del relè ENEL del Raspberry (48/51 V): vanno cambiate insieme." \
            if (raw[327], raw[326]) == (480, 510) else "Diversa dalle soglie del relè ENEL del Raspberry (48/51 V): controllare."
    if raw.get(331) == 0:
        n[331] = "Priorità alla rete: per un impianto off-grid conviene la priorità al solare."
    for a in (329, 327, 326, 325, 324, 332, 333, 331, 301, 303, 305, 306):
        if raw.get(a) is not None:
            n[a] = ((n.get(a) or "") + " Modificabile dalla pagina Imposta inverter.").strip()
    return n


CLOCK_ADDR = 434   # 434-439: anno, mese, giorno, ore, minuti, secondi


def clock_from_raw(raw: dict):
    vals = [raw.get(a) for a in range(CLOCK_ADDR, CLOCK_ADDR + 6)]
    if None in vals or not 2000 < vals[0] < 2100:
        return None
    return vals


# ---------------------------------------------------------------------------
# Impostazioni che la pagina "Imposta inverter" puo' scrivere (FC16, verificato il 20/11/2026).
# Tutto il resto e' escluso di proposito: accensione/spegnimento remoto (420), tensione e
# frequenza d'uscita, equalizzazione (dannosa per il litio), protezione sovratensione (323),
# modo parallelo, protezioni di riavvio e registri non documentati.
# I limiti sono piu' stretti di quelli dell'inverter e pensati per il pacco LiFePO4 16S.
# ---------------------------------------------------------------------------
BMS_CUTOFF_V = 44.4   # 22,2 V per modulo (datasheet DATOU BOSS) x 2 banchi

WRITABLE = [
    dict(addr=329, group="Tensioni della batteria", label="Spegnimento per batteria scarica", unit="V", scale=0.1, min=44.5, max=48.0, step=0.1, impact="alto",
         desc="Senza rete l'inverter si spegne sotto questa tensione. Deve restare sopra lo stacco dei BMS (44,4 V) e sotto la soglia di passaggio alla rete."),
    dict(addr=327, group="Tensioni della batteria", label="Passa alla rete sotto", unit="V", scale=0.1, min=45.0, max=52.0, step=0.1, impact="alto",
         desc="Con la rete disponibile, sotto questa tensione l'inverter prende la casa dalla rete."),
    dict(addr=326, group="Tensioni della batteria", label="Torna alla batteria sopra", unit="V", scale=0.1, min=48.0, max=56.0, step=0.1, impact="alto",
         desc="Dopo il passaggio alla rete, torna a usare la batteria sopra questa tensione."),
    dict(addr=325, group="Tensioni della batteria", label="Tensione di mantenimento", unit="V", scale=0.1, min=52.0, max=57.6, step=0.1, impact="alto",
         desc="Tensione a cui l'inverter tiene la batteria carica. Non può superare la tensione massima di carica."),
    dict(addr=324, group="Tensioni della batteria", label="Tensione massima di carica", unit="V", scale=0.1, min=54.0, max=58.4, step=0.1, impact="alto",
         desc="Massimo 58,4 V (3,65 V per cella). Valori più alti sovraccaricano le celle."),
    dict(addr=332, group="Carica", label="Corrente massima di carica", unit="A", scale=0.1, min=10, max=100, step=1, impact="medio",
         desc="Limite complessivo della corrente di carica, solare più rete."),
    dict(addr=333, group="Carica", label="Corrente massima di carica dalla rete", unit="A", scale=0.1, min=2, max=60, step=1, impact="medio",
         desc="Quanto l'inverter può caricare prendendo energia dalla rete."),
    dict(addr=331, group="Carica", label="Da dove carica la batteria", enum=CHARGE_PRIORITY, impact="medio",
         desc="Fonte preferita per caricare la batteria."),
    dict(addr=301, group="Funzionamento", label="Priorità di uscita", enum=OUTPUT_PRIORITY, impact="alto",
         desc="Ordine delle fonti con cui l'inverter alimenta la casa. SBU usa la batteria prima della rete."),
    dict(addr=303, group="Display e cicalino", label="Cicalino", impact="basso",
         enum={0: "Sempre muto", 1: "Cambi di fonte, avvisi e guasti", 2: "Avvisi e guasti", 3: "Solo guasti"},
         desc="Quando l'inverter emette segnali acustici."),
    dict(addr=305, group="Display e cicalino", label="Retroilluminazione del display", impact="basso",
         enum={0: "Si spegne dopo un po'", 1: "Sempre accesa"}, desc="Se il display resta acceso."),
    dict(addr=306, group="Display e cicalino", label="Ritorno alla schermata principale", impact="basso",
         enum={0: "No", 1: "Dopo 1 minuto"}, desc="Se il display torna da solo alla schermata iniziale."),
]
WRITABLE_BY_ADDR = {w["addr"]: w for w in WRITABLE}
SETTINGS_BLOCKS = ((300, 40), (434, 6))   # 300-339 impostazioni e vincoli, 434-439 orologio


def to_raw(spec, value) -> int:
    """Valore leggibile -> grezzo, con controllo di limiti e passo. Solleva ValueError."""
    if spec.get("enum") is not None:
        v = int(value)
        if v not in spec["enum"]:
            raise ValueError("valore non ammesso")
        return v
    v = float(value)
    if v < spec["min"] - 1e-9 or v > spec["max"] + 1e-9:
        raise ValueError("fuori dall'intervallo %s-%s %s" % (spec["min"], spec["max"], spec["unit"]))
    steps = round((v - spec["min"]) / spec["step"])
    if abs(spec["min"] + steps * spec["step"] - v) > 1e-6:
        raise ValueError("passo non valido (multipli di %s %s)" % (spec["step"], spec["unit"]))
    return int(round(v / spec["scale"]))


def from_raw(spec, raw: int):
    if spec.get("enum") is not None:
        return raw
    return round(raw * spec["scale"], 3)


def cross_check(raw: dict) -> list:
    """Vincoli tra le tensioni, su valori grezzi (0,1 V). Ritorna gli errori in italiano."""
    v = lambda a: raw.get(a, 0) / 10.0
    errs = []
    if v(329) < BMS_CUTOFF_V + 0.1:
        errs.append("lo spegnimento (%.1f V) deve stare sopra lo stacco dei BMS (%.1f V)" % (v(329), BMS_CUTOFF_V))
    if v(329) > v(327) - 1.0:
        errs.append("lo spegnimento (%.1f V) deve stare almeno 1 V sotto il passaggio alla rete (%.1f V)" % (v(329), v(327)))
    if v(327) > v(326) - 1.0:
        errs.append("il passaggio alla rete (%.1f V) deve stare almeno 1 V sotto il ritorno alla batteria (%.1f V)" % (v(327), v(326)))
    if v(326) > v(325):
        errs.append("il ritorno alla batteria (%.1f V) non può superare la tensione di mantenimento (%.1f V)" % (v(326), v(325)))
    if v(325) > v(324):
        errs.append("il mantenimento (%.1f V) non può superare la tensione massima di carica (%.1f V)" % (v(325), v(324)))
    if raw.get(323) and v(324) > v(323) - 1.0:
        errs.append("la carica massima (%.1f V) deve stare almeno 1 V sotto la protezione sovratensione (%.1f V)" % (v(324), v(323)))
    return errs

# RASPYNVERTER — off-grid inverter / solar monitoring & management

Embedded system to monitor and manage an **off-grid** solar installation, running 24/7 on a **Raspberry Pi 3**.
It reads the inverter over **Modbus RTU**, measures battery currents and voltages with **Hall-effect** sensors and **ADS1115** ADCs (I2C), estimates **state of charge by coulomb counting**, drives **bank balancing** and **automatic grid changeover**, and shows everything in a **PWA dashboard** with live and historical charts.

**Flask + SQLite** backend, **PWA frontend with Chart.js** (works offline too). All on a single Pi.

## ✨ Features

- **PWA dashboard**, dark theme: live values, history by hour / day / month, installable on mobile. **Chart.js vendored locally** → no CDN, works **offline**.
- **Inverter readout** over Modbus RTU (`/dev/serial0`), robust on both **pymodbus 2.x** and **3.x**, with automatic fallback to `minimalmodbus`.
- **Battery measurement independent of the inverter**: currents via **Hall WCS1800** sensors, two-bank voltages via **dividers** → **2× ADS1115** (I2C, 16-bit).
- **Coulomb-counting state of charge** with **auto-calibration at full** (physical rule: full voltage + tail current + sunlight present).
- **Relay control**: **active balancing** of the two banks (isolated charger onto the lower bank) and a **grid relay** that switches to the mains when the total series voltage of the two banks drops below **46 V**.
- **Daily off-grid analysis**: autonomy, energy drawn from the grid, LiFePO4 battery health, PV surplus, diagnostics.
- **SQLite history** with automatic archiving and trimming.

## 🔌 The real system

Monitored **off-grid** installation: **EASUN** hybrid inverter, **LiFePO4 51.2 V / 500 Ah** bank (10 prismatic cells, two 24 V banks in series), currents via **Hall WCS1800** sensors, voltages via **ADS1115** (I2C), inverter readout over **RS485 / Modbus RTU**.

**Self-designed, 3D-printed enclosure** — a single chassis integrating: the **Raspberry Pi**, the **DC-DC converter** that powers the Pi from bank 1 at 24 V (+ planned 5 V buffer), **2× ADS1115** digitizing the **Hall-effect** sensors (currents) and the **voltage dividers** (the two banks' voltages, in real time), and the **RS485** module that reads the inverter over **Modbus** and feeds the dashboard. The **3 relays** drive **active balancing** of the two banks and the **grid relay**, which switches to the mains when the total voltage drops below **46 V**.

![Self-designed, 3D-printed controller: Raspberry Pi + DC-DC + 2x ADS1115 + RS485](docs/hardware-chassis.jpg)

| Full installation | Panel (inverter, meters, Pi) |
|:---:|:---:|
| ![Full installation](docs/hardware-system.jpg) | ![Electrical panel](docs/hardware-panel.jpg) |

| Raspberry Pi + GPIO | Hall current sensor (WCS1800) | RS485 adapter (Modbus) |
|:---:|:---:|:---:|
| ![Raspberry Pi](docs/hardware-pi.jpg) | ![Hall sensor WCS1800](docs/hardware-wcs1800.jpg) | ![RS485 adapter](docs/hardware-modbus.jpg) |

<sub>Photos of the real system in operation · EXIF metadata stripped.</sub>

## 📊 Dashboard

Dark-theme PWA, **Chart.js** charts (DB-backed) and **live** values: inverter via **Modbus RTU**, per-cell voltages via **I2C ADS1115**, daily energy totals.

![Overview](docs/dashboard-overview.png)

| ☀️ Solar production (live) | 📈 Energy per hour (history) |
|:---:|:---:|
| ![Solar](docs/dashboard-solar.png) | ![History](docs/dashboard-history.png) |

🔋 **Battery** — SOC, power / voltage / current, **per-cell** voltages (ADS1115), calibration and cell history:

![Battery](docs/dashboard-battery.png)

<p align="center"><img src="docs/dashboard-mobile.png" width="300" alt="Mobile view (PWA)"></p>

<sub>Screenshots from the real system in operation — live data (interface in Italian).</sub>

## 🧱 Architecture

Modular Python backend with separated responsibilities (acyclic dependency graph `config` → `database` / `hardware` → `inverter_api`):

| Module | Responsibility |
|---|---|
| `config.py` | paths, loading `inverter_config.json`, Modbus/I2C constants, register map, helpers |
| `database.py` | SQLite layer (samples, archive, battery counters, I2C snapshots) + archiving/trim |
| `hardware.py` | hardware access: I2C (ADS1115), Modbus RTU, GPIO/relays; holds the runtime state |
| `daily_analyzer.py` | off-grid analysis for the `/analysis` page |
| `inverter_api.py` | entrypoint: Flask app, poll loop in a thread, REST routes, `main()` |

**PWA frontend** in `web/`: Jinja templates with a shared `_base.html`, service worker (`sw.js`), web manifest and `chart.umd.min.js` served locally (offline dashboard).

## 🛠️ Hardware

- **Raspberry Pi 3**
- **Hybrid inverter** with a Modbus RTU / RS485 interface (here an **EASUN**)
- **LiFePO4 bank** (here 51.2 V / 500 Ah — two 24 V banks in series, 10 prismatic cells)
- **2× ADS1115** (16-bit I2C ADC, addresses `0x48` / `0x49`)
- **Hall-effect current sensors WCS1800** (powered at **3.3 V**, not 5 V: the ADS1115 is not 5 V-tolerant)
- **Voltage dividers** for the two banks
- **RS485 ↔ TTL module** for the Pi's UART
- **DC-DC converter** 24 V → 5 V to power the Pi from the bank
- **Relay modules** (bank balancing + grid relay)
- **3D-printed enclosure** integrating everything

## ⚙️ Prerequisites (Raspberry Pi OS / Debian)

1. **Hardware UART** for Modbus (`/dev/serial0`):
   ```bash
   sudo raspi-config   # Interface Options → Serial Port: login shell = NO, hardware = YES
   ```
   On the Pi 3 it helps to free the UART from Bluetooth. In `/boot/firmware/config.txt`:
   ```
   enable_uart=1
   dtoverlay=disable-bt
   ```
   then `sudo systemctl disable --now hciuart serial-getty@ttyAMA0 && sudo reboot`.
   > ⚠️ The **serial login console** must be disabled: if it stays active it "eats" the Modbus bytes and the inverter appears mute. Make sure `console=serial0,115200` is not present in `/boot/firmware/cmdline.txt`.

2. **I2C** for the ADS1115:
   ```bash
   sudo raspi-config   # Interface Options → I2C → Enable
   i2cdetect -y 1      # 0x48 and 0x49 should appear
   ```

3. User in the hardware groups:
   ```bash
   sudo usermod -aG dialout,i2c,gpio "$USER"
   ```

## 🚀 Installation

```bash
# on the Raspberry Pi
git clone https://github.com/corgiolu-labs/raspinverter.git
cd raspinverter
python3 -m pip install --break-system-packages -r requirements.txt   # or in a venv

sudo cp inverter.service /etc/systemd/system/inverter.service
sudo systemctl daemon-reload
sudo systemctl enable --now inverter.service
journalctl -u inverter.service -f      # live logs
```

Then open the dashboard at **`http://<pi-ip>:8000`**.

> On Debian Trixie, if relay (GPIO) init fails, install `rpi-lgpio` (it provides the `RPi.GPIO` API on the new kernel).

## 🔧 Configuration

`config/inverter_config.json` — serial parameters, LiFePO4 battery (capacity, SOC thresholds), relay thresholds (balancing, grid), ADC channels and offsets. Also editable from the **`/settings`** page. The SQLite database is created automatically in `data/` on first run.

## 🩺 Modbus diagnostics

The `/dev/serial0` bus is **shared**: stop the service before a manual test (they can't read at the same time).
```bash
sudo systemctl stop inverter.service
python3 realtime_inverter_test.py --port /dev/serial0 --baud 9600 --unit-id 1 --interval 5
sudo systemctl start inverter.service
```
The dashboard's **🔧 Diagnostics** tab also offers relay tests, live Modbus/I2C readout and battery-counter resets.

## 📄 License

Released under the **Creative Commons Attribution-NonCommercial 4.0** license (CC BY-NC 4.0) — see [LICENSE](LICENSE). © 2026 Alessandro Corgiolu.

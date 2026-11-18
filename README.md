# Home Energy Setup

Home energy monitoring stack for a solar + battery + EV setup in Ireland.

## Stack

- **Inverter**: Livoltek hybrid (solar + battery)
- **EV Charger**: Easee (Unicron)
- **Smart Meter**: ESB Networks (Ireland) half-hourly HDF data
- **Database**: InfluxDB 1.x (local Windows PC)
- **Dashboard**: Home Assistant (HAOS in VirtualBox)

## ESB Smart Meter Scripts

Fetches half-hourly kWh readings from ESB Networks and writes to InfluxDB.

### Setup

1. Install dependencies:
   ```bash
   pip install requests beautifulsoup4 influxdb pytz playwright
   playwright install chromium
   ```

2. Copy `.secrets.example` to `.secrets` and fill in your credentials:
   ```
   cp .secrets.example .secrets
   ```

3. Run InfluxDB 1.x locally. Create database:
   ```
   influx -execute "CREATE DATABASE esb_smart_meter"
   ```

### Scripts

| Script | Purpose |
|---|---|
| `esb-smart-meter-reader.py` | Automated nightly run — requests-based B2C login, fetches CSV, writes to InfluxDB |
| `esb_manual_login.py` | Manual headed-browser run — use when CAPTCHA is triggered or for first-time data load |
| `esb_playwright_runner.py` | Playwright runner using saved cookies from manual login |
| `run_esb.ps1` | PowerShell wrapper — called by Windows Task Scheduler at 02:00 daily |

### Notes

- ESB B2C login triggers CAPTCHA after repeated rapid logins — rate clears after ~6 hours
- Download endpoint (as of Nov 2026): `POST /energymanagement/api/consumption/DownloadHdfPeriodic`
- Timestamps must be written with `time_precision='s'` — InfluxDB defaults to nanoseconds
- CSV format: columns `MPRN`, `Meter Serial Number`, `Read Value`, `Read Type`, `Read Date and End Time`
- ReadTypes: `Active Import Interval (kWh)` and `Active Export Interval (kWh)`

## Home Assistant Configuration

### `ha/configuration.yaml`

- Template sensors: Solar Surplus, Battery SoC, Solar Generation, tariff price sensors, Battery Grid Charge Power
- InfluxDB sensors: ESB Grid Import kWh, ESB Grid Export kWh, ESB Grid Import/Export Energy (for Energy dashboard)
- Riemann sum integrals: Battery Energy In/Out, Grid Import Cost, Grid Export Earnings
- Utility meters: grid import/export/cost/earnings — daily/weekly/monthly with day/night/peak tariffs

### `ha/automations.yaml`

| Automation | Description |
|---|---|
| EV Night-Band Charging | Enables Easee charger 23:00–08:00 on night rate |
| EV Solar Surplus Charging | Enables surplus charging when export >1.4 kW for 5 min |
| Immersion Solar Diversion | Runs immersion heater on solar surplus / battery >80% |
| Immersion Night-Rate Morning | Heats water tank 05:00–07:00 on night rate |
| Tariff Band Switcher | Switches utility meter tariffs at Night/Day/Peak boundaries |
| Daily Energy Summary (Gemini) | AI energy summary at 19:00 via Google Gemini |
| Livoltek Auto Reload | Reloads Livoltek integration if cloud sensors unavailable 5 min |

### `ha/lovelace_overview.json`

Graph-based Overview dashboard:
1. Gemini AI energy summary
2. Power (24h): Solar, House Load, Grid Import, Battery Charging, EV Charging
3. Battery SoC (24h)
4. Cost summary table (today/week/month) + Cost Rate graph

### Tariffs (Flogas DG2 Smart, Ireland)

| Band | Hours | Rate |
|---|---|---|
| Night | 23:00–08:00 | €0.2306/kWh |
| Peak | 17:00–19:00 | €0.3779/kWh |
| Day | All other | €0.3194/kWh |
| Export | — | €0.185/kWh |

## Livoltek Integration Notes

- Uses [hass-livoltek](https://github.com/adamlonsdale/hass-livoltek) custom component
- `sensor.qpmtwtuj_*` — local inverter sensors (always available)
- `sensor.aidan_molloy_*` — cloud API sensors (can go unavailable)
- `qpmtwtuj_export_power` = inverter AC output, not purely grid export
- `battery_grid_charge_power` template = `qpmtwtuj_export_power - unicron_power`
- Token renewal: Settings > Livoltek portal > generate token, update `usertoken_id` in HA config entry

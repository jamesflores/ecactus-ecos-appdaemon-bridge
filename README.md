# ECOS Bridge for Home Assistant (AppDaemon)

Bring live data from an **eCactus / Weiheng ECOS** home battery into Home Assistant: battery state of charge, battery power, the battery's own solar (MPPT) input and more. It uses the same cloud account as the ECOS phone app.

It's useful when the battery has no local integration. It's especially useful for **AC-coupled setups**, where a CT clamp on the battery's AC side can't see solar going into the battery's own DC MPPT input.

> **Unofficial.** This uses the undocumented ECOS cloud API. It can change or stop working without notice, and it is not affiliated with or endorsed by eCactus or Weiheng.

## Credits

The API details (datacenter hosts, login flow, endpoints and field names) come from **[ecactus-ecos-py](https://github.com/gmasse/ecactus-ecos-py)** by Germain Masse. Thank you! This project is licensed under GPL-3.0 to match.

## Sensors

Entity IDs use a configurable prefix (default `ecos`).

| Entity | Unit | Meaning |
|---|---|---|
| `sensor.ecos_battery_soc` | % | Battery state of charge (0–100) |
| `sensor.ecos_solar_power` | W | PV going into the battery's own MPPT input(s), all strings combined |
| `sensor.ecos_battery_power` | W | Power into/out of the battery cells. **Positive = charging** |
| `sensor.ecos_grid_power` | W | Inverter AC side. Negative = AC flowing into the battery |
| `sensor.ecos_meter_power` | W | ECOS grid meter reading. Negative = export |
| `sensor.ecos_home_power` | W | ECOS's own "home" figure. Can be wrong on AC-coupled setups |
| `sensor.ecos_bridge_status` | – | `ok` or the last error, with `last_update` / `last_update_ts` attributes |

<img width="1512" height="830" alt="image" src="https://github.com/user-attachments/assets/be791729-0584-46e6-960e-2ad538df31d2" />

<img width="1065" height="825" alt="image" src="https://github.com/user-attachments/assets/47d34eab-cbf5-448b-920c-82e2f974ebbf" />


Notes:

- ECOS usually refreshes its cloud data every few minutes. The bridge polls every 60 s by default, so values change in steps.
- The sensors are created with `set_state`, so they can't be edited in the HA UI, and they disappear after an HA restart until the next poll (within a minute).
- They still have `device_class` and `state_class`, so they work with long-term statistics, Integral (Riemann sum) helpers, gauges and graphs.

## Install

1. **Install AppDaemon**: Settings → Add-ons → Add-on Store → **AppDaemon**. Start it once so it creates its config folder.
2. **Add the app files**. Either:
   - **HACS**: HACS → ⋮ → Custom repositories → add this repo's URL as type **AppDaemon**, then download **ECOS Bridge**. You may need to enable AppDaemon apps in HACS's integration settings first.
   - **Manual**: copy `apps/ecos_bridge/ecos_bridge.py` into `/addon_configs/a0d7b954_appdaemon/apps/ecos_bridge/`.
3. **Add your login to a secrets file**:
   - Make sure `appdaemon.yaml` has a secrets line at the top, e.g. `secrets: /homeassistant/secrets.yaml`.
   - Add these to that file:
     ```yaml
     ecos_email: "you@example.com"
     ecos_password: "your-ecos-app-password"
     ```
4. **Configure the app**: add the block from [`apps.yaml.example`](apps.yaml.example) to `apps/apps.yaml`. Set `datacenter` to `AU`, `EU` or `CN`, matching your ECOS account's region.
5. **Restart AppDaemon** and check its log. You should see `Logged in to ECOS`, `Found device: …` and `First ECOS payload: {…}`.

`requests` is normally already available in the AppDaemon add-on. If not, add `requests` to the add-on's **Python packages** option.

## Options

| Option | Default | Description |
|---|---|---|
| `email` / `password` | required | Your ECOS app login (use `!secret`) |
| `datacenter` | `EU` | `AU`, `EU` or `CN` |
| `url` | – | Override the API host entirely |
| `device_serial` | first device | Pick a device if your account has more than one |
| `interval` | `60` | Poll interval in seconds (minimum 30) |
| `prefix` | `ecos` | Entity ID prefix |
| `name_prefix` | `ECOS` | Friendly-name prefix |
| `log_first_payload` | `true` | Log the first raw API reply |

## Example: true battery power on an AC-coupled system

Suppose a CT clamp or Shelly measures the battery's AC connection, with negative meaning charging. That reading misses any solar going straight into the battery's MPPT input. You can get the true battery power with a template sensor:

```jinja
{% set ac = states('sensor.your_battery_ac_power') %}
{% set pv = states('sensor.ecos_solar_power') %}
{{ (ac | float - pv | float(0)) | round(0) if is_number(ac) else none }}
```

- It keeps the "negative = charging" convention.
- If ECOS is offline, it falls back to the AC reading alone.
- When the battery is full and MPPT solar passes through to the house, the two cancel to about 0.

## Example: Energy dashboard

If your Energy dashboard's battery uses an AC-side meter, add the MPPT solar like this so it isn't double-counted:

1. **Integral helper** on `sensor.ecos_solar_power`: Left Riemann sum, kilo prefix, hours. This gives `sensor.ecos_solar_energy`.
2. Add it as a **solar source**.
3. Add a **second battery system**:
   - **Energy into the battery:** `sensor.ecos_solar_energy`
   - **Energy out:** a template sensor that is always `0` (kWh, device class Energy, state class Total increasing)

This tells HA that the MPPT solar goes into the battery and later comes out through the AC-side meter you already have.

## License

GPL-3.0. See [LICENSE](LICENSE).

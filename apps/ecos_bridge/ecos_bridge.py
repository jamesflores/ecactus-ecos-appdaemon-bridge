"""
ECOS Bridge - eCactus / Weiheng ECOS cloud -> Home Assistant, via AppDaemon.

Copyright (C) 2026 James Flores

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the Free
Software Foundation, version 3.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

The ECOS API details used here (datacenter hosts, login flow, endpoints and
field names) come from ecactus-ecos-py by Germain Masse (GPL-3.0):
https://github.com/gmasse/ecactus-ecos-py

This uses an unofficial, undocumented API that may change without notice.

Logs in with your ECOS app account and, every `interval` seconds, publishes
these sensors (prefix configurable, default "ecos"):

  sensor.<prefix>_solar_power     W   PV on the battery's own MPPT input(s)
  sensor.<prefix>_battery_power   W   battery cells, positive = charging
  sensor.<prefix>_home_power      W   ECOS "home" figure (may be wrong on
                                      AC-coupled setups)
  sensor.<prefix>_meter_power     W   grid meter, negative = export
  sensor.<prefix>_grid_power      W   inverter AC side, negative = AC into
                                      the battery
  sensor.<prefix>_battery_soc     %   battery state of charge (0-100)
  sensor.<prefix>_bridge_status       "ok" or the last error, with
                                      last_update / last_update_ts attributes
"""

import json
import time

import requests
import appdaemon.plugins.hass.hassapi as hass

DATACENTERS = {
    "AU": "https://api-ecos-au.weiheng-tech.com",
    "EU": "https://api-ecos-eu.weiheng-tech.com",
    "CN": "https://api-ecos-hu.weiheng-tech.com",
}

POWER_KEYS = {
    # API field: (entity suffix, friendly name)
    "solarPower": ("solar_power", "Solar Power (MPPT)"),
    "batteryPower": ("battery_power", "Battery Power"),
    "homePower": ("home_power", "Home Power"),
    "meterPower": ("meter_power", "Meter Power"),
    "gridPower": ("grid_power", "Inverter AC Power"),
}

FAILURES_BEFORE_UNAVAILABLE = 5
FAILURES_BEFORE_RESET = 10


class EcosAuthError(Exception):
    pass


class EcosBridge(hass.Hass):
    def initialize(self):
        self.email = self.args["email"]
        self.password = self.args["password"]
        dc = str(self.args.get("datacenter", "EU")).upper()
        self.base = self.args.get("url") or DATACENTERS[dc]
        self.wanted_serial = self.args.get("device_serial")
        self.prefix = self.args.get("prefix", "ecos")
        self.name_prefix = self.args.get("name_prefix", "ECOS")
        self.interval = max(30, int(self.args.get("interval", 60)))
        self.log_first_payload = bool(self.args.get("log_first_payload", True))

        self.token = None
        self.device_id = None
        self.failures = 0
        self.logged_raw = False
        self.run_every(self.poll, "now+5", self.interval)

    # ------------------------------------------------------------ helpers
    def _entity(self, suffix):
        return f"sensor.{self.prefix}_{suffix}"

    def _call(self, method, path, payload=None, auth=True):
        headers = {"Authorization": self.token} if auth and self.token else {}
        url = f"{self.base}/{path.lstrip('/')}"
        if method == "GET":
            r = requests.get(url, params=payload or {}, headers=headers, timeout=15)
        else:
            r = requests.post(url, json=payload or {}, headers=headers, timeout=15)
        try:
            body = r.json()
        except ValueError:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code == 401 or body.get("code") == 401:
            raise EcosAuthError(body.get("message", "unauthorised"))
        if not r.ok or not body.get("success"):
            raise RuntimeError(f"API code {body.get('code')}: {body.get('message')}")
        return body.get("data")

    # ---------------------------------------------------------------- API
    def _login(self):
        data = self._call(
            "POST",
            "/api/client/guide/login",
            {
                "_t": int(time.time()),
                "clientType": "BROWSER",
                "clientVersion": "1.0",
                "email": self.email,
                "password": self.password,
            },
            auth=False,
        )
        self.token = data["accessToken"]
        self.log("Logged in to ECOS")

    def _find_device(self):
        devices = self._call("GET", "/api/client/home/device/list") or []
        for d in devices:
            self.log(
                f"Found device: {d.get('deviceAliasName')} "
                f"serial={d.get('deviceSn')} id={d.get('deviceId')}"
            )
        if not devices:
            raise RuntimeError("No devices found on this ECOS account")
        if self.wanted_serial:
            devices = [d for d in devices if d.get("deviceSn") == self.wanted_serial]
            if not devices:
                raise RuntimeError(f"No device with serial {self.wanted_serial}")
        self.device_id = devices[0]["deviceId"]
        self.log(f"Using device {devices[0].get('deviceAliasName')} ({self.device_id})")

    def _fetch(self):
        if self.token is None:
            self._login()
        if self.device_id is None:
            self._find_device()
        return self._call(
            "POST", "/api/client/home/now/device/runData", {"deviceId": self.device_id}
        )

    # --------------------------------------------------------------- poll
    def poll(self, kwargs):
        try:
            try:
                data = self._fetch()
            except EcosAuthError:
                self.token = None  # token expired: log in again once
                data = self._fetch()
        except Exception as err:  # noqa: BLE001
            self._on_failure(err)
            return

        self.failures = 0
        if self.log_first_payload and not self.logged_raw:
            self.log("First ECOS payload: " + json.dumps(data))
            self.logged_raw = True

        for key, (suffix, name) in POWER_KEYS.items():
            if data.get(key) is not None:
                self._set(self._entity(suffix), round(float(data[key])), {
                    "friendly_name": f"{self.name_prefix} {name}",
                    "unit_of_measurement": "W",
                    "device_class": "power",
                    "state_class": "measurement",
                })

        if data.get("batterySoc") is not None:
            soc = float(data["batterySoc"])
            if soc <= 1.0:  # the API sends a fraction (0.55 = 55%)
                soc *= 100
            self._set(self._entity("battery_soc"), round(soc, 1), {
                "friendly_name": f"{self.name_prefix} Battery SOC",
                "unit_of_measurement": "%",
                "device_class": "battery",
                "state_class": "measurement",
            })

        self._status("ok")

    def _on_failure(self, err):
        self.failures += 1
        self.log(f"ECOS poll failed ({self.failures}): {err}", level="WARNING")
        self._status(f"error: {err}"[:250])
        if self.failures >= FAILURES_BEFORE_UNAVAILABLE:
            for suffix, _ in POWER_KEYS.values():
                self._set(self._entity(suffix), "unavailable", {})
            self._set(self._entity("battery_soc"), "unavailable", {})
        if self.failures % FAILURES_BEFORE_RESET == 0:
            self.token, self.device_id = None, None  # start over

    def _status(self, text):
        self._set(self._entity("bridge_status"), text, {
            "friendly_name": f"{self.name_prefix} Bridge Status",
            "icon": "mdi:cloud-sync",
            "last_update": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_update_ts": int(time.time()),
        })

    def _set(self, entity, state, attrs):
        # AppDaemon leaves a falsy state (0) out of the request entirely, and
        # HA rejects a state write with no state (HTTP 400). That froze
        # solar power at dusk and meter power whenever it hit exactly 0 W.
        # HA stores every state as a string anyway, so always send a string.
        self.set_state(entity, state=str(state), attributes=attrs, replace=bool(attrs))

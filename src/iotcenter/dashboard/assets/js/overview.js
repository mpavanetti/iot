// Overview: one device at a glance.
// "Live" streams raw readings over SSE; 1h…30d ranges show per-bucket averages with
// min–max bands (served from raw readings, or from hourly aggregates for long ranges).

import { exportUrl, getJSON } from "./api.js";
import { TimeChart, breakGaps, sparkline } from "./charts.js";
import {
  METRICS, ago, bytes, clock, dateTime, duration, el, integer, number, roughly, signal, signed,
  statusBadge,
} from "./format.js";

const LIVE_WINDOW_S = 15 * 60;
const HISTORY_REFRESH_MS = 60_000;
const KPI_METRICS = ["temperature_c", "humidity_pct", "pressure_hpa", "dew_point_c"];

const CHARTS = {
  temperature: {
    unit: "°C",
    series: [
      { key: "temperature_c", label: "Air", color: "--series-1" },
      { key: "dew_point_c", label: "Dew point", color: "--series-2" },
    ],
  },
  humidity: { unit: "%", series: [{ key: "humidity_pct", label: "Humidity", color: "--series-1" }] },
  pressure: { unit: "hPa", series: [{ key: "pressure_hpa", label: "Pressure", color: "--series-1" }] },
};

const remember = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
const recall = (key) => { try { return localStorage.getItem(key); } catch { return null; } };

export class Overview {
  constructor(app) {
    this.app = app; // shared state: { info, devices }
    this.deviceId = recall("iot-device");
    this.range = recall("iot-range") || "live";
    this.live = []; // raw readings of the selected device, oldest first
    this.history = null;
    this.insights = null; // pressure tendency, sea-level pressure, comfort (api/insights)
    this.loadToken = 0;
    this.renderQueued = false;

    this.root = document.getElementById("view-overview");
    this.select = document.getElementById("device-select");
    this.rangeButtons = [...document.querySelectorAll("#range-select button")];
    this.charts = {};
    for (const figure of this.root.querySelectorAll("[data-chart]")) {
      const name = figure.dataset.chart;
      this.charts[name] = new TimeChart(figure.querySelector(".chart"), { ...CHARTS[name], digits: 1 });
      const toggle = figure.querySelector(".table-toggle");
      toggle.addEventListener("click", () => {
        const showTable = toggle.getAttribute("aria-pressed") !== "true";
        toggle.setAttribute("aria-pressed", String(showTable));
        figure.querySelector(".chart").hidden = showTable;
        figure.querySelector(".chart-table").hidden = !showTable;
        this.render();
      });
    }

    this.select.addEventListener("change", () => this.selectDevice(this.select.value));
    for (const button of this.rangeButtons) {
      button.addEventListener("click", () => this.setRange(button.dataset.range));
    }
    setInterval(() => this.range !== "live" && !document.hidden && this.load(), HISTORY_REFRESH_MS);
    setInterval(() => this.range === "live" && !document.hidden && this.loadInsights(), HISTORY_REFRESH_MS);
    this.markRange();
  }

  get device() {
    return this.app.devices.find((d) => d.device_id === this.deviceId);
  }

  // --- state changes --------------------------------------------------------------------

  setDevices(devices) {
    const options = devices.map((d) =>
      el("option", { value: d.device_id }, `${d.name || d.device_id}${d.online ? "" : " (offline)"}`),
    );
    this.select.replaceChildren(...options);
    this.select.disabled = devices.length === 0;
    document.getElementById("empty-state").hidden = devices.length > 0;
    document.getElementById("overview-content").hidden = devices.length === 0;

    if (!devices.length) return;
    if (!this.device) {
      const pick = devices.find((d) => d.online) || devices[devices.length - 1];
      this.selectDevice(pick.device_id);
    } else {
      this.select.value = this.deviceId;
      if (!this.loadToken) this.load(); // first time: the device came from saved state
    }
  }

  selectDevice(deviceId) {
    this.deviceId = deviceId;
    this.select.value = deviceId;
    remember("iot-device", deviceId);
    this.live = [];
    this.history = null;
    this.insights = null;
    this.load();
  }

  setRange(range) {
    this.range = range;
    remember("iot-range", range);
    this.markRange();
    this.load();
  }

  markRange() {
    for (const button of this.rangeButtons) {
      button.setAttribute("aria-checked", String(button.dataset.range === this.range));
    }
  }

  async load() {
    if (!this.deviceId) return;
    const token = ++this.loadToken; // ignore responses that arrive after a newer request
    const deviceId = this.deviceId;
    const range = this.range;
    this.root.classList.add("loading"); // keep the previous frame visible while fetching
    try {
      const [recent, history, insights] = await Promise.all([
        getJSON("api/readings/recent", { device_id: deviceId, minutes: LIVE_WINDOW_S / 60 }),
        range === "live" ? null : getJSON("api/readings/history", { device_id: deviceId, range }),
        getJSON("api/insights", { device_id: deviceId }),
      ]);
      if (token !== this.loadToken) return;
      this.live = recent.readings;
      this.history = history;
      this.insights = insights;
    } catch (error) {
      console.error(error);
    } finally {
      if (token === this.loadToken) this.root.classList.remove("loading");
    }
    this.render();
  }

  async loadInsights() {
    const deviceId = this.deviceId;
    if (!deviceId) return; // no board yet
    try {
      const insights = await getJSON("api/insights", { device_id: deviceId });
      if (deviceId !== this.deviceId) return;
      this.insights = insights;
      this.queueRender();
    } catch (error) {
      console.error(error);
    }
  }

  onReading(reading) {
    if (reading.device_id !== this.deviceId) return;
    const newest = this.live.length ? this.live[this.live.length - 1].event_time : 0;
    const cutoff = Math.max(Date.now() / 1000, newest) - LIVE_WINDOW_S;
    if (reading.event_time < cutoff) return; // a backfilled/old reading: not "live"
    if (reading.event_time >= newest) this.live.push(reading);
    else this.live.splice(this.live.findIndex((r) => r.event_time > reading.event_time), 0, reading);
    while (this.live.length && this.live[0].event_time < cutoff) this.live.shift();
    this.queueRender();
  }

  queueRender() {
    if (this.renderQueued) return;
    this.renderQueued = true;
    requestAnimationFrame(() => {
      this.renderQueued = false;
      this.render();
    });
  }

  // --- rendering ------------------------------------------------------------------------

  render() {
    if (!this.device || this.root.hidden) return;
    document.getElementById("export-csv").href = exportUrl(
      this.deviceId,
      this.range === "live" ? "1h" : this.range,
    );
    this.renderKpis();
    this.renderCharts();
    this.renderDeviceCard();
    this.renderLatestTable();
    this.tick();
  }

  tick() {
    const device = this.device;
    if (!device) return;
    document.getElementById("updated").textContent = `Last reading ${ago(device.last_seen)}`;
  }

  latest() {
    const device = this.device;
    const streamed = this.live[this.live.length - 1];
    if (!device?.latest) return streamed;
    if (!streamed) return device.latest;
    return streamed.event_time >= device.latest.event_time ? streamed : device.latest;
  }

  renderKpis() {
    const latest = this.latest();
    const tiles = KPI_METRICS.map((metric) => {
      const meta = METRICS[metric];
      const value = latest?.[metric];
      let trend = [];
      let delta = null;
      let deltaLabel = "";

      if (this.range === "live") {
        trend = this.live.map((r) => r[metric]);
        const reference = this.live.find((r) => r.event_time >= (latest?.event_time ?? 0) - 600);
        if (reference && latest && latest.event_time - reference.event_time >= 60) {
          delta = latest[metric] - reference[metric];
          deltaLabel = `vs ${roughly(latest.event_time - reference.event_time)} ago`;
        }
      } else if (this.history) {
        trend = this.history[metric].avg;
        const first = trend.find((v) => v != null);
        if (first != null && value != null) {
          delta = value - first;
          deltaLabel = `vs ${this.range} ago`;
        }
      }

      return el(
        "article",
        { class: "card kpi", "aria-label": meta.label },
        el("span", { class: "kpi-label" }, meta.label),
        el("span", { class: "kpi-value" }, number(value, meta.digits), el("span", { class: "unit" }, meta.unit)),
        el("span", { class: "kpi-delta" }, delta == null ? "" : `${signed(delta, meta.digits)} ${meta.unit} ${deltaLabel}`),
        sparkline(sample(trend, 90)),
        ...kpiNotes(metric, this.insights).map((note) => el("span", { class: "kpi-note" }, note)),
      );
    });
    document.getElementById("kpis").replaceChildren(...tiles);
  }

  renderCharts() {
    for (const [name, chart] of Object.entries(this.charts)) {
      const spec = CHARTS[name];
      const figure = chart.container.closest("figure");
      const tableView = figure.querySelector(".chart-table");
      if (this.range === "live") {
        const live = this.live;
        const times = live.map((r) => r.event_time);
        const columns = spec.series.map((s) => live.map((r) => r[s.key]));
        const now = Math.max(Date.now() / 1000, times[times.length - 1] ?? 0);
        const data = breakGaps([now - LIVE_WINDOW_S, ...times], columns.map((c) => [null, ...c]), gapThreshold(times));
        chart.setLive(data);
        figure.querySelector(".chart").setAttribute("aria-label", describe(spec, live[live.length - 1], "now"));
        if (!tableView.hidden) tableView.replaceChildren(liveTable(spec, live));
      } else if (this.history) {
        const data = bandData(this.history, spec);
        chart.setBands(data);
        figure.querySelector(".chart").setAttribute("aria-label", `${spec.series.map((s) => s.label).join(" and ")}, ${this.range} history`);
        if (!tableView.hidden) tableView.replaceChildren(historyTable(spec, this.history));
      }
    }
  }

  renderDeviceCard() {
    const device = this.device;
    const latest = this.latest() || {};
    const used = latest.mem_alloc_bytes;
    const total = used != null && latest.mem_free_bytes != null ? used + latest.mem_free_bytes : null;
    const memoryPct = total ? (100 * used) / total : null;
    const wifi = signal(latest.wifi_rssi_dbm);
    const lossPct = device.messages ? (100 * (device.dropped || 0)) / (device.messages + (device.dropped || 0)) : 0;

    const facts = [
      ["IP address", latest.ip || (latest.source === "usb" ? "none: Wi-Fi off" : device.ip) || "–"],
      ["Connection", sourceName(device.source)],
      ["Firmware", latest.firmware || device.firmware || "–"],
      ["Uptime", duration(latest.uptime_s)],
      ["Last start", latest.boot_reason || "–"],
      ["Longest loop pass", latest.loop_max_ms == null ? "–" : `${integer(latest.loop_max_ms)} ms`],
      ["Sensor errors", latest.sensor_errors == null ? "–" : `${integer(latest.sensor_errors)} since boot`],
      ["Board temperature", latest.cpu_temp_c == null ? "–" : `${number(latest.cpu_temp_c, 1)} °C`],
      ["CPU frequency", latest.cpu_freq_mhz == null ? "–" : `${integer(latest.cpu_freq_mhz)} MHz`],
      ["Free storage", latest.storage_free_kb == null ? "–" : bytes(latest.storage_free_kb * 1024)],
      ["Messages", integer(device.messages)],
      ["Lost in transit", device.dropped == null ? "–" : `${integer(device.dropped)} (${number(lossPct, 1)}%)`],
      ["Restarts seen", device.restarts == null ? "–" : integer(device.restarts)],
      ["First seen", dateTime(device.first_seen)],
    ];

    document.getElementById("device-card").replaceChildren(
      el(
        "header",
        { class: "device-head" },
        el("div", {}, el("h2", { id: "device-card-title" }, device.name || device.device_id), el("p", { class: "subtitle" }, device.device_id)),
        statusBadge(device.online ? "online" : "offline"),
      ),
      el(
        "div",
        { class: "meters" },
        meter("Memory used", memoryPct, total ? `${bytes(used)} of ${bytes(total)}` : "–", memoryPct > 90 ? "critical" : memoryPct > 80 ? "warning" : ""),
        meter("CPU busy", latest.cpu_busy_pct, latest.cpu_busy_pct == null ? "–" : `${number(latest.cpu_busy_pct, 1)} % of one core`, latest.cpu_busy_pct > 90 ? "critical" : latest.cpu_busy_pct > 70 ? "warning" : ""),
        meter("Wi-Fi signal", wifi.pct, latest.wifi_rssi_dbm != null ? `${wifi.word} · ${latest.wifi_rssi_dbm} dBm` : latest.source === "usb" ? "off (on USB)" : "–", wifi.pct != null && wifi.pct < 40 ? "warning" : ""),
      ),
      el("dl", { class: "facts" }, facts.flatMap(([label, value]) => [el("dt", {}, label), el("dd", {}, value)])),
    );
  }

  renderLatestTable() {
    const rows = this.live.slice(-20).reverse();
    const head = ["Time", "Temperature", "Humidity", "Pressure", "Dew point", "Seq"];
    const body = rows.map((r) =>
      el(
        "tr",
        {},
        el("td", {}, clock(r.event_time)),
        el("td", {}, `${number(r.temperature_c, 2)} °C`),
        el("td", {}, `${number(r.humidity_pct, 1)} %`),
        el("td", {}, `${number(r.pressure_hpa, 2)} hPa`),
        el("td", {}, `${number(r.dew_point_c, 1)} °C`),
        el("td", {}, integer(r.seq)),
      ),
    );
    const empty = el("tr", {}, el("td", { colspan: head.length, class: "text" }, el("span", { class: "muted" }, "No readings in the last 15 minutes.")));
    document.getElementById("latest-table").replaceChildren(table(head, body.length ? body : [empty]));
  }
}

// --- helpers ----------------------------------------------------------------------------

function meter(label, pct, text, level = "") {
  return el(
    "div",
    { class: "meter" },
    el("div", { class: "meter-label" }, el("span", {}, label), el("strong", {}, text)),
    el(
      "div",
      { class: "meter-track", role: "meter", "aria-label": label, "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct == null ? null : Math.round(pct) },
      el("div", { class: `meter-fill ${level}`, style: `width: ${pct == null ? 0 : Math.max(2, pct)}%` }),
    ),
  );
}

export function table(head, rows) {
  return el(
    "table",
    {},
    el("thead", {}, el("tr", {}, head.map((h, i) => el("th", { class: i === 0 ? "text" : null, scope: "col" }, h)))),
    el("tbody", {}, rows),
  );
}

function liveTable(spec, live) {
  const head = ["Time", ...spec.series.map((s) => `${s.label} (${spec.unit})`)];
  const rows = live.slice(-60).reverse().map((r) =>
    el("tr", {}, el("td", {}, clock(r.event_time)), spec.series.map((s) => el("td", {}, number(r[s.key], 2)))),
  );
  return table(head, rows);
}

function historyTable(spec, history) {
  const head = ["Period", "Samples"];
  for (const s of spec.series) head.push(`${s.label} avg`, "min", "max");
  const rows = history.t.map((t, i) =>
    el(
      "tr",
      {},
      el("td", {}, dateTime(t)),
      el("td", {}, integer(history.samples[i])),
      spec.series.flatMap((s) => ["avg", "min", "max"].map((stat) => el("td", {}, number(history[s.key][stat][i], 1)))),
    ),
  );
  return table(head, rows.reverse());
}

// History buckets → uPlot columns. Lines break only at real outages (3+ empty buckets), so
// a board reporting less often than the bucket size still reads as a line, and the axis
// always spans the whole range.
function bandData(history, spec) {
  const columns = spec.series.flatMap((s) => ["avg", "min", "max"].map((stat) => history[s.key][stat]));
  const [times, ...values] = breakGaps(history.t, columns, 3 * history.bucket_s);
  return [[history.start, ...times, history.end], ...values.map((v) => [null, ...v, null])];
}

// Readings arrive every few seconds; a pause much longer than usual is a gap, not a line.
function gapThreshold(times) {
  if (times.length < 3) return 60;
  const steps = times.slice(1).map((t, i) => t - times[i]).sort((a, b) => a - b);
  return Math.max(30, 5 * steps[Math.floor(steps.length / 2)]);
}

function sample(values, max) {
  if (values.length <= max) return values;
  const step = values.length / max;
  return Array.from({ length: max }, (_, i) => values[Math.floor(i * step)]).concat(values[values.length - 1]);
}

function describe(spec, latest, when) {
  const parts = spec.series.map((s) => `${s.label} ${latest ? number(latest[s.key], 1) : "–"} ${spec.unit}`);
  return `${parts.join(", ")} ${when}; last 15 minutes`;
}

/** Extra lines under a KPI tile, from api/insights. */
function kpiNotes(metric, insights) {
  if (!insights) return [];
  if (metric === "humidity_pct" && insights.comfort) {
    return [`${insights.comfort.label} indoors (${insights.comfort.range})`];
  }
  if (metric !== "pressure_hpa") return [];
  const notes = [];
  const tendency = insights.pressure_tendency;
  if (tendency) {
    const trend = tendency.trend[0].toUpperCase() + tendency.trend.slice(1);
    notes.push(`${trend}, ${signed(tendency.change_hpa, 1)} hPa in ${tendency.hours} h: ${tendency.outlook}`);
  } else {
    notes.push("Trend shown after 3 hours of data");
  }
  if (insights.sea_level_pressure_hpa != null) {
    notes.push(`${number(insights.sea_level_pressure_hpa, 1)} hPa at sea level (${integer(insights.altitude_m)} m)`);
  }
  return notes;
}

function sourceName(source) {
  return { tcp: "Wi-Fi (TCP)", usb: "USB serial", simulator: "Simulator → Kafka" }[source] || source || "–";
}

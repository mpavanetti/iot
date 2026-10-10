// Pipeline: the stages a reading passes through, each with its live health and counters.
// The server lists the components in data-flow order, so this page doubles as a live
// architecture diagram of whichever edition is running.

import { getJSON } from "./api.js";
import { ago, bytes, dateTime, duration, el, icon, integer, number, statusBadge } from "./format.js";

const REFRESH_MS = 5000;

export class Pipeline {
  constructor() {
    this.timer = null;
  }

  start() {
    if (this.timer) return;
    this.refresh();
    this.timer = setInterval(() => !document.hidden && this.refresh(), REFRESH_MS);
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
  }

  async refresh() {
    let status;
    try {
      status = await getJSON("api/status");
    } catch (error) {
      console.error(error);
      return;
    }
    document.getElementById("flow").replaceChildren(...status.components.map(step));
    renderHost(status.host);
    renderStorage(status.storage);
  }
}

function step(component, index) {
  const metrics = Object.entries(component.metrics || {});
  return el(
    "li",
    { class: "card flow-step" },
    el("span", { class: "step-no" }, `Step ${index + 1}`),
    statusBadge(component.status),
    el("h2", {}, component.name),
    el("p", { class: "role" }, component.role),
    component.detail ? el("p", { class: "detail" }, component.detail) : null,
    component.note ? el("p", { class: "note" }, component.note) : null,
    metrics.length
      ? el("dl", {}, metrics.flatMap(([key, value]) => [el("dt", {}, key), el("dd", {}, metric(key, value))]))
      : null,
    component.last_error && component.status !== "up" ? el("p", { class: "error" }, component.last_error) : null,
    (component.links || []).map((link) =>
      el("a", { href: hostUrl(link), target: "_blank", rel: "noopener" }, `${link.label} `, icon("external")),
    ),
  );
}

function metric(key, value) {
  if (value === null || value === undefined) return "–";
  if (typeof value === "string") return value;
  if (/\b(size|bytes)\b/.test(key)) return bytes(value);
  if (/\b(lag|age)\b/.test(key)) return duration(value);
  return Number.isInteger(value) ? integer(value) : number(value, 1);
}

function renderHost(host) {
  const card = document.getElementById("host-card");
  if (!host) return card.replaceChildren();
  card.replaceChildren(
    el("header", {}, el("h2", { id: "host-title" }, "Host"), el("p", { class: "subtitle" }, `${host.hostname} · ${host.platform}`)),
    el(
      "div",
      { class: "meters" },
      gauge("CPU", host.cpu_pct, `${number(host.cpu_pct, 0)}% of ${host.cpu_count} cores`),
      gauge("Memory", host.mem_pct, `${bytes(host.mem_used_bytes)} of ${bytes(host.mem_total_bytes)}`),
      gauge("Disk", host.disk_pct, `${bytes(host.disk_used_bytes)} of ${bytes(host.disk_total_bytes)}`),
    ),
    el(
      "dl",
      { class: "facts" },
      el("dt", {}, "CPU temperature"), el("dd", {}, host.cpu_temp_c == null ? "not exposed" : `${number(host.cpu_temp_c, 1)} °C`),
      el("dt", {}, "Load (1 min)"), el("dd", {}, host.load_1m == null ? "–" : number(host.load_1m, 2)),
      el("dt", {}, "Uptime"), el("dd", {}, duration(host.uptime_s)),
      el("dt", {}, "Python"), el("dd", {}, host.python),
    ),
  );
}

function renderStorage(storage) {
  const card = document.getElementById("storage-card");
  if (!storage) return card.replaceChildren();
  const facts = [
    ["Messages stored", integer(storage.messages)],
    ["Devices", integer(storage.devices)],
    ["Hourly aggregates", `${integer(storage.hourly_rows)} rows`],
    ["Oldest reading", storage.oldest ? `${dateTime(storage.oldest)} (${ago(storage.oldest)})` : "–"],
    ["Newest reading", storage.newest ? `${dateTime(storage.newest)} (${ago(storage.newest)})` : "–"],
    ["Size on disk", bytes(storage.size_bytes)],
    ...forecastFacts(storage.forecast),
  ];
  card.replaceChildren(
    el("header", {}, el("h2", { id: "storage-title" }, storage.title || "Storage"), el("p", { class: "subtitle" }, storage.subtitle || "Where history lives")),
    el("dl", { class: "facts" }, facts.flatMap(([label, value]) => [el("dt", {}, label), el("dd", {}, value)])),
  );
}

/** The retention policy, and how big the database gets at the pace of the last hour (Lite). */
function forecastFacts(forecast) {
  if (!forecast) return [];
  const day = (epoch) => new Date(epoch * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  const kept = (days) => (days <= 0 ? "forever" : days % 365 === 0 ? `${days / 365} year${days === 365 ? "" : "s"}` : `${days} days`);
  const facts = [
    ["Kept", `raw readings ${kept(forecast.retention_days)}, hourly averages ${kept(forecast.hourly_retention_days)}`],
    [
      "Growing",
      forecast.readings_per_day
        ? `${bytes(forecast.growth_bytes_per_day)} a day (${integer(forecast.readings_per_day)} readings)`
        : "not now: no readings in the last hour",
    ],
  ];
  if (forecast.levels_off_bytes != null) {
    facts.push([
      "Levels off at",
      `about ${bytes(forecast.levels_off_bytes)}: ${bytes(forecast.raw_bytes)} of raw readings (full by ${day(forecast.raw_full_at)}) and ${bytes(forecast.hourly_bytes)} of hourly averages`,
    ]);
  } else {
    facts.push(["Levels off", "never: a retention of 0 keeps that data forever"]);
  }
  return facts;
}

function gauge(label, pct, text) {
  const level = pct > 90 ? "critical" : pct > 75 ? "warning" : "";
  return el(
    "div",
    { class: "meter" },
    el("div", { class: "meter-label" }, el("span", {}, label), el("strong", {}, text)),
    el(
      "div",
      { class: "meter-track", role: "meter", "aria-label": label, "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(pct ?? 0) },
      el("div", { class: `meter-fill ${level}`, style: `width: ${Math.max(2, pct ?? 0)}%` }),
    ),
  );
}

// Links to other UIs are host-relative ({port, path}): the browser knows the right host name.
export function hostUrl(link) {
  return `${location.protocol}//${location.hostname}:${link.port}${link.path || "/"}`;
}

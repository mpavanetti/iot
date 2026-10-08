// Devices: every board that has ever sent data, newest reading per board.

import { ago, duration, el, integer, number, signal, statusBadge } from "./format.js";
import { table } from "./overview.js";

const HEAD = ["Device", "Status", "Last seen", "Temperature", "Humidity", "Pressure", "Wi-Fi",
  "Uptime", "Messages", "Lost", "Connection", "Firmware"];

export function renderDevices(devices, onOpen) {
  const container = document.getElementById("devices-table");
  if (!devices.length) {
    container.replaceChildren(el("p", { class: "subtitle", style: "padding: 16px" }, "No devices yet."));
    return;
  }
  const rows = devices.map((d) => {
    const r = d.latest || {};
    const wifi = signal(r.wifi_rssi_dbm);
    const open = () => onOpen(d.device_id);
    return el(
      "tr",
      {
        class: "clickable",
        tabindex: 0,
        "aria-label": `Open ${d.name || d.device_id}`,
        onclick: open,
        onkeydown: (event) => (event.key === "Enter" || event.key === " ") && (event.preventDefault(), open()),
      },
      el("td", { class: "text" }, el("strong", {}, d.name || d.device_id), el("div", { class: "muted" }, d.device_id)),
      el("td", { class: "text" }, statusBadge(d.online ? "online" : "offline")),
      el("td", { "data-ago": d.last_seen }, ago(d.last_seen)),
      el("td", {}, r.temperature_c == null ? "–" : `${number(r.temperature_c, 1)} °C`),
      el("td", {}, r.humidity_pct == null ? "–" : `${number(r.humidity_pct, 1)} %`),
      el("td", {}, r.pressure_hpa == null ? "–" : `${number(r.pressure_hpa, 1)} hPa`),
      el("td", {}, r.wifi_rssi_dbm == null ? "–" : `${wifi.word} (${r.wifi_rssi_dbm} dBm)`),
      el("td", {}, duration(r.uptime_s)),
      el("td", {}, integer(d.messages)),
      el("td", {}, d.dropped == null ? "–" : integer(d.dropped)),
      el("td", { class: "text" }, d.source || "–"),
      el("td", { class: "text" }, r.firmware || d.firmware || "–"),
    );
  });
  container.replaceChildren(table(HEAD, rows));
}

// Refresh the "x s ago" cells without rebuilding the table.
export function tickDevices() {
  for (const cell of document.querySelectorAll("#devices-table [data-ago]")) {
    cell.textContent = ago(Number(cell.dataset.ago));
  }
}

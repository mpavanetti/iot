// IoT Center dashboard: boot, routing, the live stream and the theme toggle.
// Works unchanged on both editions; /api/info says which one is serving it.

import { getJSON, openLiveStream } from "./api.js";
import { renderDevices, tickDevices } from "./devices.js";
import { el, icon } from "./format.js";
import { Overview } from "./overview.js";
import { Pipeline, hostUrl } from "./pipeline.js";

const VIEWS = ["overview", "devices", "pipeline"];
const app = { info: null, devices: [] };
let overview;
let pipeline;
let view = "overview";
let devicesRefresh = null;

async function boot() {
  setupTheme();
  app.info = await retry(() => getJSON("api/info"));
  renderChrome(app.info);

  overview = new Overview(app);
  pipeline = new Pipeline();
  await refreshDevices();
  route();
  window.addEventListener("hashchange", route);

  openLiveStream({
    onReading,
    onState: setConnection,
    onReconnect: () => {
      refreshDevices();
      overview.load();
    },
  });
  setInterval(refreshDevices, 15_000);
  setInterval(tick, 1_000);
}

async function retry(task) {
  for (;;) {
    try {
      return await task();
    } catch (error) {
      console.error(error);
      setConnection("offline");
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
  }
}

function renderChrome(info) {
  const edition = document.getElementById("edition");
  edition.textContent = info.edition === "lite" ? "Lite" : "Platform";
  edition.hidden = false;
  document.title = `IoT Center ${edition.textContent}`;
  document.getElementById("version").textContent = `IoT Center ${info.version} · ${edition.textContent}`;
  const port = info.ingest_port ?? 1500;
  document.getElementById("ingest-address").textContent = `tcp://${location.hostname}:${port}`;
  document.getElementById("external-links").replaceChildren(
    ...info.links.map((link) =>
      el("a", { href: hostUrl(link), target: "_blank", rel: "noopener", title: link.title }, link.label, icon("external")),
    ),
  );
  document.getElementById("pipeline-subtitle").textContent =
    info.edition === "lite"
      ? "Lite edition: one process receives, validates, stores and serves every reading."
      : "Platform edition: gateway → Kafka → Spark Structured Streaming → PostgreSQL → dashboards.";
}

// --- data -------------------------------------------------------------------------------

async function refreshDevices() {
  try {
    app.devices = await getJSON("api/devices");
  } catch (error) {
    console.error(error);
    return;
  }
  overview.setDevices(app.devices);
  if (view === "devices") renderDevices(app.devices, openDevice);
}

function onReading(reading) {
  const device = app.devices.find((d) => d.device_id === reading.device_id);
  if (!device) {
    // A board we have not seen before: fetch the registry (once, not per reading).
    devicesRefresh ??= setTimeout(() => {
      devicesRefresh = null;
      refreshDevices();
    }, 500);
    return;
  }
  device.last_seen = Math.max(device.last_seen || 0, reading.received_at);
  device.messages = (device.messages || 0) + 1;
  if (!device.latest || reading.event_time >= device.latest.event_time) device.latest = reading;
  if (!device.online) {
    device.online = true;
    overview.setDevices(app.devices);
  }
  overview.onReading(reading);
}

// Every second: relative times, and online -> offline when a board goes quiet.
function tick() {
  const now = Date.now() / 1000;
  let changed = false;
  for (const device of app.devices) {
    const online = device.last_seen != null && now - device.last_seen <= app.info.offline_after_s;
    if (online !== device.online) {
      device.online = online;
      changed = true;
    }
  }
  if (changed) {
    overview.setDevices(app.devices);
    overview.render();
    if (view === "devices") renderDevices(app.devices, openDevice);
  }
  if (view === "overview") overview.tick();
  if (view === "devices") tickDevices();
}

function openDevice(deviceId) {
  overview.selectDevice(deviceId);
  location.hash = "#overview";
}

// --- chrome -----------------------------------------------------------------------------

function route() {
  const requested = location.hash.replace("#", "");
  view = VIEWS.includes(requested) ? requested : "overview";
  for (const name of VIEWS) document.getElementById(`view-${name}`).hidden = name !== view;
  for (const tab of document.querySelectorAll(".tabs [data-view]")) {
    if (tab.dataset.view === view) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  if (view === "pipeline") pipeline.start();
  else pipeline.stop();
  if (view === "devices") renderDevices(app.devices, openDevice);
  if (view === "overview") overview.render();
}

function setConnection(state) {
  const pill = document.getElementById("connection");
  pill.dataset.state = state;
  pill.querySelector(".label").textContent =
    { live: "Live", connecting: "Connecting…", offline: "Server unreachable" }[state];
}

function setupTheme() {
  const root = document.documentElement;
  const button = document.getElementById("theme-toggle");
  const order = ["auto", "light", "dark"];
  let theme = root.dataset.theme || "auto";

  const label = () => {
    button.title = `Theme: ${theme}`;
    button.setAttribute("aria-label", `Switch theme (currently ${theme})`);
  };
  const redraw = () => overview && Object.values(overview.charts).forEach((chart) => chart.rebuild());

  button.addEventListener("click", () => {
    theme = order[(order.indexOf(theme) + 1) % order.length];
    if (theme === "auto") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try {
      if (theme === "auto") localStorage.removeItem("iot-theme");
      else localStorage.setItem("iot-theme", theme);
    } catch {}
    label();
    redraw(); // canvas colors are read from CSS tokens at build time
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => theme === "auto" && redraw());
  label();
}

boot();

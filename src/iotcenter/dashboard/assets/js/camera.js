// Camera: the live picture at the camera's full quality, and what the camera notices.
// The picture is an MJPEG stream in an <img>: the camera's own JPEG frames, untouched. It
// streams only while this view is open and the browser tab visible, so an idle dashboard
// costs no bandwidth. Motion, light and zones arrive as `camera` events, and what the
// microphone hears as `sound` events, on the dashboard's one live stream (main.js), which
// keeps a browser to two connections per tab (three while listening).

import { getJSON } from "./api.js";
import { ago, bytes, clock, dateTime, duration, el, integer, number } from "./format.js";
import { Recordings } from "./recordings.js";
import { ZonesEditor } from "./zones.js";

const RATES = ["full", "15", "5", "1"];
const STATUS_MS = 2000;
const RETRY_MS = 3000;
const WINDOW_S = 600; // the motion and light charts: the last 10 minutes
const GAP_S = 5; // no sample for this long breaks the line (camera unplugged, restart)
const SVG = "http://www.w3.org/2000/svg";
const tooltip = document.getElementById("tooltip");

const remember = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
const recall = (key) => { try { return localStorage.getItem(key); } catch { return null; } };

export class CameraView {
  constructor(camera, { microphone = false } = {}) {
    this.name = camera.name;
    this.microphone = microphone;
    this.sound = null; // api/sound
    this.latestSound = null;
    this.soundHistory = { t: [], level_db: [] };
    this.soundEvents = [];
    this.audio = null; // the Listen player
    const rate = recall("iot-camera-rate");
    this.rate = RATES.includes(rate) ? rate : "full";
    this.showBoxes = recall("iot-camera-boxes") !== "off";
    this.active = false;
    this.failed = false; // the picture stream broke; reconnect once the camera is back
    this.status = null; // api/camera, refreshed every 2 s while open
    this.latest = null; // the newest camera event
    this.history = { t: [], motion_pct: [], brightness_pct: [] };
    this.events = [];
    this.timer = null;
    this.retry = null;
    this.renderedAt = 0;

    this.stage = document.getElementById("camera-stage");
    this.frame = document.getElementById("camera-frame");
    this.image = document.getElementById("camera-image");
    this.boxes = document.getElementById("camera-boxes-layer");
    this.message = document.getElementById("camera-message");
    this.clock = document.getElementById("camera-clock");
    this.livePill = document.getElementById("camera-live");
    this.movingPill = document.getElementById("camera-moving");
    this.rateButtons = [...document.querySelectorAll("#camera-rate button")];
    this.boxesButton = document.getElementById("camera-boxes");
    this.zonesButton = document.getElementById("camera-show-zones");
    this.listenButton = document.getElementById("camera-listen");
    this.fullscreenButton = document.getElementById("camera-fullscreen");
    this.zones = new ZonesEditor({ frame: this.frame, onChange: () => this.refresh() });
    this.recordings = camera.recording ? new Recordings() : null;
    this.recPill = document.getElementById("camera-rec");
    if (camera.recording) {
      document.getElementById("camera-subtitle").textContent = "Live, at the camera's own resolution and quality. Each motion is recorded below.";
    }
    this.zones.setVisible(recall("iot-camera-zones") !== "off");

    document.getElementById("camera-title").textContent = this.name;
    this.image.alt = `Live picture from ${this.name}`;
    this.image.addEventListener("load", () => this.onPicture());
    this.image.addEventListener("error", () => this.onPictureError());
    for (const button of this.rateButtons) {
      button.addEventListener("click", () => this.setRate(button.dataset.rate));
    }
    this.boxesButton.addEventListener("click", () => this.toggleBoxes());
    this.zonesButton.addEventListener("click", () => this.toggleZones());
    this.listenButton.hidden = !microphone;
    this.listenButton.addEventListener("click", () => this.toggleListening());
    document.getElementById("camera-sound").hidden = !microphone;
    document.getElementById("camera-snapshot").addEventListener("click", (event) => this.snapshot(event.currentTarget));
    this.fullscreenButton.addEventListener("click", () => this.toggleFullscreen());
    this.frame.addEventListener("dblclick", () => !this.zones.drawing && this.toggleFullscreen());
    document.addEventListener("fullscreenchange", () => this.markFullscreen());
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this.stage.classList.contains("expanded")) this.toggleFullscreen();
    });

    this.panels = buildPanels();
    this.markControls();
  }

  // --- lifecycle (main.js: when the view opens/closes, the tab hides, the stream returns) --

  start() {
    if (this.active) return;
    this.active = true;
    this.connect();
    this.refresh();
    this.loadHistory();
    this.timer = setInterval(() => this.refresh(), STATUS_MS);
    this.recordings?.start();
    this.tick();
  }

  stop() {
    if (!this.active) return;
    this.active = false;
    clearInterval(this.timer);
    clearTimeout(this.retry);
    this.image.removeAttribute("src"); // closes the stream
    this.boxes.replaceChildren();
    this.stopListening();
    this.zones.cancel();
    this.recordings?.stop();
    if (document.fullscreenElement === this.stage) document.exitFullscreen();
    this.stage.classList.remove("expanded");
  }

  onReconnect() {
    // The live stream came back, so the server may have restarted: so must the picture.
    if (!this.active) return;
    this.connect();
    this.loadHistory();
  }

  connect() {
    clearTimeout(this.retry);
    const url = new URL("api/camera/stream.mjpg", document.baseURI);
    if (this.rate !== "full") url.searchParams.set("fps", this.rate);
    url.searchParams.set("t", Date.now()); // always a fresh request
    this.failed = false;
    this.image.src = url.href;
  }

  onPicture() {
    this.failed = false;
    this.renderMessage();
  }

  onPictureError() {
    if (!this.active || !this.image.getAttribute("src")) return;
    this.failed = true;
    this.renderMessage();
    clearTimeout(this.retry);
    this.retry = setTimeout(() => this.active && this.connect(), RETRY_MS);
  }

  // --- data -----------------------------------------------------------------------------

  async refresh() {
    let status;
    try {
      status = await getJSON("api/camera");
    } catch (error) {
      console.error(error);
      return;
    }
    if (this.microphone) {
      try {
        this.sound = await getJSON("api/sound");
        this.soundEvents = this.sound.events;
      } catch (error) {
        console.error(error);
      }
    }
    if (!this.active) return;
    this.status = status;
    if (status.activity) {
      this.events = status.activity.events;
      this.zones.setZones(status.activity.zones);
    }
    if (status.width && status.height) this.frame.style.setProperty("--aspect", (status.width / status.height).toFixed(4));
    if (status.state === "streaming" && this.failed) this.connect();
    const recording = Boolean(status.recording?.recording);
    if (!this.recPill.hidden && !recording) this.recordings?.load(); // a clip just ended
    this.recPill.hidden = !recording;
    this.render();
  }

  async loadHistory() {
    try {
      const history = await getJSON("api/camera/activity");
      this.history = { t: history.t, motion_pct: history.motion_pct, brightness_pct: history.brightness_pct };
      this.events = history.events;
      if (this.microphone) {
        const sound = await getJSON("api/sound/activity");
        this.soundHistory = { t: sound.t, level_db: sound.level_db };
        this.soundEvents = sound.events;
      }
    } catch (error) {
      console.error(error); // activity detection may be off: the picture still works
      return;
    }
    if (this.active) this.render();
  }

  /** Every sound event (4 a second): the level, and any alarm. */
  onSound(sample) {
    this.latestSound = sample;
    appendPeak(this.soundHistory, sample.t, { level_db: sample.level_db });
    if (this.active && performance.now() - this.renderedAt > 1000) this.render();
  }

  /** Every camera event (5 a second), whichever view is open: cheap bookkeeping only. */
  onActivity(sample) {
    this.latest = sample;
    appendPeak(this.history, sample.t, { motion_pct: sample.motion_pct, brightness_pct: sample.brightness_pct });
    if (!this.active) return;
    this.drawBoxes(sample);
    this.zones.live(sample.zones);
    this.movingPill.hidden = !sample.moving;
    if (performance.now() - this.renderedAt > 1000) this.render();
  }

  // --- controls -------------------------------------------------------------------------

  setRate(rate) {
    this.rate = rate;
    remember("iot-camera-rate", rate);
    this.markControls();
    if (this.active) this.connect();
  }

  toggleBoxes() {
    this.showBoxes = !this.showBoxes;
    remember("iot-camera-boxes", this.showBoxes ? "on" : "off");
    this.markControls();
    if (!this.showBoxes) this.boxes.replaceChildren();
    const moving = this.latest?.moving;
    this.toast(this.showBoxes ? `Motion boxes on${moving ? "" : ": drawn when something moves"}` : "Motion boxes off");
  }

  /** A short note on the picture, e.g. what a toggle just did. */
  toast(text) {
    const note = document.getElementById("camera-toast");
    note.textContent = text;
    note.hidden = false;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => (note.hidden = true), 2200);
  }

  toggleZones() {
    this.zones.setVisible(!this.zones.visible);
    remember("iot-camera-zones", this.zones.visible ? "on" : "off");
    this.markControls();
    this.toast(this.zones.visible ? "Zone outlines on" : "Zone outlines off (the zones are still watched)");
  }

  toggleListening() {
    if (this.audio) {
      this.stopListening();
      return;
    }
    const url = new URL("api/sound/live.wav", document.baseURI);
    url.searchParams.set("t", Date.now());
    this.audio = new Audio(url.href);
    this.audio.play().catch((error) => {
      if (error.name === "AbortError") return; // stopped before it started
      console.error(error);
      this.stopListening();
    });
    this.markControls();
  }

  stopListening() {
    if (!this.audio) return;
    this.audio.pause();
    this.audio.removeAttribute("src"); // closes the stream
    this.audio.load();
    this.audio = null;
    this.markControls();
  }

  markControls() {
    for (const button of this.rateButtons) {
      button.setAttribute("aria-checked", String(button.dataset.rate === this.rate));
    }
    this.boxesButton.setAttribute("aria-pressed", String(this.showBoxes));
    this.zonesButton.setAttribute("aria-pressed", String(this.zones.visible));
    this.listenButton.setAttribute("aria-pressed", String(Boolean(this.audio)));
    this.listenButton.querySelector("span").textContent = this.audio ? "Stop listening" : "Listen";
  }

  toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else if (this.stage.requestFullscreen && !this.stage.classList.contains("expanded")) {
      this.stage.requestFullscreen().catch(() => this.stage.classList.add("expanded"));
    } else {
      this.stage.classList.toggle("expanded"); // phones without element full screen
    }
    this.markFullscreen();
  }

  markFullscreen() {
    const full = document.fullscreenElement === this.stage || this.stage.classList.contains("expanded");
    this.fullscreenButton.querySelector("span").textContent = full ? "Exit full screen" : "Full screen";
  }

  async snapshot(button) {
    button.disabled = true;
    try {
      const response = await fetch(new URL("api/camera/snapshot.jpg", document.baseURI), { cache: "no-store" });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const url = URL.createObjectURL(await response.blob());
      el("a", { href: url, download: `${slug(this.name)}-${stamp(new Date())}.jpg` }).click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (error) {
      console.error(error);
      this.flash("No picture to save right now.");
    } finally {
      button.disabled = false;
    }
  }

  flash(text) {
    this.message.textContent = text;
    this.message.hidden = false;
    setTimeout(() => this.renderMessage(), 2500);
  }

  // --- rendering ------------------------------------------------------------------------

  tick() {
    if (!this.active) return;
    this.clock.textContent = clock(Date.now() / 1000);
  }

  drawBoxes(sample) {
    if (!this.showBoxes || !sample.boxes?.length) {
      this.boxes.replaceChildren();
      return;
    }
    this.boxes.replaceChildren(
      ...sample.boxes.flatMap(([x, y, w, h]) =>
        ["halo", "box"].map((cls) => svg("rect", { class: cls, x, y, width: w, height: h })),
      ),
    );
  }

  renderMessage() {
    const status = this.status;
    let text = null;
    if (status?.state === "unavailable") text = `Camera unavailable: ${status.error}. Trying again every few seconds.`;
    else if (status?.state === "starting") text = "Starting the camera…";
    else if (this.failed) text = "Reconnecting to the picture…";
    this.message.textContent = text || "";
    this.message.hidden = !text;
    const live = !text && status?.state === "streaming";
    this.livePill.classList.toggle("off", !live);
    this.livePill.querySelector(".label").textContent = !live ? "Offline" : this.rate === "full" ? "Live" : `Live · ${this.rate} fps`;
  }

  render() {
    this.renderedAt = performance.now();
    this.renderMessage();
    this.renderMotion();
    this.renderLight();
    if (this.microphone) this.renderSound();
    this.renderStream();
    this.renderEvents();
  }

  renderSound() {
    const { value, note, last, state, chart } = this.panels.sound;
    const sound = this.sound;
    const level = this.latestSound?.level_db ?? sound?.level_db;
    const background = this.latestSound?.background_db ?? sound?.background_db;
    const alarm = this.latestSound ? this.latestSound.alarm : sound?.alarm?.pattern;
    const unavailable = sound && sound.state !== "listening";
    const word = unavailable ? "Unavailable" : alarm ? "Alarm" : level != null && background != null && level > background + 10 ? "Noisy" : "Quiet";
    setPill(state, word, word === "Noisy");
    state.classList.toggle("critical", Boolean(alarm));
    value.replaceChildren(number(level, 0), el("span", { class: "unit" }, "dBFS"));
    note.textContent = unavailable
      ? `Microphone unavailable: ${sound.error}`
      : `background ${number(background, 0)} dBFS (0 is the loudest it can record)`;
    const chirping = sound?.chirping;
    const lastAlarm = this.soundEvents.find((e) => e.kind === "alarm");
    last.textContent = alarm
      ? `${ALARMS[alarm]} now`
      : chirping
        ? `A detector chirps every ${chirping.interval_s} s: its battery may be low`
        : lastAlarm
          ? `Last alarm ${ago(lastAlarm.end ?? lastAlarm.start)}: ${ALARMS[lastAlarm.pattern].toLowerCase()}`
          : "No alarm heard since IoT Center started";
    chart.update(this.soundHistory.t, this.soundHistory.level_db, 0, -80);
  }

  renderMotion() {
    const { value, note, last, state, chart } = this.panels.motion;
    const activity = this.status?.activity;
    const moving = this.latest?.moving ?? activity?.moving ?? false;
    const motion = this.latest?.motion_pct ?? activity?.motion_pct;
    const lastMotion = moving ? Date.now() / 1000 : activity?.last_motion_at;
    const peak = Math.max(0, ...this.history.motion_pct);
    setPill(state, moving ? "Moving" : "Still", moving);
    value.replaceChildren(number(motion, 1), el("span", { class: "unit" }, "%"));
    note.textContent = "of the picture is changing";
    last.textContent = moving
      ? "Something is moving now"
      : lastMotion
        ? `Last motion ${ago(lastMotion)}${peak ? ` · peak ${number(peak, 1)} % in 10 min` : ""}`
        : "No motion since IoT Center started";
    chart.update(this.history.t, this.history.motion_pct, Math.max(2, peak * 1.15));
  }

  renderLight() {
    const { value, note, last, state, chart } = this.panels.light;
    const activity = this.status?.activity;
    const brightness = this.latest?.brightness_pct ?? activity?.brightness_pct;
    const word = brightness == null ? "–" : brightness < 8 ? "Dark" : brightness < 25 ? "Dim" : "Bright";
    setPill(state, word, word === "Bright");
    value.replaceChildren(number(brightness, 0), el("span", { class: "unit" }, "%"));
    note.textContent = "brightness of the picture";
    last.textContent = activity?.lights
      ? `Light switched ${activity.lights} at ${clock(activity.lights_changed_at)} (${ago(activity.lights_changed_at)})`
      : "No light switched on or off since IoT Center started";
    chart.update(this.history.t, this.history.brightness_pct, 100);
  }

  renderStream() {
    const status = this.status;
    if (!status) return;
    const fps = status.fps;
    const viewRate = this.rate === "full" ? fps : Math.min(fps ?? Infinity, Number(this.rate));
    const mbps = (rate) => (status.frame_bytes && rate ? `${number((status.frame_bytes * rate * 8) / 1e6, 1)} Mbit/s` : "–");
    const passthrough = status.format === "MJPEG";
    const facts = [
      ["Picture", status.width ? `${status.width} × ${status.height}` : "–"],
      ["Encoding", passthrough ? "MJPEG by the camera, passed through untouched" : status.format],
      ["Camera rate", fps ? `${number(fps, 1)} fps` : "–"],
      ["Frame size", status.frame_bytes ? bytes(status.frame_bytes) : "–"],
      ["Data rate", this.rate === "full" ? mbps(fps) : `${mbps(viewRate)} at ${this.rate} fps (full rate: ${mbps(fps)})`],
      ["Viewers", integer(status.viewers)],
      ["Streaming for", status.streaming_since ? duration(Date.now() / 1000 - status.streaming_since) : "–"],
      ["Reconnects", integer(status.reconnects)],
    ];
    setPill(this.panels.stream.state, status.state === "streaming" ? "Streaming" : status.state === "starting" ? "Starting" : "Unavailable", status.state === "streaming");
    this.panels.stream.facts.replaceChildren(...facts.flatMap(([label, text]) => [el("dt", {}, label), el("dd", {}, text)]));
  }

  renderEvents() {
    const head = ["Time", "What", "Lasted", "Details"];
    const now = Date.now() / 1000;
    const events = [...this.events, ...this.soundEvents].sort((a, b) => b.start - a.start).slice(0, 30);
    const rows = events.map((event) => {
      const when = now - event.start > 20 * 3600 ? dateTime(event.start) : clock(event.start);
      const [what, details] = describe(event);
      const lasted = event.end === undefined || event.end === event.start ? "–" : event.end == null ? "ongoing" : duration(Math.max(1, event.end - event.start));
      return el(
        "tr",
        { class: event.kind === "alarm" || (event.kind === "water" && !event.unconfirmed && !event.stain) ? "alert" : event.kind === "water" && event.unconfirmed && !event.stain ? "check" : null },
        el("td", {}, when),
        el("td", { class: "text" }, what),
        el("td", {}, lasted),
        el("td", { class: "text" }, details),
      );
    });
    const empty = el("tr", {}, el("td", { colspan: head.length, class: "text" }, el("span", { class: "muted" }, "Nothing yet: motion, lights, zones and sounds show up here.")));
    document.getElementById("camera-events").replaceChildren(
      el("table", {}, el("thead", {}, el("tr", {}, head.map((h, i) => el("th", { class: i % 2 ? "text" : null, scope: "col" }, h)))), el("tbody", {}, rows.length ? rows : [empty])),
    );
  }
}

export const ALARMS = {
  smoke: "Smoke alarm sounding (temporal-3 beeps)",
  co: "Carbon monoxide alarm sounding (temporal-4 beeps)",
  beeping: "Something is beeping (an alarm or a sensor)",
};

/** An event as words: what happened, and its details. */
function describe(event) {
  switch (event.kind) {
    case "motion":
      return [`Motion${event.zones?.length ? ` at ${event.zones.join(", ")}` : ""}`, `${number(event.peak_pct, 1)} % of the picture`];
    case "lights_on":
    case "lights_off":
      return [`Room light switched ${event.kind === "lights_on" ? "on" : "off"}`, "–"];
    case "light_on":
      return [`${event.zone}: on`, "–"];
    case "light_blinking":
      return [`${event.zone}: blinking`, "a fault code, on many control boards"];
    case "new_view":
      return ["New lighting, or the camera moved", "floor zones learn the dry floor for it; redraw zones if the camera moved"];
    case "water":
      if (event.stain) return [`Dark patch at ${event.zone}: a stain`, "it did not change, so it is part of the floor now"];
      if (event.unconfirmed) {
        return [`Dark patch at ${event.zone}: water or a stain?`, event.drying ? "it got lighter: water, drying" : `${number(event.peak_pct, 1)} % of the zone, already there when it started watching`];
      }
      return [`Possible water: ${event.zone}`, `${number(event.peak_pct, 1)} % of the zone looks wet`];
    case "alarm":
      return [ALARMS[event.pattern] || "Alarm", `${integer(event.freq_hz)} Hz`];
    case "chirping":
      return ["A detector is chirping: low battery?", `every ${event.interval_s} s, ${event.count} chirps`];
    case "loud":
      return ["Loud noise", `${number(event.peak_db, 0)} dBFS, ${number(event.over_db, 0)} dB over the background`];
    default:
      return [event.kind, "–"];
  }
}

/** One point a second keeping each second's peak, trimmed to the chart's window. */
function appendPeak(history, t, values) {
  const last = history.t.length - 1;
  if (last >= 0 && t - history.t[last] < 0.9) {
    for (const [key, value] of Object.entries(values)) history[key][last] = Math.max(history[key][last], value);
  } else {
    history.t.push(t);
    for (const [key, value] of Object.entries(values)) history[key].push(value);
  }
  while (history.t.length && history.t[0] < t - WINDOW_S) {
    for (const key of Object.keys(history)) history[key].shift();
  }
}

// --- the cards under the picture ------------------------------------------------------------

function buildPanels() {
  const panel = (id, title, chartSpec) => {
    const root = document.getElementById(`camera-${id}`);
    const state = el("span", { class: "pill" });
    const value = el("span", { class: "kpi-value" });
    const note = el("span", { class: "kpi-note" });
    const last = el("span", { class: "kpi-note" });
    const chart = new Strip(chartSpec);
    root.replaceChildren(
      el("header", { class: "panel-head" }, el("h2", { id: `camera-${id}-title` }, title), state),
      value,
      note,
      last,
      chart.root,
    );
    return { root, state, value, note, last, chart };
  };
  const stream = document.getElementById("camera-stream");
  const state = el("span", { class: "pill" });
  const facts = el("dl", { class: "facts" });
  stream.replaceChildren(el("header", { class: "panel-head" }, el("h2", { id: "camera-stream-title" }, "Stream"), state), facts);
  return {
    motion: panel("motion", "Motion", { label: "Motion", unit: "% of the picture", digits: 1, area: true }),
    light: panel("light", "Room light", { label: "Brightness", unit: "%", digits: 0, area: false }),
    sound: panel("sound", "Sound", { label: "Loudest", unit: "dBFS", digits: 0, area: true }),
    stream: { state, facts },
  };
}

function setPill(pill, text, on) {
  pill.textContent = text;
  pill.classList.toggle("on", on);
}

/**
 * The last 10 minutes as a small chart: a 2px line (with a light wash for motion) on a fixed
 * scale, a hairline baseline, and a crosshair with a tooltip on hover.
 */
class Strip {
  constructor({ label, unit, digits, area }) {
    this.spec = { label, unit, digits };
    this.data = { t: [], values: [], start: 0 };
    this.svg = svg("svg", { class: "camera-strip", viewBox: "0 0 100 40", preserveAspectRatio: "none", role: "img" });
    this.area = area ? svg("path", { class: "area" }) : null;
    this.line = svg("path", { class: "line" });
    this.cross = svg("line", { class: "cross", y1: 0, y2: 40, visibility: "hidden" });
    this.svg.append(svg("line", { class: "base", x1: 0, x2: 100, y1: 39.5, y2: 39.5 }), ...(this.area ? [this.area] : []), this.line, this.cross);
    this.root = el(
      "div",
      { class: "camera-strip-wrap" },
      this.svg,
      el("div", { class: "strip-axis", "aria-hidden": "true" }, el("span", {}, "10 min ago"), el("span", {}, "now")),
    );
    this.svg.addEventListener("pointermove", (event) => this.hover(event));
    this.svg.addEventListener("pointerleave", () => {
      this.cross.setAttribute("visibility", "hidden");
      tooltip.hidden = true;
    });
  }

  update(t, values, max, min = 0) {
    const end = Date.now() / 1000;
    const start = end - WINDOW_S;
    this.data = { t, values, start, max };
    const x = (time) => (((time - start) / WINDOW_S) * 100).toFixed(2);
    const y = (value) => (38 - ((Math.min(Math.max(value, min), max) - min) / (max - min)) * 35).toFixed(2);
    let line = "";
    let area = "";
    let segment = [];
    const flush = () => {
      if (!segment.length) return;
      line += segment.map(([tx, v], i) => `${i ? "L" : "M"}${x(tx)},${y(v)}`).join("");
      area += `M${x(segment[0][0])},39.5${segment.map(([tx, v]) => `L${x(tx)},${y(v)}`).join("")}L${x(segment[segment.length - 1][0])},39.5Z`;
      segment = [];
    };
    for (let i = 0; i < t.length; i += 1) {
      if (t[i] < start || values[i] == null) continue;
      if (segment.length && t[i] - segment[segment.length - 1][0] > GAP_S) flush();
      segment.push([t[i], values[i]]);
    }
    flush();
    this.line.setAttribute("d", line);
    this.area?.setAttribute("d", area);
    const latest = values[values.length - 1];
    this.svg.setAttribute("aria-label", `${this.spec.label} over the last 10 minutes; now ${number(latest, this.spec.digits)} ${this.spec.unit}`);
  }

  hover(event) {
    const { t, values, start } = this.data;
    const box = this.svg.getBoundingClientRect();
    const at = start + ((event.clientX - box.left) / box.width) * WINDOW_S;
    let best = -1;
    for (let i = 0; i < t.length; i += 1) {
      if (best < 0 || Math.abs(t[i] - at) < Math.abs(t[best] - at)) best = i;
    }
    if (best < 0 || Math.abs(t[best] - at) > GAP_S) {
      this.cross.setAttribute("visibility", "hidden");
      tooltip.hidden = true;
      return;
    }
    const x = (((t[best] - start) / WINDOW_S) * 100).toFixed(2);
    this.cross.setAttribute("x1", x);
    this.cross.setAttribute("x2", x);
    this.cross.setAttribute("visibility", "visible");
    tooltip.replaceChildren(
      el("div", { class: "when" }, clock(t[best])),
      el(
        "div",
        { class: "row" },
        el("i", { class: "key", style: "--key: var(--series-1)" }),
        el("strong", {}, `${number(values[best], this.spec.digits)} ${this.spec.unit}`),
        el("span", {}, this.spec.label),
      ),
    );
    tooltip.hidden = false;
    const tip = tooltip.getBoundingClientRect();
    let left = event.clientX + 14;
    if (left + tip.width > window.innerWidth - 8) left = event.clientX - tip.width - 14;
    tooltip.style.left = `${Math.max(8, left)}px`;
    tooltip.style.top = `${Math.max(8, box.top - tip.height - 8)}px`;
  }
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function slug(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "camera";
}

function stamp(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
}

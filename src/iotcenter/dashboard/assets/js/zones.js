// Zones: parts of the camera's picture the server watches for one thing each: water on a
// floor, a status light (or a flame), or motion in an area. Drawn on the picture with their
// state, listed with what they saw, and added by dragging a rectangle over the picture.

import { send } from "./api.js";
import { ago, duration, el, number } from "./format.js";

const SVG = "http://www.w3.org/2000/svg";
const KINDS = { floor: "Water on the floor", drain: "Floor drain: water pooling", light: "Status light or flame", area: "Motion" };
const isFloor = (zone) => zone.kind === "floor" || zone.kind === "drain";

export class ZonesEditor {
  constructor({ frame, onChange }) {
    this.frame = frame;
    this.onChange = onChange; // ask the camera view to refresh after a change
    this.layer = document.getElementById("camera-zones-layer");
    this.labels = document.getElementById("camera-zone-labels");
    this.hint = document.getElementById("camera-hint");
    this.form = document.getElementById("zone-form");
    this.list = document.getElementById("zone-list");
    this.addButton = document.getElementById("zone-add");
    this.zones = []; // from api/camera: definitions and what each one sees
    this.drawing = false;
    this.draft = null; // {x, y, w, h} while drawing or waiting for the form
    this.visible = true;

    this.addButton.addEventListener("click", () => this.startDrawing());
    document.getElementById("zone-cancel").addEventListener("click", () => this.cancel());
    this.form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.save();
    });
    this.frame.addEventListener("pointerdown", (event) => this.pointerDown(event));
    this.frame.addEventListener("pointermove", (event) => this.pointerMove(event));
    this.frame.addEventListener("pointerup", (event) => this.pointerUp(event));
    document.addEventListener("keydown", (event) => event.key === "Escape" && this.drawing && this.cancel());
  }

  // --- data -----------------------------------------------------------------------------

  setZones(zones) {
    this.zones = zones;
    this.render();
  }

  /** What a live sample says about each zone (5 a second): just the labels. */
  live(states) {
    if (!states?.length) return;
    for (const state of states) {
      const zone = this.zones.find((z) => z.id === state.id);
      if (zone) Object.assign(zone, state);
    }
    this.renderOverlay();
  }

  setVisible(visible) {
    this.visible = visible;
    this.renderOverlay();
  }

  // --- drawing a new zone ---------------------------------------------------------------

  startDrawing() {
    this.drawing = true;
    this.draft = null;
    this.form.hidden = true;
    this.hint.hidden = false;
    this.frame.classList.add("drawing");
    this.frame.scrollIntoView({ behavior: "smooth", block: "center" });
    this.renderOverlay();
  }

  cancel() {
    this.drawing = false;
    this.draft = null;
    this.form.hidden = true;
    this.hint.hidden = true;
    this.frame.classList.remove("drawing");
    this.renderOverlay();
  }

  point(event) {
    const box = this.frame.getBoundingClientRect();
    const clamp = (v) => Math.min(1, Math.max(0, v));
    return { x: clamp((event.clientX - box.left) / box.width), y: clamp((event.clientY - box.top) / box.height) };
  }

  pointerDown(event) {
    if (!this.drawing) return;
    event.preventDefault();
    this.frame.setPointerCapture(event.pointerId);
    this.origin = this.point(event);
    this.draft = { ...this.origin, w: 0, h: 0 };
  }

  pointerMove(event) {
    if (!this.drawing || !this.origin) return;
    const p = this.point(event);
    this.draft = {
      x: Math.min(p.x, this.origin.x),
      y: Math.min(p.y, this.origin.y),
      w: Math.abs(p.x - this.origin.x),
      h: Math.abs(p.y - this.origin.y),
    };
    this.renderOverlay();
  }

  pointerUp() {
    if (!this.drawing || !this.origin) return;
    this.origin = null;
    if (!this.draft || this.draft.w < 0.02 || this.draft.h < 0.02) {
      this.draft = null; // a click, not a drag: keep drawing
      this.renderOverlay();
      return;
    }
    this.hint.hidden = true;
    this.form.hidden = false;
    document.getElementById("zone-form-note").textContent = "";
    this.form.elements.name.value = "";
    this.form.elements.name.focus();
    this.renderOverlay();
  }

  async save() {
    const round = (v) => Math.round(v * 10_000) / 10_000;
    const { x, y, w, h } = this.draft;
    const body = { name: this.form.elements.name.value, kind: this.form.elements.kind.value, x: round(x), y: round(y), w: round(w), h: round(h) };
    try {
      await send("POST", "api/camera/zones", body);
    } catch (error) {
      document.getElementById("zone-form-note").textContent = `Not saved: ${error.message}`;
      return;
    }
    this.cancel();
    this.onChange();
  }

  async remove(zone) {
    if (!confirm(`Delete the zone “${zone.name}”?`)) return;
    try {
      await send("DELETE", `api/camera/zones/${zone.id}`);
    } catch (error) {
      console.error(error);
    }
    this.onChange();
  }

  async setEnabled(zone, enabled) {
    try {
      await send("PATCH", `api/camera/zones/${zone.id}`, { enabled });
    } catch (error) {
      console.error(error);
    }
    this.onChange();
  }

  async markDry(zone) {
    try {
      await send("POST", `api/camera/zones/${zone.id}/dry`);
    } catch (error) {
      console.error(error);
    }
    this.onChange();
  }

  // --- rendering ------------------------------------------------------------------------

  render() {
    this.renderOverlay();
    this.renderList();
  }

  renderOverlay() {
    const shapes = [];
    const labels = [];
    const show = this.visible || this.drawing;
    for (const zone of show ? this.zones : []) {
      const alert = zone.state === "water";
      const check = zone.state === "check";
      const off = zone.enabled === false;
      const cls = `zone ${zone.kind}${alert ? " alert" : ""}${check ? " check" : ""}${zone.state === "on" || zone.state === "blinking" ? " lit" : ""}${off ? " off" : ""}`;
      shapes.push(svg("rect", { class: "halo", x: zone.x, y: zone.y, width: zone.w, height: zone.h }));
      shapes.push(svg("rect", { class: cls, x: zone.x, y: zone.y, width: zone.w, height: zone.h }));
      if (isFloor(zone) && zone.box) {
        const [x, y, w, h] = zone.box;
        shapes.push(svg("rect", { class: "patch", x, y, width: w, height: h }));
      }
      labels.push(
        el(
          "span",
          { class: `zone-label${alert ? " alert" : ""}${check ? " check" : ""}${off ? " off" : ""}`, style: `left: ${zone.x * 100}%; top: ${zone.y * 100}%` },
          zone.name,
          el("b", {}, shortState(zone)),
        ),
      );
    }
    if (this.draft) {
      const { x, y, w, h } = this.draft;
      shapes.push(svg("rect", { class: "draft", x, y, width: w, height: h }));
    }
    this.layer.replaceChildren(...shapes);
    this.labels.replaceChildren(...labels);
  }

  renderList() {
    if (!this.zones.length) {
      this.list.replaceChildren(
        el("p", { class: "zones-empty" }, "No zones yet. Add one to watch the floor around a water heater or a drain for water, a furnace's status light or flame, or an area for motion."),
      );
      return;
    }
    const now = Date.now() / 1000;
    const rows = this.zones.map((zone) =>
      el(
        "tr",
        { class: zone.state === "water" ? "alert" : zone.state === "check" ? "check" : zone.enabled === false ? "off" : null },
        el(
          "td",
          { class: "switch-cell" },
          el(
            "label",
            { class: "switch", title: zone.enabled === false ? "Not watched: switch on to watch it again" : "Watched: switch off to stop watching (it is kept)" },
            el("input", { type: "checkbox", checked: zone.enabled !== false, "aria-label": `Watch ${zone.name}`, onchange: (event) => this.setEnabled(zone, event.target.checked) }),
            el("span", { "aria-hidden": "true" }),
          ),
        ),
        el("td", { class: "text" }, el("strong", {}, zone.name), el("div", { class: "muted" }, KINDS[zone.kind])),
        el("td", { class: "text" }, longState(zone)),
        el("td", { class: "text" }, detail(zone, now)),
        el(
          "td",
          {},
          el(
            "div",
            { class: "zone-actions" },
            isFloor(zone)
              ? el("button", { class: "button small", type: "button", title: "The floor is as it should be (dry, or only a stain): learn it as it is now", onclick: () => this.markDry(zone) }, zone.state === "check" ? "It's a stain" : "Floor is dry")
              : null,
            el("button", { class: "button small", type: "button", onclick: () => this.remove(zone) }, "Delete"),
          ),
        ),
      ),
    );
    const head = el("tr", {}, ["Watch", "Zone", "Now", "Details", ""].map((h, i) => el("th", { class: i < 4 ? "text" : null, scope: "col" }, h)));
    this.list.replaceChildren(el("table", {}, el("thead", {}, head), el("tbody", {}, rows)));
  }
}

function shortState(zone) {
  if (zone.enabled === false) return "off";
  if (isFloor(zone)) {
    return { learning: "learning", dry: "dry", water: "water?", check: "water or stain?", paused: "paused" }[zone.state] || "";
  }
  if (zone.kind === "light") return { on: "on", off: "off", blinking: "blinking" }[zone.state] || "…";
  return zone.state === "moving" ? "motion" : "";
}

function longState(zone) {
  if (zone.enabled === false) return "Not watched (switched off)";
  if (isFloor(zone)) {
    const check = zone.drying
      ? "A dark patch that is getting lighter: water, drying"
      : "A dark patch was already there: water or a stain? Watching whether it dries (water), spreads (water) or stays as it is (a stain)";
    return {
      learning: "Learning the dry floor…",
      dry: zone.kind === "drain" ? "No pool" : "Dry",
      water: zone.kind === "drain"
        ? `Possible backup: water over ${number(zone.wet_pct, 0)} % of the zone`
        : `Possible water: ${number(zone.wet_pct, 1)} % of the zone looks wet. Check the picture; if it is something left there, mark the floor dry`,
      check,
      paused: "Waiting: someone is there, the light changed, or it is too dark",
    }[zone.state] || "–";
  }
  if (zone.kind === "light") {
    return { on: "On", off: "Off", blinking: "Blinking", unknown: "Not seen yet" }[zone.state] || "–";
  }
  return zone.state === "moving" ? "Something is moving" : "Still";
}

function detail(zone, now) {
  if (zone.enabled === false) return "–";
  if (zone.kind === "light") {
    const since = !zone.since ? null : zone.since_start ? `${zone.state} since IoT Center started, ${duration(now - zone.since)} ago` : `${zone.state} for ${duration(now - zone.since)}`;
    const pace = zone.state === "blinking" && zone.flip_s ? `changes every ${number(zone.flip_s, 1)} s` : null;
    const day = `on ${zone.on_count_24h} time${zone.on_count_24h === 1 ? "" : "s"} in 24 h, ${duration(zone.on_s_24h)} in all`;
    const levels = zone.lit_level != null ? `level ${zone.level} (unlit about ${zone.unlit_level}, lit about ${zone.lit_level})` : `level ${zone.level} of 255: not seen both lit and unlit yet`;
    return [since, pace, day, levels].filter(Boolean).join(" · ");
  }
  if (isFloor(zone)) {
    const since = zone.since && zone.state !== "learning" ? `${shortState(zone)} for ${duration(now - zone.since)}` : null;
    const stain = zone.state === "check" && zone.stain_at ? (zone.stain_at > now ? `counts as a stain in ${duration(zone.stain_at - now)} if it stays as it is` : "about to count as a stain") : null;
    return [since, stain, zone.last_motion_at ? `last motion ${ago(zone.last_motion_at)}` : null].filter(Boolean).join(" · ") || "–";
  }
  return zone.last_motion_at ? `last motion ${ago(zone.last_motion_at)}` : "no motion yet";
}

function svg(tag, attrs) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

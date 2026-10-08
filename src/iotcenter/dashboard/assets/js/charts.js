// Time-series charts on top of uPlot, styled from the CSS color tokens.
//
// A chart plots one or more series against time. In "band" mode (history ranges) every
// series carries avg/min/max per bucket: the average is the line, min..max a soft band.
// Crosshairs are synced across charts; the tooltip lists every series at that moment.

import { dateTime, clock, el, number } from "./format.js";

const tooltip = document.getElementById("tooltip");
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function rgba(hex, alpha) {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export class TimeChart {
  /**
   * @param {HTMLElement} container  element that receives the canvas (sized by CSS)
   * @param {object} spec  { series: [{ label, color: "--series-1" }], unit, digits }
   */
  constructor(container, spec) {
    this.container = container;
    this.spec = spec;
    this.band = false;
    this.data = null;
    this.plot = null;
    this.hovered = false;
    this.empty = el("div", { class: "chart-empty", hidden: true }, "No readings in this range");
    container.append(this.empty);
    new ResizeObserver(() => this.resize()).observe(container);
  }

  /** Live mode: data = [times, values1, values2, ...] */
  setLive(data) {
    this.update(data, false);
  }

  /** History mode: data = [times, avg1, min1, max1, avg2, min2, max2, ...] */
  setBands(data) {
    this.update(data, true);
  }

  update(data, band) {
    this.data = data;
    const hasPoints = data[0].length > 0 && data.slice(1).some((s) => s.some((v) => v != null));
    this.empty.hidden = hasPoints;
    if (this.plot && this.band === band) {
      this.plot.setData(data);
      return;
    }
    this.band = band;
    this.rebuild();
  }

  rebuild() {
    this.plot?.destroy();
    this.plot = null;
    if (!this.data) return;
    const { width, height } = this.size();
    if (!width) return; // hidden view: built on the next resize
    this.plot = new uPlot(this.options(width, height), this.data, this.container);
    this.plot.over.addEventListener("mouseenter", () => (this.hovered = true));
    this.plot.over.addEventListener("mouseleave", () => {
      this.hovered = false;
      tooltip.hidden = true;
    });
  }

  size() {
    return { width: this.container.clientWidth, height: this.container.clientHeight };
  }

  resize() {
    const { width, height } = this.size();
    if (!width) return;
    if (!this.plot) this.rebuild();
    else this.plot.setSize({ width, height });
  }

  options(width, height) {
    const { spec, band } = this;
    const muted = css("--muted");
    const grid = css("--grid");
    const surface = css("--surface");
    const colors = spec.series.map((s) => css(s.color));
    const single = spec.series.length === 1;
    const series = [{}];
    const bands = [];
    const isEdge = [false]; // min/max series only shape the band: no hover dot
    const seriesColor = [null];

    spec.series.forEach((s, i) => {
      const color = colors[i];
      series.push({
        label: s.label,
        stroke: color,
        width: 2,
        fill: !band && single ? rgba(color, 0.1) : undefined,
        points: { show: false },
      });
      isEdge.push(false);
      seriesColor.push(color);
      if (band) {
        const avg = series.length - 1;
        for (const edge of ["min", "max"]) {
          series.push({ label: `${s.label} ${edge}`, stroke: "transparent", width: 0, points: { show: false } });
          isEdge.push(true);
          seriesColor.push(color);
        }
        bands.push({ series: [avg + 2, avg + 1], fill: rgba(color, 0.12) });
      }
    });

    const font = `11px ${css("--font") || "system-ui"}`;
    return {
      width,
      height,
      series,
      bands,
      legend: { show: false },
      padding: [8, 20, 0, 0], // room on the right so the last time label isn't clipped
      scales: { x: { time: true } },
      axes: [
        { stroke: muted, font, space: 90, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid, width: 1, size: 4 } },
        {
          stroke: muted,
          font,
          size: 52,
          grid: { stroke: grid, width: 1 },
          ticks: { show: false },
          values: (u, values) => values.map((v) => number(v, spec.digits ?? 1)),
        },
      ],
      cursor: {
        sync: { key: "iot-overview" },
        y: false,
        drag: { x: false, y: false },
        points: {
          // an 8 px dot inside a 2 px surface-colored ring; band edges get no marker
          size: (u, i) => (isEdge[i] ? 0 : 12),
          width: (u, i) => (isEdge[i] ? 0 : 2),
          stroke: () => surface,
          fill: (u, i) => seriesColor[i],
        },
      },
      hooks: {
        draw: [(u) => !band && this.drawEndDots(u, colors, surface)],
        setCursor: [(u) => this.showTooltip(u, colors)],
      },
    };
  }

  // Live lines: a dot with a surface-colored ring marks "now" at the end of each line, and
  // readings with a gap on both sides (no line to draw) get a small dot so they stay visible.
  drawEndDots(u, colors, surface) {
    const ctx = u.ctx;
    const ratio = uPlot.pxRatio;
    const dot = (x, y, radius, color, ring) => {
      ctx.beginPath();
      ctx.arc(x, y, radius * ratio, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      if (ring) {
        ctx.lineWidth = 2 * ratio;
        ctx.strokeStyle = surface;
        ctx.stroke();
      }
    };
    colors.forEach((color, i) => {
      const values = u.data[i + 1];
      const times = u.data[0];
      let last = values.length - 1;
      while (last >= 0 && values[last] == null) last -= 1;
      if (last < 0) return;
      for (let j = 0; j < last; j += 1) {
        if (values[j] != null && values[j - 1] == null && values[j + 1] == null) {
          dot(u.valToPos(times[j], "x", true), u.valToPos(values[j], "y", true), 2, color, false);
        }
      }
      dot(u.valToPos(times[last], "x", true), u.valToPos(values[last], "y", true), 4, color, true);
    });
  }

  showTooltip(u, colors) {
    const idx = u.cursor.idx;
    if (!this.hovered || idx == null) {
      if (this.hovered) tooltip.hidden = true;
      return;
    }
    const { spec, band } = this;
    const t = u.data[0][idx];
    const stride = band ? 3 : 1;
    const rows = spec.series.map((s, i) => {
      const at = 1 + i * stride;
      const value = u.data[at][idx];
      const row = el(
        "div",
        { class: "row" },
        el("i", { class: "key", style: `--key: ${colors[i]}` }),
        el("strong", {}, value == null ? "–" : `${number(value, spec.digits ?? 1)} ${spec.unit}`),
        el("span", {}, band ? `${s.label} (avg)` : s.label),
      );
      if (!band || value == null) return row;
      const range = `range ${number(u.data[at + 1][idx], spec.digits ?? 1)} – ${number(u.data[at + 2][idx], spec.digits ?? 1)}`;
      return [row, el("div", { class: "range" }, range)];
    });
    tooltip.replaceChildren(el("div", { class: "when" }, band ? dateTime(t) : clock(t)), ...rows.flat());
    tooltip.hidden = false;

    const box = u.over.getBoundingClientRect();
    const tip = tooltip.getBoundingClientRect();
    let left = box.left + u.cursor.left + 14;
    if (left + tip.width > window.innerWidth - 8) left = box.left + u.cursor.left - tip.width - 14;
    const top = Math.max(8, Math.min(box.top + u.cursor.top - tip.height / 2, window.innerHeight - tip.height - 8));
    tooltip.style.left = `${Math.max(8, left)}px`;
    tooltip.style.top = `${top}px`;
  }
}

// Insert nulls where readings are missing, so lines break at gaps instead of bridging them.
export function breakGaps(times, columns, maxGap) {
  const outTimes = [];
  const outColumns = columns.map(() => []);
  for (let i = 0; i < times.length; i += 1) {
    if (i > 0 && times[i] - times[i - 1] > maxGap) {
      outTimes.push((times[i] + times[i - 1]) / 2);
      outColumns.forEach((column) => column.push(null));
    }
    outTimes.push(times[i]);
    columns.forEach((column, c) => outColumns[c].push(column[i]));
  }
  return [outTimes, ...outColumns];
}

// A tiny SVG sparkline for KPI tiles: the trend in a quiet gray, the most recent stretch
// in the accent color (the "now" of the tile).
export function sparkline(values) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "kpi-spark");
  svg.setAttribute("viewBox", "0 0 100 36");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const points = values.filter((v) => v != null);
  if (points.length < 2) return svg;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const coords = points.map((v, i) => `${((i / (points.length - 1)) * 100).toFixed(2)},${(33 - ((v - min) / span) * 30).toFixed(2)}`);
  const recent = Math.max(2, Math.ceil(points.length * 0.12));
  for (const [cls, slice] of [["trend", coords], ["now", coords.slice(-recent)]]) {
    const line = document.createElementNS(svg.namespaceURI, "polyline");
    line.setAttribute("class", cls);
    line.setAttribute("points", slice.join(" "));
    line.setAttribute("vector-effect", "non-scaling-stroke");
    svg.append(line);
  }
  return svg;
}

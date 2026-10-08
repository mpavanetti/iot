// Formatting helpers: numbers, units, durations and times. Timestamps are Unix seconds.

export const METRICS = {
  temperature_c: { label: "Temperature", unit: "°C", digits: 1 },
  humidity_pct: { label: "Humidity", unit: "%", digits: 1 },
  pressure_hpa: { label: "Pressure", unit: "hPa", digits: 1 },
  dew_point_c: { label: "Dew point", unit: "°C", digits: 1 },
  cpu_temp_c: { label: "Board temperature", unit: "°C", digits: 1 },
};

export function number(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function integer(value) {
  return value === null || value === undefined ? "–" : Math.round(value).toLocaleString();
}

export function withUnit(value, metric) {
  const { unit, digits } = METRICS[metric];
  return value === null || value === undefined ? "–" : `${number(value, digits)} ${unit}`;
}

export function signed(value, digits = 1) {
  const rounded = Number(value.toFixed(digits)); // so -0.02 shows as ±0.0, not −0.0
  const text = number(Math.abs(rounded), digits);
  return rounded > 0 ? `+${text}` : rounded < 0 ? `−${text}` : `±${text}`;
}

export function bytes(value) {
  if (value === null || value === undefined) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${number(value, i === 0 ? 0 : 1)} ${units[i]}`;
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

// Coarse duration for labels: "40 s", "8 min", "3 h", "2 days".
export function roughly(seconds) {
  const s = Math.max(0, seconds);
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 90 * 60) return `${Math.round(s / 60)} min`;
  if (s < 36 * 3600) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} days`;
}

export function ago(epoch, now = Date.now() / 1000) {
  if (!epoch) return "never";
  const seconds = Math.max(0, now - epoch);
  if (seconds < 5) return "just now";
  return `${duration(seconds)} ago`;
}

const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const dateTimeFormat = new Intl.DateTimeFormat(undefined, {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
});

export function clock(epoch) {
  return epoch ? timeFormat.format(new Date(epoch * 1000)) : "–";
}

export function dateTime(epoch) {
  return epoch ? dateTimeFormat.format(new Date(epoch * 1000)) : "–";
}

// Wi-Fi signal: map RSSI to a 0-100 quality and a word people understand.
export function signal(rssi) {
  if (rssi === null || rssi === undefined) return { pct: null, word: "–" };
  const pct = Math.max(0, Math.min(100, 2 * (rssi + 100)));
  const word = rssi >= -55 ? "Excellent" : rssi >= -67 ? "Good" : rssi >= -75 ? "Fair" : "Weak";
  return { pct, word };
}

// Build DOM without innerHTML, so values coming from devices are never parsed as HTML.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function icon(name, className = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", name === "external" ? "0 0 24 24" : "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  if (className) svg.setAttribute("class", className);
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#icon-${name}`);
  svg.append(use);
  return svg;
}

// A status is never color alone: icon + label + color.
const STATUS = {
  up: ["check", "Up"],
  online: ["check", "Online"],
  down: ["cross", "Down"],
  offline: ["cross", "Offline"],
  degraded: ["warn", "Degraded"],
  disabled: ["minus", "Disabled"],
  unknown: ["minus", "Unknown"],
};

export function statusBadge(state) {
  const [iconName, label] = STATUS[state] || STATUS.unknown;
  const kind = state === "online" ? "up" : state === "offline" ? "down" : state;
  return el("span", { class: `status ${kind}` }, icon(iconName), label);
}

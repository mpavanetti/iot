// Recordings: a short clip of each motion event, kept on the server's disk for a while.
// One day at a time, newest first; a clip plays in a dialog, and can be downloaded or deleted.

import { getJSON, send } from "./api.js";
import { bytes, clock, duration, el, integer, number } from "./format.js";

const REFRESH_MS = 30_000;

export class Recordings {
  constructor() {
    this.root = document.getElementById("camera-recordings");
    this.grid = document.getElementById("clips");
    this.dayInput = document.getElementById("recordings-day");
    this.subtitle = document.getElementById("recordings-subtitle");
    this.dialog = document.getElementById("clip-player");
    this.video = document.getElementById("clip-video");
    this.status = null;
    this.clips = [];
    this.open = null; // the clip in the player
    this.timer = null;
    this.root.hidden = false;
    this.dayInput.value = isoDay(new Date());
    this.dayInput.max = this.dayInput.value;
    this.dayInput.addEventListener("change", () => this.load());
    document.getElementById("clip-close").addEventListener("click", () => this.dialog.close());
    document.getElementById("clip-delete").addEventListener("click", () => this.remove(this.open));
    this.dialog.addEventListener("close", () => {
      this.video.pause();
      this.video.removeAttribute("src");
      this.video.load();
      this.open = null;
    });
  }

  start() {
    this.load();
    this.timer ??= setInterval(() => !document.hidden && this.load(), REFRESH_MS);
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
    if (this.dialog.open) this.dialog.close();
  }

  async load() {
    const [year, month, day] = this.dayInput.value.split("-").map(Number);
    const since = new Date(year, month - 1, day).getTime() / 1000;
    const until = new Date(year, month - 1, day + 1).getTime() / 1000;
    try {
      const data = await getJSON("api/camera/recordings", { since, until });
      this.status = data;
      this.clips = data.clips;
    } catch (error) {
      console.error(error);
      return;
    }
    const oldest = new Date(Date.now() - this.status.days * 86_400_000);
    if (this.status.days > 0) this.dayInput.min = isoDay(oldest);
    this.render();
  }

  render() {
    const s = this.status;
    const kept = s.days > 0 ? `kept ${s.days} days` : "kept until the space runs out";
    this.subtitle.textContent = `A short clip of each motion, ${kept} on this machine (at most ${bytes(s.max_bytes)}): ${integer(s.count)} clips, ${bytes(s.size_bytes)} now.`;
    if (!this.clips.length) {
      this.grid.replaceChildren(el("p", { class: "zones-empty" }, "No motion recorded this day."));
      return;
    }
    this.grid.replaceChildren(
      ...this.clips.map((clip) =>
        el(
          "button",
          { class: "clip", type: "button", onclick: () => this.play(clip), "aria-label": `Play the clip of ${clock(clip.start)}` },
          clip.poster ? el("img", { src: url(clip, "jpg"), alt: "", loading: "lazy" }) : el("span", { class: "clip-no-poster" }),
          el("span", { class: "clip-time" }, clock(clip.start)),
          el("span", { class: "clip-meta" }, `${duration(clip.duration_s)} · ${clip.zones?.length ? clip.zones.join(", ") : `${number(clip.peak_pct, 1)} % moved`}`),
        ),
      ),
    );
  }

  play(clip) {
    this.open = clip;
    document.getElementById("clip-title").textContent = `${new Date(clip.start * 1000).toLocaleDateString()} ${clock(clip.start)}`;
    const where = clip.zones?.length ? `at ${clip.zones.join(", ")}` : "";
    document.getElementById("clip-detail").textContent = [duration(clip.duration_s), `peak ${number(clip.peak_pct, 1)} % of the picture`, where, `${clip.width}×${clip.height}, ${bytes(clip.size_bytes)}`].filter(Boolean).join(" · ");
    const download = document.getElementById("clip-download");
    download.href = url(clip, "mp4");
    download.download = `motion-${clip.id}.mp4`;
    this.video.src = url(clip, "mp4");
    this.dialog.showModal();
    this.video.play().catch(() => {}); // autoplay may need a click on some phones
  }

  async remove(clip) {
    if (!clip || !confirm(`Delete the clip of ${clock(clip.start)}?`)) return;
    try {
      await send("DELETE", `api/camera/recordings/${clip.id}`);
    } catch (error) {
      console.error(error);
    }
    this.dialog.close();
    this.load();
  }
}

function url(clip, kind) {
  return new URL(`api/camera/recordings/${clip.id}.${kind}`, document.baseURI).href;
}

function isoDay(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

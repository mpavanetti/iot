// The dashboard's only link to the server. Both editions expose these same endpoints.
// Paths are relative, so the dashboard also works behind a reverse proxy sub-path.

export async function getJSON(path, params = {}) {
  const url = new URL(path, document.baseURI);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) url.searchParams.set(key, value);
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${url.pathname}`);
  return response.json();
}

/** POST/DELETE with an optional JSON body; the server's `detail` becomes the error. */
export async function send(method, path, body) {
  const response = await fetch(new URL(path, document.baseURI), {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      detail = typeof data.detail === "string" ? data.detail : data.detail?.[0]?.msg || detail;
    } catch {}
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

export function exportUrl(deviceId, range) {
  const url = new URL("api/readings/export.csv", document.baseURI);
  url.searchParams.set("device_id", deviceId);
  url.searchParams.set("range", range);
  return url.href;
}

/**
 * Live readings over Server-Sent Events. The browser reconnects by itself after network
 * hiccups; if the server rejects the stream outright we retry every 5 s.
 * onState receives "connecting" | "live" | "offline"; onCamera and onSound, what the camera
 * and the microphone notice.
 */
export function openLiveStream({ onReading, onCamera, onSound, onState, onReconnect }) {
  let source;
  let wasLive = false;

  const connect = () => {
    source = new EventSource(new URL("api/stream", document.baseURI));
    onState("connecting");
    source.addEventListener("hello", () => {
      onState("live");
      if (wasLive) onReconnect?.(); // we may have missed readings while away: refetch
      wasLive = true;
    });
    source.addEventListener("reading", (event) => onReading(JSON.parse(event.data)));
    source.addEventListener("camera", (event) => onCamera?.(JSON.parse(event.data)));
    source.addEventListener("sound", (event) => onSound?.(JSON.parse(event.data)));
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) {
        onState("offline");
        setTimeout(connect, 5000);
      } else {
        onState("connecting");
      }
    };
  };

  connect();
  return () => source.close();
}

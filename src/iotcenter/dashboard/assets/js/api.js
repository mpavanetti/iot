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

export function exportUrl(deviceId, range) {
  const url = new URL("api/readings/export.csv", document.baseURI);
  url.searchParams.set("device_id", deviceId);
  url.searchParams.set("range", range);
  return url.href;
}

/**
 * Live readings over Server-Sent Events. The browser reconnects by itself after network
 * hiccups; if the server rejects the stream outright we retry every 5 s.
 * onState receives "connecting" | "live" | "offline".
 */
export function openLiveStream({ onReading, onState, onReconnect }) {
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

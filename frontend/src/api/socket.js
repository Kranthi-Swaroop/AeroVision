// WebSocket with automatic reconnect.
//
// Reconnect matters more in development than in production: every backend hot
// reload drops the socket, and without this you would be refreshing the
// browser by hand a few hundred times over two days.

const RETRY_MS = 1500;

export function connect({ onMessage, onStatus }) {
  let ws = null;
  let retry = null;
  let closed = false;

  const open = () => {
    if (closed) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.onopen = () => onStatus("live");
    ws.onclose = () => {
      onStatus("down");
      if (!closed) retry = setTimeout(open, RETRY_MS);
    };
    ws.onerror = () => ws?.close();
    ws.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data));
      } catch (err) {
        console.warn("unparseable frame", err);
      }
    };
  };

  open();

  return () => {
    closed = true;
    clearTimeout(retry);
    ws?.close();
  };
}

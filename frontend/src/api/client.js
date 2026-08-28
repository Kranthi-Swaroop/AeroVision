// REST calls. Paths are relative so Vite's dev proxy handles them and the
// production build works behind any reverse proxy without a rebuild.

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    // FastAPI puts the useful message in `detail`; surface it rather than a
    // bare status code, so a failed mission start says why it failed.
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON error body, keep the status line */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const getScene = () => request("/api/scene");
export const getTruth = () => request("/api/truth");
export const getReport = () => request("/api/mission/report");

export const startMission = (polygon) =>
  request("/api/mission/start", {
    method: "POST",
    body: JSON.stringify({ polygon }),
  });

export const stopMission = () =>
  request("/api/mission/stop", { method: "POST" });

export const routeTo = (lat, lon) =>
  request("/api/route", { method: "POST", body: JSON.stringify({ lat, lon }) });

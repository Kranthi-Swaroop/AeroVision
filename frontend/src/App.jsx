import { useState } from "react";
import MapView from "./components/MapView";
import TelemetryPanel from "./components/TelemetryPanel";
import SceneViewer, { DroneCameraFeed, RescueRouteMap } from "./components/SceneViewer";
import { useMission } from "./hooks/useMission";

const STATUS_TEXT = {
  live: "Link established",
  down: "Reconnecting",
  connecting: "Connecting",
};

export default function App() {
  const m = useMission();
  const [view, setView] = useState("3d"); // "map" | "3d"

  const scanEverything = () => {
    if (!m.scene) return;
    const [[south, west], [north, east]] = m.scene.bounds;
    const padLat = (north - south) * 0.08;
    const padLon = (east - west) * 0.08;
    m.start([
      [north - padLat, west + padLon],
      [north - padLat, east - padLon],
      [south + padLat, east - padLon],
      [south + padLat, west + padLon],
    ]);
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="wordmark">
          Aero<span>Vision</span>
        </div>
        <span className="label">Drone-assisted search and rescue mapping</span>

        {/* View toggle */}
        <div className="view-toggle">
          <button
            className={`view-toggle__btn${view === "3d" ? " view-toggle__btn--active" : ""}`}
            onClick={() => setView("3d")}
          >
            3D Scene
          </button>
          <button
            className={`view-toggle__btn${view === "map" ? " view-toggle__btn--active" : ""}`}
            onClick={() => setView("map")}
          >
            Map
          </button>
        </div>

        <div className="topbar__spacer" />

        {m.scene && (
          <span className="readout readout--sm" style={{ color: "var(--ink-dim)" }}>
            {m.scene.size_m[0]}×{m.scene.size_m[1]} m · {m.scene.gsd} m/px
          </span>
        )}

        <span className={`status status--${m.status}`}>
          <span className="status__dot" />
          {STATUS_TEXT[m.status] ?? m.status}
        </span>

        <button
          className="btn btn--go"
          onClick={scanEverything}
          disabled={!m.scene || m.flying}
        >
          Scan whole area
        </button>
        <button className="btn" onClick={m.stop} disabled={!m.flying}>
          Abort
        </button>
      </header>

      <main className="workspace">
        {/* Main view: 3D scene or 2D map */}
        <div className="workspace__main">
          <div className="workspace__viewport">
            {/* 3D Scene — kept mounted so Three.js context persists */}
            <div style={{
              position: "absolute",
              inset: 0,
              opacity: view === "3d" ? 1 : 0,
              pointerEvents: view === "3d" ? "auto" : "none",
              transition: "opacity 0.25s ease",
            }}>
              <SceneViewer
                victims={m.victims}
                telemetry={m.telemetry}
              />
            </div>
            {/* 2D Map — also kept mounted so Leaflet state survives */}
            <div style={{
              position: "absolute",
              inset: 0,
              opacity: view === "map" ? 1 : 0,
              pointerEvents: view === "map" ? "auto" : "none",
              transition: "opacity 0.25s ease",
            }}>
              <MapView
                scene={m.scene}
                telemetry={m.telemetry}
                footprint={m.footprint}
                plan={m.plan}
              />
            </div>
          </div>
          <div className="workspace__telemetry">
            <TelemetryPanel telemetry={m.telemetry} />
          </div>
        </div>

        <aside className="workspace__side">
          {m.error && (
            <section
              className="panel"
              style={{ borderColor: "var(--caution)", padding: "10px 12px" }}
            >
              <div className="label" style={{ color: "var(--caution)" }}>
                Mission error
              </div>
              <p style={{ margin: "4px 0 8px", fontSize: 13 }}>{m.error}</p>
              <button className="btn" onClick={m.dismissError}>
                Dismiss
              </button>
            </section>
          )}

          {m.plan && (
            <section className="panel">
              <div className="panel__head">
                <span className="label">Scan plan</span>
                <span className="readout readout--sm">
                  {(m.plan.area_m2 / 10000).toFixed(2)} ha
                </span>
              </div>
              <div className="grid">
                <div>
                  <div className="label">Legs</div>
                  <div className="cell__value">{m.plan.legs}</div>
                </div>
                <div>
                  <div className="label">Swath</div>
                  <div className="cell__value">
                    {m.plan.swath_m}
                    <small>m</small>
                  </div>
                </div>
                <div>
                  <div className="label">Leg spacing</div>
                  <div className="cell__value">
                    {m.plan.spacing_m}
                    <small>m</small>
                  </div>
                </div>
                <div>
                  <div className="label">Path length</div>
                  <div className="cell__value">
                    {Math.round(m.plan.path_length_m)}
                    <small>m</small>
                  </div>
                </div>
              </div>
            </section>
          )}

          <section className="panel workspace__visual-panel">
            <div className="panel__head">
              <span className="label">Drone camera</span>
              <span className="readout readout--sm">FORWARD · 72°</span>
            </div>
            <DroneCameraFeed />
          </section>

          <section className="panel workspace__visual-panel">
            <div className="panel__head">
              <span className="label">Rescue route map</span>
              <span className="readout readout--sm">LIVE GRID</span>
            </div>
            <RescueRouteMap />
          </section>

          {/* Victims panel */}
          {m.victims.length > 0 && (
            <section className="panel">
              <div className="panel__head">
                <span className="label">Detected victims</span>
                <span className="readout readout--sm">{m.victims.length}</span>
              </div>
              <div style={{ maxHeight: 200, overflowY: "auto" }}>
                {m.victims.map((v) => (
                  <div key={v.id} className="victim-row">
                    <span
                      className="victim-row__dot"
                      style={{
                        background: v.status === "confirmed"
                          ? "var(--detect)"
                          : "var(--caution)",
                      }}
                    />
                    <span className="readout readout--sm">{v.id.toUpperCase()}</span>
                    <span className="label" style={{ marginLeft: "auto" }}>
                      {v.priority_level ?? v.status}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}

        </aside>
      </main>
    </div>
  );
}

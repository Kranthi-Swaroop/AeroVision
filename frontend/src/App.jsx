import { useEffect, useState } from "react";
import MapView from "./components/MapView";
import TelemetryPanel from "./components/TelemetryPanel";
import SceneViewer, { DroneCameraFeed, RescueRouteMap } from "./components/SceneViewer";
import { useMission } from "./hooks/useMission";
import { configureSurveyArea, startSurveyMission, surveyMission } from "./lib/surveyMission";

const STATUS_TEXT = {
  live: "Link established",
  down: "Reconnecting",
  connecting: "Connecting",
};

export default function App() {
  const m = useMission();
  const [view, setView] = useState("3d"); // "map" | "3d"
  const [surveyArea, setSurveyArea] = useState(null);
  const [surveyDrawing, setSurveyDrawing] = useState(false);
  const [surveyAltitude, setSurveyAltitude] = useState(15);
  const [surveySpeed, setSurveySpeed] = useState(4);
  const [, setSurveyRevision] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => setSurveyRevision((value) => value + 1), 400);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const altitude = Math.max(3, Math.min(60, Number(surveyAltitude) || 15));
    const speed = Math.max(0.5, Math.min(12, Number(surveySpeed) || 4));
    configureSurveyArea(surveyArea, altitude, speed);
  }, [surveyArea, surveyAltitude, surveySpeed]);

  const surveyRunning = ["scanning", "confirming", "rtl"].includes(surveyMission.status);
  const searchAreaM2 = surveyArea
    ? Math.abs(surveyArea.maxX - surveyArea.minX) * Math.abs(surveyArea.maxZ - surveyArea.minZ)
    : null;
  const beginSurveyDrawing = () => {
    setView("3d");
    setSurveyDrawing(true);
  };
  const launchSurvey = () => {
    if (!surveyArea) return;
    const altitude = Math.max(3, Math.min(60, Number(surveyAltitude) || 15));
    const speed = Math.max(0.5, Math.min(12, Number(surveySpeed) || 4));
    startSurveyMission(surveyArea, altitude, speed);
    setSurveyDrawing(false);
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

        <div className="survey-controls">
          <button className={`btn${surveyDrawing ? " btn--active" : ""}`} onClick={beginSurveyDrawing} disabled={surveyRunning}>
            {surveyDrawing ? "Drag search zone" : "Draw survey area"}
          </button>
          <label className="survey-controls__field">
            <span>ALT</span>
            <input type="number" min="3" max="60" value={surveyAltitude} disabled={surveyRunning}
              onChange={(event) => setSurveyAltitude(event.target.value)} />
            <small>m</small>
          </label>
          <label className="survey-controls__field">
            <span>SPEED</span>
            <input type="number" min="0.5" max="12" step="0.5" value={surveySpeed} disabled={surveyRunning}
              onChange={(event) => setSurveySpeed(event.target.value)} />
            <small>m/s</small>
          </label>
          <button className="btn btn--go" onClick={launchSurvey} disabled={!surveyArea || surveyRunning}>Launch</button>
          <span className="readout readout--sm survey-controls__status">
            {surveyMission.manualActive ? "MANUAL" : surveyMission.status.toUpperCase()}
          </span>
        </div>
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
                surveyArea={surveyArea}
                surveyDrawing={surveyDrawing}
                onSurveyAreaChange={setSurveyArea}
                onSurveyDrawingChange={setSurveyDrawing}
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
            <TelemetryPanel
              telemetry={m.telemetry}
              surveyStats={{
                areaM2: searchAreaM2,
                coveragePct: surveyMission.coveragePct,
                peopleDetected: surveyMission.detections.length,
                droneAltitude: surveyMission.droneAltitude,
                status: surveyMission.manualActive ? "manual" : surveyMission.status,
              }}
            />
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
              <span className="readout readout--sm">SEARCH · −60° · 72° FOV</span>
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

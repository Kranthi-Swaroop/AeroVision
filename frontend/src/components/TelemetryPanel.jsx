const STATE_LABEL = {
  idle: "Standing by",
  scanning: "Scanning",
  rtl: "Returning to launch",
  landed: "Landed",
};

function Cell({ label, value, unit }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className="cell__value">
        {value}
        {unit && <small>{unit}</small>}
      </div>
    </div>
  );
}

export default function TelemetryPanel({ telemetry }) {
  if (!telemetry) {
    return (
      <section className="panel">
        <div className="panel__head">
          <span className="label">Aircraft</span>
        </div>
        <p className="empty">No telemetry. Start a scan to arm the drone.</p>
      </section>
    );
  }

  const pct = Math.round(telemetry.battery * 100);
  const low = telemetry.battery <= 0.25;

  return (
    <section className="panel">
      <div className="panel__head">
        <span className="label">Aircraft</span>
        <span className="label" style={{ color: "var(--ink-dim)" }}>
          {STATE_LABEL[telemetry.state] ?? telemetry.state}
        </span>
      </div>

      <div className="grid">
        <Cell label="Latitude" value={telemetry.lat.toFixed(6)} unit="°N" />
        <Cell label="Longitude" value={telemetry.lon.toFixed(6)} unit="°E" />
        <Cell label="Altitude" value={telemetry.alt.toFixed(0)} unit="m AGL" />
        <Cell label="Heading" value={telemetry.heading.toFixed(0)} unit="°" />
        <Cell label="Distance flown" value={telemetry.distance_m.toFixed(0)} unit="m" />
        <Cell
          label="Waypoint"
          value={`${telemetry.waypoint_index}/${telemetry.waypoint_total}`}
        />
      </div>

      <div style={{ padding: "10px 12px", borderTop: "1px solid var(--hairline)" }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span className="label">Battery</span>
          <span className="readout readout--sm">
            {pct}% · {Math.round(telemetry.eta_s)}s to reserve
          </span>
        </div>
        <div className="meter">
          <div
            className={`meter__fill${low ? " meter__fill--low" : ""}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </section>
  );
}

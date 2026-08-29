const STATE_LABEL = {
  idle: "Standing by",
  scanning: "Scanning",
  confirming: "Confirming detection",
  rtl: "Returning to launch",
  landed: "Landed",
  complete: "Mission complete",
  manual: "Manual flight",
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

export default function TelemetryPanel({ telemetry, surveyStats }) {
  const status = surveyStats?.status ?? telemetry?.state ?? "idle";
  const area = surveyStats?.areaM2;
  const coverage = surveyStats?.coveragePct ?? 0;
  const altitude = surveyStats?.droneAltitude ?? telemetry?.alt ?? 0;

  return (
    <section className="panel">
      <div className="panel__head">
        <span className="label">Aircraft</span>
        <span className="label" style={{ color: "var(--ink-dim)" }}>
          {STATE_LABEL[status] ?? status}
        </span>
      </div>

      <div className="grid">
        <Cell label="Search zone area" value={area == null ? "--" : Math.round(area).toLocaleString()} unit={area == null ? "" : "m²"} />
        <Cell label="Coverage progress" value={coverage.toFixed(1)} unit="%" />
        <Cell label="People detected" value={surveyStats?.peopleDetected ?? 0} />
        <Cell label="Drone altitude" value={altitude.toFixed(1)} unit="m AGL" />
      </div>

      <div style={{ padding: "7px 12px", borderTop: "1px solid var(--hairline)" }}>
        <div className="meter">
          <div className="meter__fill" style={{ width: `${Math.max(0, Math.min(100, coverage))}%` }} />
        </div>
      </div>
    </section>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";

import { connect } from "../api/socket";
import { getScene, startMission, stopMission } from "../api/client";

/**
 * Single source of truth for everything streaming off the backend.
 *
 * All socket messages land here and fan out as plain state. Components stay
 * dumb and just render what they are given, which keeps the socket wiring in
 * one file instead of scattered through the tree.
 *
 * Frames are deliberately held in a ref rather than state until a paint is
 * due: a base64 JPEG arriving several times a second would otherwise trigger a
 * React re-render per frame and drag the map along with it.
 */
export function useMission() {
  const [status, setStatus] = useState("connecting");
  const [scene, setScene] = useState(null);
  const [telemetry, setTelemetry] = useState(null);
  const [footprint, setFootprint] = useState(null);
  const [plan, setPlan] = useState(null);
  const [victims, setVictims] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [frame, setFrame] = useState(null);
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  const frameRef = useRef(null);
  const pendingFrame = useRef(false);

  useEffect(() => {
    getScene().then(setScene).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    const disconnect = connect({
      onStatus: setStatus,
      onMessage: (msg) => {
        switch (msg.type) {
          case "telemetry":
            setTelemetry(msg.drone);
            setFootprint(msg.footprint);
            break;

          case "frame":
            // Coalesce to one paint per animation frame. The backend may emit
            // faster than the display can show, and dropping the surplus is
            // strictly better than queueing renders we will never see.
            frameRef.current = msg;
            if (!pendingFrame.current) {
              pendingFrame.current = true;
              requestAnimationFrame(() => {
                pendingFrame.current = false;
                setFrame(frameRef.current);
              });
            }
            break;

          case "victims":
            setVictims(msg.victims);
            break;
          case "metrics":
            setMetrics(msg);
            break;
          case "plan":
            setPlan(msg);
            setReport(null);
            break;
          case "mission_complete":
            setReport(msg.report);
            setVictims(msg.report.victims ?? []);
            break;
          case "mission_stopped":
            setPlan(null);
            break;
          case "error":
            setError(msg.message);
            break;
          default:
            break;
        }
      },
    });
    return disconnect;
  }, []);

  const start = useCallback(async (polygon) => {
    setError(null);
    setVictims([]);
    setMetrics(null);
    setReport(null);
    try {
      await startMission(polygon);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const stop = useCallback(async () => {
    try {
      await stopMission();
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const flying =
    telemetry?.state === "scanning" || telemetry?.state === "rtl";

  return {
    status, scene, telemetry, footprint, plan, victims, metrics,
    frame, report, error, flying, start, stop,
    dismissError: () => setError(null),
  };
}

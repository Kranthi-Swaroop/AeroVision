export const surveyMission = {
  status: "idle",
  area: null,
  altitude: 15,
  speed: 4,
  footprint: 0,
  waypoints: [],
  waypointIndex: 0,
  holdUntil: 0,
  detections: [],
  coveredCells: new Set(),
  totalCells: 0,
  coveragePct: 0,
  droneAltitude: 0,
  manualActive: false,
};

export function buildSurveyWaypoints(area, altitude, speed, launch = { x: 0, z: -85 }) {
  const minX = Math.min(area.minX, area.maxX), maxX = Math.max(area.minX, area.maxX);
  const minZ = Math.min(area.minZ, area.maxZ), maxZ = Math.max(area.minZ, area.maxZ);
  // 72° camera FOV projected onto the flood surface with 30% overlap.
  const footprint = Math.max(4, 2 * altitude * Math.tan((72 * Math.PI / 180) / 2));
  const step = footprint * 0.7;
  const xs = [];
  const zs = [];
  for (let x = minX; x < maxX; x += step) xs.push(Math.min(x + step / 2, maxX));
  for (let z = minZ; z < maxZ; z += step) zs.push(Math.min(z + step / 2, maxZ));
  if (!xs.length) xs.push((minX + maxX) / 2);
  if (!zs.length) zs.push((minZ + maxZ) / 2);
  const y = 2.2 + altitude;
  const scan = zs.flatMap((z, row) => (row % 2 ? [...xs].reverse() : xs)
    .filter((x) => Math.hypot(x, z - launch.z) <= 128)
    .map((x) => ({ x, y, z, scan: true })));
  return {
    footprint,
    waypoints: [{ x: launch.x, y, z: launch.z, scan: false }, ...scan, { x: launch.x, y, z: launch.z, scan: false }, { x: launch.x, y: 2.2, z: launch.z, scan: false }],
    speed,
  };
}

export function startSurveyMission(area, altitude, speed) {
  const plan = buildSurveyWaypoints(area, altitude, speed);
  Object.assign(surveyMission, {
    status: "scanning", area, altitude, speed, footprint: plan.footprint,
    waypoints: plan.waypoints, waypointIndex: 0, holdUntil: 0, detections: [],
    coveredCells: new Set(), totalCells: Math.max(1, plan.waypoints.filter((point) => point.scan).length),
    coveragePct: 0,
  });
}

export function configureSurveyArea(area, altitude, speed) {
  if (!area) return;
  const signature = [area.minX, area.maxX, area.minZ, area.maxZ, altitude, speed].join(":");
  if (surveyMission.configurationSignature === signature) return;
  const plan = buildSurveyWaypoints(area, altitude, speed);
  Object.assign(surveyMission, {
    area, altitude, speed, footprint: plan.footprint,
    coveredCells: new Set(), totalCells: Math.max(1, plan.waypoints.filter((point) => point.scan).length),
    coveragePct: 0, configurationSignature: signature,
  });
}

export function markSurveyCoverage(x, z) {
  const area = surveyMission.area;
  if (!area || surveyMission.footprint <= 0) return;
  const minX = Math.min(area.minX, area.maxX), maxX = Math.max(area.minX, area.maxX);
  const minZ = Math.min(area.minZ, area.maxZ), maxZ = Math.max(area.minZ, area.maxZ);
  if (x < minX || x > maxX || z < minZ || z > maxZ) return;
  const step = surveyMission.footprint * 0.7;
  const col = Math.floor((x - minX) / step);
  const row = Math.floor((z - minZ) / step);
  surveyMission.coveredCells.add(`${col}:${row}`);
  surveyMission.coveragePct = Math.min(100, (surveyMission.coveredCells.size / Math.max(1, surveyMission.totalCells)) * 100);
}

export function registerSurveyDetection(detection) {
  const autonomous = surveyMission.status === "scanning";
  if ((!autonomous && !surveyMission.manualActive) || detection.confidence < 0.50) return false;
  if (surveyMission.detections.some((item) => Math.hypot(item.x - detection.x, item.z - detection.z) < 7)) return false;

  const latitude = 21.2514 - (detection.z + 85) / 111320;
  const longitude = 81.6296 + detection.x / (111320 * Math.cos(21.2514 * Math.PI / 180));
  surveyMission.detections.push({ ...detection, latitude, longitude, id: surveyMission.detections.length + 1 });
  if (autonomous) {
    surveyMission.status = "confirming";
    surveyMission.holdUntil = performance.now() + 2200;
  }
  return true;
}

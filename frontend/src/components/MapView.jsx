import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

const CSS = getComputedStyle(document.documentElement);
const colour = (name, fallback) =>
  CSS.getPropertyValue(name).trim() || fallback;

const TRACK = colour("--track", "#4a9fd8");
const DETECT = colour("--detect", "#e8368f");

/** Top-down drone glyph, rotated to the current heading. */
function droneIcon(heading) {
  return L.divIcon({
    className: "drone-icon",
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    html: `
      <svg width="30" height="30" viewBox="0 0 30 30"
           style="transform: rotate(${heading}deg)">
        <circle cx="15" cy="15" r="13" fill="${TRACK}" fill-opacity="0.14"/>
        <path d="M15 3 L21 21 L15 17 L9 21 Z" fill="${TRACK}"
              stroke="#0c1116" stroke-width="1" stroke-linejoin="round"/>
      </svg>`,
  });
}

/**
 * The map is imperative Leaflet inside a React shell rather than react-leaflet.
 *
 * Telemetry arrives ten times a second, and moving a marker by mutating it is
 * far cheaper than reconciling a component tree at that rate. It also keeps
 * the Leaflet API visible instead of hidden behind a wrapper, which matters
 * when something needs debugging at 2am.
 */
export default function MapView({ scene, telemetry, footprint, plan }) {
  const container = useRef(null);
  const map = useRef(null);
  const layers = useRef({});

  // --- create the map once the scene bounds are known ---------------------
  useEffect(() => {
    if (!scene || map.current) return;

    const bounds = L.latLngBounds(scene.bounds[0], scene.bounds[1]);
    const m = L.map(container.current, {
      zoomControl: true,
      attributionControl: true,
    }).fitBounds(bounds);

    L.imageOverlay(scene.ortho_url, bounds, {
      attribution:
        'Imagery &copy; OpenAerialMap contributors, Open Imagery Network (CC-BY 4.0)',
    }).addTo(m);

    m.setMaxBounds(bounds.pad(0.4));

    // Water polygons: the hazard layer the triage scoring actually reads.
    scene.water?.forEach((ring) => {
      L.polygon(ring, {
        color: TRACK,
        weight: 1,
        opacity: 0.5,
        fillColor: TRACK,
        fillOpacity: 0.16,
        interactive: false,
      }).addTo(m);
    });

    L.circleMarker(scene.base, {
      radius: 6,
      color: "#e6ecf1",
      weight: 2,
      fillColor: "#10151a",
      fillOpacity: 1,
    })
      .bindTooltip("Staging point", { direction: "top" })
      .addTo(m);

    map.current = m;
    return () => {
      m.remove();
      map.current = null;
    };
  }, [scene]);

  // --- planned flight path ------------------------------------------------
  useEffect(() => {
    const m = map.current;
    if (!m) return;

    layers.current.plan?.remove();
    layers.current.area?.remove();
    if (!plan) return;

    layers.current.area = L.polygon(plan.polygon, {
      color: "#e6ecf1",
      weight: 1.5,
      dashArray: "5 5",
      fill: false,
      interactive: false,
    }).addTo(m);

    layers.current.plan = L.polyline(plan.waypoints, {
      color: TRACK,
      weight: 1.5,
      opacity: 0.65,
      interactive: false,
    }).addTo(m);
  }, [plan]);

  // --- drone position, heading, track and camera footprint ----------------
  useEffect(() => {
    const m = map.current;
    if (!m || !telemetry) return;
    const pos = [telemetry.lat, telemetry.lon];

    if (!layers.current.drone) {
      layers.current.drone = L.marker(pos, {
        icon: droneIcon(telemetry.heading),
        zIndexOffset: 1000,
      }).addTo(m);
      // Flown track, as distinct from the planned path: the difference between
      // the two is visible evidence the drone is following its mission.
      layers.current.trail = L.polyline([pos], {
        color: TRACK,
        weight: 2.5,
        opacity: 0.9,
      }).addTo(m);
    } else {
      layers.current.drone.setLatLng(pos);
      layers.current.drone.setIcon(droneIcon(telemetry.heading));
      layers.current.trail.addLatLng(pos);
    }

    if (footprint?.length) {
      if (!layers.current.footprint) {
        layers.current.footprint = L.polygon(footprint, {
          color: DETECT,
          weight: 1,
          opacity: 0.55,
          fillColor: DETECT,
          fillOpacity: 0.09,
          interactive: false,
        }).addTo(m);
      } else {
        layers.current.footprint.setLatLngs(footprint);
      }
    }
  }, [telemetry, footprint]);

  // Clear the flown track when a new mission is planned.
  useEffect(() => {
    if (plan && layers.current.trail) {
      layers.current.trail.setLatLngs([]);
    }
  }, [plan]);

  return <div ref={container} className="map" />;
}

/**
 * SceneViewer — interactive 3D flood environment using Three.js / R3F.
 *
 * Renders the flood GLTF scene with:
 *  - Animated rising water plane (semi-transparent)
 *  - Reflective water surface via MeshPhysicalMaterial
 *  - Fog for atmosphere
 *  - Orbit controls (drag to orbit, scroll to zoom)
 *  - Drone camera path preview
 *  - Person markers (male / female GLTFs) placed at detected victim positions
 *
 * Optimisation choices:
 *  - useGLTF.preload() defers heavy parsing to a worker
 *  - Suspend boundary + Loader overlay instead of blocking the UI
 *  - The flood scene uses its own baked textures — we skip shadow maps to
 *    avoid the full-scene re-render they require at 60 fps
 *  - Water plane is a single 200×200 segment quad, not a subdivided mesh
 *  - Drei's <Environment> uses the "sunset" preset (equirect inline, ~5 kB)
 */

import { Suspense, useRef, useMemo, useEffect, useState, useCallback } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import {
  useGLTF,
  useTexture,
  MapControls,
  Environment,
  Html,
  useProgress,
} from "@react-three/drei";
import * as THREE from "three";
import { clone as cloneSkeleton } from "three/addons/utils/SkeletonUtils.js";
import { planRescueRoutes } from "../lib/rescuePlanner";
import { markSurveyCoverage, registerSurveyDetection, surveyMission } from "../lib/surveyMission";

// ── paths served from /public/models/ ────────────────────────────────────────
const FLOOD_GLTF = "/models/flood/309aca03bca744a49509cfb4069981bc.gltf";
const MALE_GLTF  = "/models/person4/scene.gltf";
const FEMALE_GLTF = "/models/person3/scene.gltf";
const DRONE_GLTF = "/models/drone/scene.gltf";
const PERSON_GLTF_PATHS = [1, 2, 3, 4].map((id) => `/models/person${id}/scene.gltf`);
const OBSTACLE_GLTF_PATHS = { rock1: "/models/rockdebris1/scene.gltf", rock2: "/models/rockdebris2/scene.gltf", tree: "/models/tree/scene.gltf" };
const FLOOD_CENTER_Z = -85;
const DRONE_MODEL_SCALE = 1.0;
const DRONE_PARKED_Y = 2.2;

// Mutable render-time pose shared by the aircraft and its FPV camera without
// causing React dashboard updates on every animation frame.
const liveDronePose = {
  x: 0,
  y: DRONE_PARKED_Y,
  z: FLOOD_CENTER_Z,
  yaw: 0,
  launched: false,
};

// The supplied flood export contains UVs, but its GLTF material table does not
// reference any of the images shipped beside it.  Match the original object
// names to their diffuse maps here. More-specific variants must come first.
const FLOOD_TEXTURE_RULES = [
  [/(?:md_)?houses_close_01/i, "daf5072a41d94e7cbd1aedb0f55e4796_RGB_Md_Houses_close_01_Dif[A62123].jpeg"],
  [/(?:md_)?houses_close_02/i, "275f74e575714332b21018b094b3aefe_RGB_Md_Houses_close_02_Dif[A62124].jpeg"],
  [/md_houses_01_03/i, "cad7ac2df61944a7b6191caa72853a2e_RGB_Md_Houses_01_B_Dif[A62125].jpeg"],
  [/md_houses_01_02/i, "54fa028a9a5c492da98112058b2f4d28_RGB_Md_Houses_01_A_Dif[A62125].jpeg"],
  [/md_houses_01/i, "121990536de14c36b94feb37b4528729_RGB_Md_Houses_01_Dif[A62125].jpeg"],
  [/md_houses_02_02/i, "f6cda5a3e2e44204aa961c92230a2908_RGB_Md_Houses_02_A_Dif[A62125].jpeg"],
  [/md_houses_02/i, "644d30df318b4065bdaae01a87ac14a9_RGB_Md_Houses_02_Dif[A62125].jpeg"],
  [/md_houses_03_03/i, "9c5cb30c7e284b378f45a16e5b616ed4_RGB_Md_Houses_03_B_Dif[A62125].jpeg"],
  [/md_houses_03_02/i, "6fa92a8f9ed040999ecd3172584b41aa_RGB_Md_Houses_03_A_Dif[A62125].jpeg"],
  [/md_houses_03/i, "026264c69f6a46478293fa025d39f3ce_RGB_Md_Houses_03_Dif[A62125].jpeg"],
  [/md_houses_04_02/i, "e1dbee5f86ef40f9aea202d15dca38bc_RGB_Md_Houses_04_A_Dif[A62125].jpeg"],
  [/md_houses_04/i, "ab628d49bfb04ba5be3464cde51390fa_RGB_Md_Houses_04_Dif[A62125].jpeg"],
  [/(?:md|md)_houses_05_03/i, "d60bb91f52f542039b284a0cf690e682_RGB_Md_Houses_05_B_Dif[A62125].jpeg"],
  [/(?:md|md)_houses_05_02/i, "06718496e6184f80b4fd59b2b6a46154_RGB_Md_Houses_05_A_Dif[A62125].jpeg"],
  [/(?:md|md)_houses_05/i, "50b976f450724de89c1ee265ae193906_RGB_Md_Houses_05_Dif[A62125].jpeg"],
  [/md_houses_06_03/i, "36dc2e6689b041b2899b26bce3761009_RGB_Md_Houses_06_B_Dif[A62125].jpeg"],
  [/md_houses_06_02/i, "aef337ace0164946b38f5d1570b6d3a1_RGB_Md_Houses_06_A_Dif[A62125].jpeg"],
  [/md_houses_06/i, "9c1732cf92f346d1abc934e0cc72ed05_RGB_Md_Houses_06_Dif[A62125].jpeg"],
  [/md_houses_07/i, "b087e32b92a04e23bfb74a15d8d5f342_RGB_Md_Houses_07_Dif[A62125].jpeg"],
  [/md_houses_08_03/i, "fc49312cea394128afe84043cb4f2a9e_RGB_Md_Houses_08_B_Dif[A62125].jpeg"],
  [/md_houses_08_02/i, "8295e28a5bdb48fcb90d62446e5dd894_RGB_Md_Houses_08_A_Dif[A62125].jpeg"],
  [/md_houses_08/i, "ddbeafe3767246fdb1c259ac6caadc56_RGB_Md_Houses_08_Dif[A62125].jpeg"],
  [/sangeyuanfloor_0002/i, "11731376b5974226a5323b19ccd6d68e_RGB_I__sangeyuafloor_0002_building_CO_0002[A62110].jpeg"],
  [/sangeyuanfloor/i, "49a7b038bffe4e0cb7abe42bf617e2ea_RGB_I__sangeyuafloor_0002_building_CO_0001[A62110].jpeg"],
  [/sangeyuanroof_0007/i, "b9e9bc3cf3124b468c952258fae3f72f_RGB_I__sangeyuanroof_0001_CO_0007.jpeg"],
  [/sangeyuanroof/i, "8c9f4644797f41478519e90a81bd7fb8_RGB_I__sangeyuanroof_0001_CO_0004.jpeg"],
  [/sangeyuan.*door|sangeyuandoor/i, "a98b7c03ce85406493e532d3b1196917_RGB_I__sangeyuandoor_0002_building_CO_0001[A62110].jpeg"],
  [/sangeyuan.*window/i, "41e55ac60a8d4c44962e5b20d7a8c64e_RGB_I__sangeyuanwindow_0002_building_CO_0001[A62110].jpeg"],
  [/sangeyuan.*pillar/i, "d43138db01864ecfab1daf1cb7cadcf1_RGB_I__sangeyuawall_0002_pillar_CO_0001[A62110].jpeg"],
  [/sangeyuan.*wall|b_sangeyuan_0002/i, "d2cd88963d344a5a9958f336f0176288_RGB_I__sangeyuawall_0002_building_CO_0001[A62110].jpeg"],
  [/sanitary.*065/i, "c9c642017ab345f8b4a2f49d2710265c_RGB_I_Sanitary_Co_065.jpeg"],
  [/sanitary.*021/i, "c96d94826ed84bf2ab79edca1c1b7c87_RGB_I_Sanitary_Co_021_1.jpeg"],
  [/chair.*067/i, "4ffeeea5610c4f24b4334c3dca38deb0_RGB_I_Chair_Co_067.jpeg"],
  [/desk.*038/i, "67c7dc6ac25e4c35b5a986119cf4551f_RGB_I_Desk_Co_038_2_2.jpeg"],
  [/cartofca/i, "c2a38539d8b047729d14bcb3eb683caf_RGB_I_Shop_1008_CartOfcA_Co_0_001.jpeg"],
  [/box.*004/i, "e71344f6fe7f43a9a93d89e9c2a734b9_RGB_I_Box_Co_004_1.jpeg"],
  [/lowtree.*0004/i, "183908130ce2480f87cc9b471c9fe83e_RGB_I_LowTree_Co_0004.jpeg"],
  [/lowtree/i, "1a90de75a2ea405c89195a0227004b3f_RGB_I_LowTree_Co_0001.jpeg"],
  [/farhouse/i, "bc4225eed42149ca9304a4c442cb4729_RGB_I_Farhouse_Co_0002[A55251].jpeg"],
  [/church/i, "4b5f704f9a28414497e8137859cbb4e6_RGB_I_Church_1001_Interior_Co_0001[A62110].jpeg"],
  [/sofa.*002/i, "2839c86197bd4900b09d7ec4cb367053_RGB_B_AM_Sofa_0_003_Co_002[A12515].jpeg"],
  [/sofa/i, "d2204c46b02548b4aae1d3efc61919b9_RGB_B_AM_Sofa_0_003_Co_001[A12515].jpeg"],
];

const floodTextureUrl = (file) => `/models/flood/${encodeURIComponent(file)}`;
const floodTextureUrls = [...new Set(FLOOD_TEXTURE_RULES.map(([, file]) =>
  floodTextureUrl(file)
))];

// Pre-warm the GLTF loader so parsing starts before the component mounts
useGLTF.preload(FLOOD_GLTF);
useGLTF.preload(DRONE_GLTF);
PERSON_GLTF_PATHS.forEach((path) => useGLTF.preload(path));
Object.values(OBSTACLE_GLTF_PATHS).forEach((path) => useGLTF.preload(path));

// ── Loading overlay ───────────────────────────────────────────────────────────
function Loader() {
  const { progress } = useProgress();
  return (
    <Html center>
      <div style={{
        color: "#e6ecf1",
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 13,
        textAlign: "center",
        userSelect: "none",
      }}>
        <div style={{ marginBottom: 8, color: "#4a9fd8", letterSpacing: "0.1em", fontSize: 11 }}>
          LOADING SCENE
        </div>
        <div style={{
          width: 180,
          height: 3,
          background: "#2a343d",
          borderRadius: 2,
          overflow: "hidden",
        }}>
          <div style={{
            height: "100%",
            width: `${progress}%`,
            background: "#4a9fd8",
            transition: "width 0.2s ease",
            borderRadius: 2,
          }} />
        </div>
        <div style={{ marginTop: 6, color: "#61717e" }}>{Math.round(progress)}%</div>
      </div>
    </Html>
  );
}

// ── Flood scene model ─────────────────────────────────────────────────────────
function FloodScene() {
  const { scene } = useGLTF(FLOOD_GLTF);
  const loadedTextures = useTexture(floodTextureUrls);

  const textureByUrl = useMemo(() => {
    const result = new Map();
    floodTextureUrls.forEach((url, index) => {
      const texture = loadedTextures[index];
      texture.flipY = false;
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = 4;
      result.set(url, texture);
    });
    return result;
  }, [loadedTextures]);

  // Clone so we don't mutate the cached asset
  const cloned = useMemo(() => {
    const copy = scene.clone(true);
    copy.traverse((obj) => {
      if (!obj.isMesh) return;

      // This export includes a 150,000-unit decorative sky dome. It surrounds
      // the useful circular map and easily blocks the camera when orbiting.
      if (/skytree.*sky/i.test(obj.name)) {
        obj.visible = false;
        return;
      }

      // The exporter left the actual elevated flood surface pure black. It is
      // already the correctly shaped circular mesh, so retain its geometry and
      // restore a muddy, reflective flood-water material like the source scene.
      if (obj.material?.name === "Material_Sea_0001") {
        obj.material = new THREE.MeshPhysicalMaterial({
          name: "FloodWater",
          color: "#57532f",
          roughness: 0.38,
          metalness: 0.0,
          reflectivity: 0.55,
          clearcoat: 0.35,
          clearcoatRoughness: 0.38,
          envMapIntensity: 0.65,
          side: THREE.DoubleSide,
        });
        return;
      }

      if (!obj.geometry.attributes.uv) return;
      const rule = FLOOD_TEXTURE_RULES.find(([matcher]) => matcher.test(obj.name));
      if (!rule) return;

      // GLTFLoader shares material instances. Clone before assigning a map so
      // differently textured buildings do not overwrite one another.
      obj.material = obj.material.clone();
      obj.material.map = textureByUrl.get(floodTextureUrl(rule[1]));
      obj.material.color.set(0xffffff);
      obj.material.needsUpdate = true;
    });
    return copy;
  }, [scene, textureByUrl]);

  useEffect(() => {
    cloned.traverse((obj) => {
      if (obj.isMesh) {
        obj.castShadow = false;
        obj.receiveShadow = false;
        if (obj.material) {
          obj.material.envMapIntensity = 0.4;
          obj.material.needsUpdate = true;
        }
      }
    });
  }, [cloned]);

  return <primitive object={cloned} dispose={null} />;
}

// ── Person marker ─────────────────────────────────────────────────────────────
function PersonMarker({ position = [0, 0, 0], gender = "male", label = "V1", status = "confirmed" }) {
  const gltfPath = gender === "female" ? FEMALE_GLTF : MALE_GLTF;
  const { scene } = useGLTF(gltfPath);
  const cloned = useMemo(() => scene.clone(true), [scene]);
  const groupRef = useRef();
  const color = status === "confirmed" ? "#e8368f" : "#e8a33d";

  useFrame(({ clock }) => {
    if (groupRef.current) {
      groupRef.current.position.y = position[1] + 0.05 * Math.sin(clock.elapsedTime * 1.5 + position[0]);
    }
  });

  return (
    <group ref={groupRef} position={position}>
      <primitive object={cloned} scale={[1, 1, 1]} dispose={null} />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.5, 0]}>
        <ringGeometry args={[0.4, 0.55, 32]} />
        <meshBasicMaterial color={color} transparent opacity={0.7} depthWrite={false} />
      </mesh>
      <Html distanceFactor={10} position={[0, 2.2, 0]} center>
        <div style={{
          background: "rgba(16,21,26,0.88)",
          border: `1px solid ${color}`,
          color,
          fontFamily: "'IBM Plex Mono', monospace",
          fontSize: 11,
          padding: "2px 6px",
          borderRadius: 2,
          letterSpacing: "0.06em",
          whiteSpace: "nowrap",
          pointerEvents: "none",
        }}>
          {label}
        </div>
      </Html>
    </group>
  );
}

// Victim locations mapped from the four coloured markers in the TOP view.
// Person IDs follow the marker order: red, green, pink, yellow.
const FLOOD_PEOPLE = [
  // Red: submerged in the walled courtyard.
  { id: 1, marker: "red", position: [47.9, -1.15, -145.2], rotation: 0.35 },
  // Green: partially submerged beside the shop courtyard.
  { id: 2, marker: "green", position: [-113.3, -0.85, -99.3], rotation: -0.35 },
  // Pink: standing on the upper terrace shown in the reference.
  { id: 3, marker: "pink", position: [36.3, 10.05, -150.0], rotation: 2.75 },
  // Yellow: submerged to the upper torso beside the blue building.
  { id: 4, marker: "yellow", position: [81.7, -1.2, -105.7], rotation: -2.75 },
];

const PERSON_HEIGHT = 2.1;
const PERSON_POSE_STORAGE_KEY = "aerovision-person-poses-v2";

function readSavedPersonPoses() {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(PERSON_POSE_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

// Shared mutable poses let the main scene editor and the drone FPV canvas show
// the same live person locations without triggering React updates every frame.
const savedPersonPoses = readSavedPersonPoses();
const livePersonPoses = new Map(
  FLOOD_PEOPLE.map(({ id, position, rotation }) => [
    id,
    savedPersonPoses[id] || { x: position[0], y: position[1], z: position[2], rotation },
  ]),
);
let activePersonControlId = null;
let activeObstacleControlId = null;

function FloodPerson({ id, marker, selected = false, selectable = false, onSelect }) {
  const { scene } = useGLTF(PERSON_GLTF_PATHS[id - 1]);
  const groupRef = useRef();
  const model = useMemo(() => {
    // Measure a plain hierarchy clone. Its authored/static geometry bounds are
    // reliable for these files, while their live SkinnedMesh bounds are not.
    const measurement = scene.clone(true);
    measurement.updateWorldMatrix(true, true);
    const bounds = new THREE.Box3().setFromObject(measurement, true);

    // Three of the supplied characters are SkinnedMeshes. Object3D.clone()
    // leaves their skeletons associated with the source hierarchy, so use the
    // Three.js skeleton-aware clone before moving each model independently.
    const copy = cloneSkeleton(scene);
    copy.traverse((obj) => {
      if (!obj.isMesh) return;
      obj.castShadow = false;
      obj.receiveShadow = false;
      obj.frustumCulled = false;
    });

    // The downloaded models use different authoring units (metres and
    // centimetres). Normalize their evaluated bounds so every person is the
    // same visible height, centred over its marker and standing on its feet.
    const size = bounds.getSize(new THREE.Vector3());
    const center = bounds.getCenter(new THREE.Vector3());
    const scale = size.y > 0 ? PERSON_HEIGHT / size.y : 1;

    copy.scale.setScalar(scale);
    copy.position.set(-center.x * scale, -bounds.min.y * scale, -center.z * scale);
    copy.updateMatrixWorld(true);
    return copy;
  }, [scene]);

  useFrame(() => {
    const group = groupRef.current;
    const pose = livePersonPoses.get(id);
    if (!group || !pose) return;
    group.position.set(pose.x, pose.y, pose.z);
    group.rotation.y = pose.rotation;
    group.userData.worldPosition = [pose.x, pose.y, pose.z];
  });

  const pose = livePersonPoses.get(id);
  const selectThisPerson = (event) => {
    if (!selectable) return;
    event.stopPropagation();
    onSelect?.(id);
  };

  return (
    <group
      ref={groupRef}
      name={`flood-person-${id}-${marker}`}
      userData={{ personId: id, marker, worldPosition: [pose.x, pose.y, pose.z] }}
      position={[pose.x, pose.y, pose.z]}
      rotation={[0, pose.rotation, 0]}
      onClick={selectable ? selectThisPerson : undefined}
    >
      <primitive object={model} dispose={null} />
      {selectable && (
        <mesh position={[0, PERSON_HEIGHT / 2, 0]} onPointerDown={selectThisPerson}>
          <cylinderGeometry args={[1.3, 1.3, PERSON_HEIGHT + 0.8, 16]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
      )}
    </group>
  );
}

function FloodPeople({ selectable = false, selectedId = null, onSelect }) {
  return FLOOD_PEOPLE.map((person) => (
    <FloodPerson
      key={person.id}
      {...person}
      selectable={selectable}
      selected={person.id === selectedId}
      onSelect={onSelect}
    />
  ));
}

function PersonControlSystem({ selectedId, onCommit }) {
  const keysRef = useRef(new Set());

  useEffect(() => {
    if (selectedId == null) return undefined;
    activePersonControlId = selectedId;
    const controlledKeys = new Set([
      "KeyW", "KeyA", "KeyS", "KeyD",
      "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter",
    ]);
    const onKeyDown = (event) => {
      if (!controlledKeys.has(event.code) || /input|textarea|select/i.test(event.target?.tagName)) return;
      event.preventDefault();
      if (event.code === "Enter") {
        keysRef.current.clear();
        onCommit();
        return;
      }
      keysRef.current.add(event.code);
    };
    const onKeyUp = (event) => {
      if (!controlledKeys.has(event.code)) return;
      event.preventDefault();
      keysRef.current.delete(event.code);
    };
    window.addEventListener("keydown", onKeyDown, { passive: false });
    window.addEventListener("keyup", onKeyUp, { passive: false });
    return () => {
      keysRef.current.clear();
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [selectedId, onCommit]);

  useFrame((_, delta) => {
    if (selectedId == null) return;
    const pose = livePersonPoses.get(selectedId);
    if (!pose) return;
    const keys = keysRef.current;
    const forward = (keys.has("KeyW") ? 1 : 0) - (keys.has("KeyS") ? 1 : 0);
    const strafe = (keys.has("KeyD") ? 1 : 0) - (keys.has("KeyA") ? 1 : 0);
    const climb = (keys.has("ArrowUp") ? 1 : 0) - (keys.has("ArrowDown") ? 1 : 0);
    const yaw = (keys.has("ArrowLeft") ? 1 : 0) - (keys.has("ArrowRight") ? 1 : 0);
    const moveSpeed = 5;
    const climbSpeed = 3;
    const yawSpeed = 1.8;
    const sin = Math.sin(pose.rotation);
    const cos = Math.cos(pose.rotation);

    pose.x += (strafe * cos - forward * sin) * moveSpeed * delta;
    pose.z += (-forward * cos - strafe * sin) * moveSpeed * delta;
    // Allow victims to be staged below the flood surface for drowning and
    // partially submerged rescue scenarios.
    pose.y = THREE.MathUtils.clamp(pose.y + climb * climbSpeed * delta, -15, 30);
    pose.rotation += yaw * yawSpeed * delta;

    const dx = pose.x;
    const dz = pose.z - FLOOD_CENTER_Z;
    const distance = Math.hypot(dx, dz);
    if (distance > 132) {
      pose.x = (dx / distance) * 132;
      pose.z = FLOOD_CENTER_Z + (dz / distance) * 132;
    }
  });

  return null;
}

// Edit these three values to resize an entire obstacle family and all 10 copies.
const OBSTACLE_MODEL_SIZES = {
  rock1: 15,
  rock2: 15,
  tree: 9,
};

// Ten copies of each asset are distributed across separate rings initially.
// Their edited positions are subsequently restored from browser storage.
const OBSTACLE_FAMILIES = [
  { type: "rock1", radius: 46, angleOffset: 0.05 },
  { type: "rock2", radius: 80, angleOffset: 0.25 },
  { type: "tree", radius: 116, angleOffset: 0.45 },
];
const FLOOD_OBSTACLES = OBSTACLE_FAMILIES.flatMap(({ type, radius, angleOffset }) =>
  Array.from({ length: 10 }, (_, index) => {
    const angle = angleOffset + (index / 10) * Math.PI * 2;
    return {
      id: `${type}-${String.fromCharCode(97 + index)}`,
      type,
      position: [Math.cos(angle) * radius, 0.5, FLOOD_CENTER_Z + Math.sin(angle) * radius],
      rotation: angle + Math.PI / 2,
    };
  }),
);
const OBSTACLE_POSE_STORAGE_KEY = "aerovision-obstacle-poses-v1";

function readSavedObstaclePoses() {
  if (typeof window === "undefined") return {};
  try { return JSON.parse(window.localStorage.getItem(OBSTACLE_POSE_STORAGE_KEY) || "{}"); }
  catch { return {}; }
}

const savedObstaclePoses = readSavedObstaclePoses();
const liveObstaclePoses = new Map(FLOOD_OBSTACLES.map(({ id, position, rotation }) => [
  id, savedObstaclePoses[id] || { x: position[0], y: position[1], z: position[2], rotation },
]));

function FloodObstacle({ id, type, selectable = false, onSelect }) {
  const { scene } = useGLTF(OBSTACLE_GLTF_PATHS[type]);
  const groupRef = useRef();
  const model = useMemo(() => {
    const copy = scene.clone(true);
    copy.updateWorldMatrix(true, true);
    const bounds = new THREE.Box3().setFromObject(copy, true);
    const size = bounds.getSize(new THREE.Vector3());
    const center = bounds.getCenter(new THREE.Vector3());
    const targetSize = OBSTACLE_MODEL_SIZES[type];
    const sourceSize = type === "tree" ? size.y : Math.max(size.x, size.z);
    const scale = sourceSize > 0 ? targetSize / sourceSize : 1;
    copy.scale.setScalar(scale);
    copy.position.set(-center.x * scale, -bounds.min.y * scale, -center.z * scale);
    copy.traverse((object) => {
      if (!object.isMesh) return;
      object.castShadow = false;
      object.receiveShadow = false;
      object.frustumCulled = false;
    });
    return copy;
  }, [scene, type]);
  const pose = liveObstaclePoses.get(id);

  useFrame(() => {
    if (!groupRef.current || !pose) return;
    groupRef.current.position.set(pose.x, pose.y, pose.z);
    groupRef.current.rotation.y = pose.rotation;
  });
  const selectObstacle = (event) => { if (selectable) { event.stopPropagation(); onSelect?.(id); } };

  return (
    <group ref={groupRef} position={[pose.x, pose.y, pose.z]} rotation={[0, pose.rotation, 0]}
      name={`flood-obstacle-${id}`} onClick={selectable ? selectObstacle : undefined}>
      <primitive object={model} dispose={null} />
      {selectable && (
        <mesh position={[0, type === "tree" ? 4.5 : 1.5, 0]} onPointerDown={selectObstacle}>
          <cylinderGeometry args={[type === "tree" ? 2.2 : 3.2, type === "tree" ? 2.2 : 3.2, type === "tree" ? 10 : 3.5, 16]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
      )}
    </group>
  );
}

function FloodObstacles({ selectable = false, onSelect }) {
  return FLOOD_OBSTACLES.map((obstacle) => <FloodObstacle key={obstacle.id} {...obstacle} selectable={selectable} onSelect={onSelect} />);
}

function ObstacleControlSystem({ selectedId, onCommit }) {
  const keysRef = useRef(new Set());
  useEffect(() => {
    if (selectedId == null) return undefined;
    activeObstacleControlId = selectedId;
    const controlled = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter"]);
    const down = (event) => {
      if (!controlled.has(event.code) || /input|textarea|select/i.test(event.target?.tagName)) return;
      event.preventDefault();
      if (event.code === "Enter") { keysRef.current.clear(); onCommit(); return; }
      keysRef.current.add(event.code);
    };
    const up = (event) => { if (controlled.has(event.code)) keysRef.current.delete(event.code); };
    window.addEventListener("keydown", down, { passive: false });
    window.addEventListener("keyup", up, { passive: false });
    return () => { keysRef.current.clear(); window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [selectedId, onCommit]);

  useFrame((_, delta) => {
    const pose = liveObstaclePoses.get(selectedId);
    if (!pose) return;
    const keys = keysRef.current;
    const forward = Number(keys.has("KeyW")) - Number(keys.has("KeyS"));
    const strafe = Number(keys.has("KeyD")) - Number(keys.has("KeyA"));
    const climb = Number(keys.has("ArrowUp")) - Number(keys.has("ArrowDown"));
    const yaw = Number(keys.has("ArrowLeft")) - Number(keys.has("ArrowRight"));
    const sin = Math.sin(pose.rotation), cos = Math.cos(pose.rotation);
    pose.x += (strafe * cos - forward * sin) * 6 * delta;
    pose.z += (-forward * cos - strafe * sin) * 6 * delta;
    pose.y = THREE.MathUtils.clamp(pose.y + climb * 4 * delta, -8, 35);
    pose.rotation += yaw * 1.8 * delta;
    const dx = pose.x, dz = pose.z - FLOOD_CENTER_Z, distance = Math.hypot(dx, dz);
    if (distance > 132) { pose.x = (dx / distance) * 132; pose.z = FLOOD_CENTER_Z + (dz / distance) * 132; }
  });
  return null;
}

function LaunchRaft({ position = [0, 0.72, FLOOD_CENTER_Z] }) {
  const raftRef = useRef();

  useFrame(({ clock }) => {
    if (!raftRef.current) return;
    raftRef.current.position.y = 0.72 + Math.sin(clock.elapsedTime * 0.8) * 0.06;
    raftRef.current.rotation.z = Math.sin(clock.elapsedTime * 0.55) * 0.008;
  });

  return (
    <group ref={raftRef} position={position}>
      <mesh position={[0, 0.18, 0]} receiveShadow>
        <boxGeometry args={[8, 0.45, 6]} />
        <meshStandardMaterial color="#735331" roughness={0.78} metalness={0.05} />
      </mesh>
      {[-2.65, 2.65].map((x) => (
        <mesh key={x} position={[x, -0.12, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[0.72, 0.72, 6.4, 20]} />
          <meshStandardMaterial color="#2d353b" roughness={0.42} metalness={0.35} />
        </mesh>
      ))}
      <mesh position={[0, 0.43, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[1.65, 2.15, 40]} />
        <meshBasicMaterial color="#e1d9b8" side={THREE.DoubleSide} />
      </mesh>
    </group>
  );
}

// ── Controllable animated drone ──────────────────────────────────────────────
function DroneMarker({ position = [0, 10, 0], heading = 0 }) {
  const flightRef = useRef();
  const modelRef = useRef();
  const keysRef = useRef(new Set());
  const launchedRef = useRef(false);
  const propellersRef = useRef([]);
  const rotorSpeedRef = useRef(0);
  const rotorsPausedRef = useRef(false);
  const { scene } = useGLTF(DRONE_GLTF);
  const droneScene = useMemo(() => {
    const copy = scene.clone(true);
    propellersRef.current = [];
    copy.traverse((obj) => {
      // The Sketchfab download includes its circular showroom/display plate.
      // It is not part of the aircraft and is replaced by our launch raft.
      if (/piste/i.test(obj.name)) obj.visible = false;
      if (/^PROPELLER\d+_low$/i.test(obj.name)) propellersRef.current.push(obj);
    });
    return copy;
  }, [scene]);

  useEffect(() => {
    const controlledKeys = new Set([
      "KeyW", "KeyA", "KeyS", "KeyD",
      "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "KeyP",
    ]);
    const onKeyDown = (event) => {
      if (!controlledKeys.has(event.code) || /input|textarea|select/i.test(event.target?.tagName)) return;
      if (activePersonControlId !== null || activeObstacleControlId !== null) {
        keysRef.current.clear();
        return;
      }
      event.preventDefault();
      if (event.code === "KeyP") {
        if (!event.repeat) {
          rotorsPausedRef.current = !rotorsPausedRef.current;
          if (rotorsPausedRef.current) rotorSpeedRef.current = 0;
        }
        return;
      }
      if (event.code === "ArrowUp" && !launchedRef.current) {
        launchedRef.current = true;
      }
      keysRef.current.add(event.code);
    };
    const onKeyUp = (event) => {
      if (!controlledKeys.has(event.code)) return;
      event.preventDefault();
      keysRef.current.delete(event.code);
    };
    window.addEventListener("keydown", onKeyDown, { passive: false });
    window.addEventListener("keyup", onKeyUp, { passive: false });
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, []);

  useFrame((_, delta) => {
    const drone = flightRef.current;
    if (!drone) return;

    const keys = keysRef.current;
    if (activePersonControlId !== null || activeObstacleControlId !== null) keys.clear();
    const autonomous = ["scanning", "confirming", "rtl"].includes(surveyMission.status);
    if (autonomous) launchedRef.current = true;
    if (surveyMission.status === "confirming" && performance.now() >= surveyMission.holdUntil) surveyMission.status = "scanning";
    const target = autonomous ? surveyMission.waypoints[surveyMission.waypointIndex] : null;
    if (target && surveyMission.status !== "confirming") {
      const offset = new THREE.Vector3(target.x - drone.position.x, target.y - drone.position.y, target.z - drone.position.z);
      const distance = offset.length();
      if (distance < 0.35) {
        surveyMission.waypointIndex += 1;
        if (surveyMission.waypointIndex >= surveyMission.waypoints.length) {
          surveyMission.status = "complete";
          launchedRef.current = false;
        } else if (surveyMission.waypointIndex >= surveyMission.waypoints.length - 2) {
          surveyMission.status = "rtl";
        }
      } else {
        offset.normalize();
        drone.position.addScaledVector(offset, Math.min(distance, surveyMission.speed * delta));
        drone.rotation.y = Math.atan2(-offset.x, -offset.z);
      }
    }
    const moveSpeed = 12;
    const climbSpeed = 8;
    const yawSpeed = 1.45;
    const canFly = launchedRef.current;
    const forward = canFly && !autonomous ? (keys.has("KeyW") ? 1 : 0) - (keys.has("KeyS") ? 1 : 0) : 0;
    const strafe = canFly && !autonomous ? (keys.has("KeyD") ? 1 : 0) - (keys.has("KeyA") ? 1 : 0) : 0;
    const climb = !autonomous ? (keys.has("ArrowUp") ? 1 : 0) - (keys.has("ArrowDown") ? 1 : 0) : 0;
    const yaw = canFly && !autonomous ? (keys.has("ArrowLeft") ? 1 : 0) - (keys.has("ArrowRight") ? 1 : 0) : 0;

    // The supplied 23-second clip contains pauses, so spin the four propeller
    // assemblies continuously after launch instead of replaying that clip.
    const targetRotorSpeed = canFly && !rotorsPausedRef.current ? 30 : 0;
    rotorSpeedRef.current = THREE.MathUtils.lerp(rotorSpeedRef.current, targetRotorSpeed, 0.08);
    propellersRef.current.forEach((propeller, index) => {
      propeller.rotation.z += rotorSpeedRef.current * delta * (index % 2 === 0 ? 1 : -1);
    });

    drone.rotation.y += yaw * yawSpeed * delta;
    const sin = Math.sin(drone.rotation.y);
    const cos = Math.cos(drone.rotation.y);
    // The Inspire model's physical nose/camera faces local -Z.
    drone.position.x += (strafe * cos - forward * sin) * moveSpeed * delta;
    drone.position.z += (-forward * cos - strafe * sin) * moveSpeed * delta;
    drone.position.y = THREE.MathUtils.clamp(
      drone.position.y + climb * climbSpeed * delta,
      DRONE_PARKED_Y,
      80,
    );

    // Keep manual flight inside the circular flood-map boundary.
    const dx = drone.position.x;
    const dz = drone.position.z - FLOOD_CENTER_Z;
    const distance = Math.hypot(dx, dz);
    if (distance > 132) {
      drone.position.x = (dx / distance) * 132;
      drone.position.z = FLOOD_CENTER_Z + (dz / distance) * 132;
    }

    // Small flight attitude cues make direction changes easy to read.
    if (modelRef.current) {
      modelRef.current.rotation.x = THREE.MathUtils.lerp(modelRef.current.rotation.x, forward * 0.1, 0.08);
      modelRef.current.rotation.z = THREE.MathUtils.lerp(modelRef.current.rotation.z, -strafe * 0.12, 0.08);
    }

    liveDronePose.x = drone.position.x;
    liveDronePose.y = drone.position.y;
    liveDronePose.z = drone.position.z;
    liveDronePose.yaw = drone.rotation.y;
    liveDronePose.launched = launchedRef.current;
    surveyMission.droneAltitude = Math.max(0, drone.position.y - DRONE_PARKED_Y);
    surveyMission.manualActive = launchedRef.current && !["scanning", "confirming", "rtl"].includes(surveyMission.status);
    if (launchedRef.current) markSurveyCoverage(drone.position.x, drone.position.z);
  });

  return (
    <>
      <LaunchRaft position={[position[0], 0.72, position[2]]} />

      <group ref={flightRef} position={position} rotation={[0, heading * (Math.PI / 180), 0]}>
        <group ref={modelRef} scale={DRONE_MODEL_SCALE} rotation={[0, Math.PI, 0]}>
          <primitive object={droneScene} dispose={null} />
        </group>
      </group>
    </>
  );
}

// ── Camera fog setup ──────────────────────────────────────────────────────────
function SceneFog() {
  const { scene } = useThree();
  useEffect(() => {
    scene.fog = new THREE.FogExp2("#0d1b2a", 0.002);
    return () => { scene.fog = null; };
  }, [scene]);
  return null;
}

const CAMERA_PRESETS = {
  free:  { position: [80, 65, 95], target: [0, 0, FLOOD_CENTER_Z], up: [0, 1, 0], fov: 55 },
  top:   { position: [0, 250, FLOOD_CENTER_Z], target: [0, 0, FLOOD_CENTER_Z], up: [0, 0, -1], fov: 65 },
  front: { position: [0, 45, 180], target: [0, 4, FLOOD_CENTER_Z], up: [0, 1, 0], fov: 55 },
  reset: { position: [0, 7, -120], target: [0, 1.5, FLOOD_CENTER_Z], up: [0, 1, 0], fov: 35 },
};

// Camera controls tuned for inspecting and flying over the circular flood map.
function CameraController({ request, enabled = true }) {
  const controlsRef = useRef();
  const { camera } = useThree();

  useEffect(() => {
    const preset = CAMERA_PRESETS[request.name];
    if (!preset || !controlsRef.current) return;
    camera.up.set(...preset.up);
    camera.position.set(...preset.position);
    camera.fov = preset.fov;
    controlsRef.current.target.set(...preset.target);
    camera.lookAt(...preset.target);
    camera.updateProjectionMatrix();
    controlsRef.current.update();
  }, [camera, request]);

  return (
    <MapControls
      ref={controlsRef}
      makeDefault
      enabled={enabled}
      enableDamping
      dampingFactor={0.07}
      enableRotate
      enablePan
      enableZoom
      screenSpacePanning={false}
      panSpeed={1.25}
      zoomSpeed={1.15}
      zoomToCursor
      keyPanSpeed={18}
      minDistance={1.5}
      maxDistance={600}
      minPolarAngle={0.01}
      maxPolarAngle={Math.PI / 2 - 0.01}
    />
  );
}

function SurveyAreaSelector({ enabled, bounds, onChange, onComplete }) {
  const startRef = useRef(null);
  const pointFromEvent = (event) => ({
    x: THREE.MathUtils.clamp(event.point.x, -128, 128),
    z: THREE.MathUtils.clamp(event.point.z, FLOOD_CENTER_Z - 128, FLOOD_CENTER_Z + 128),
  });
  const down = (event) => {
    if (!enabled) return;
    event.stopPropagation();
    startRef.current = pointFromEvent(event);
    onChange({ minX: startRef.current.x, maxX: startRef.current.x, minZ: startRef.current.z, maxZ: startRef.current.z });
  };
  const move = (event) => {
    if (!enabled || !startRef.current) return;
    event.stopPropagation();
    const point = pointFromEvent(event);
    onChange({ minX: startRef.current.x, maxX: point.x, minZ: startRef.current.z, maxZ: point.z });
  };
  const up = (event) => {
    if (!startRef.current) return;
    event.stopPropagation();
    startRef.current = null;
    onComplete();
  };
  const width = bounds ? Math.max(0.5, Math.abs(bounds.maxX - bounds.minX)) : 0;
  const depth = bounds ? Math.max(0.5, Math.abs(bounds.maxZ - bounds.minZ)) : 0;
  const centerX = bounds ? (bounds.minX + bounds.maxX) / 2 : 0;
  const centerZ = bounds ? (bounds.minZ + bounds.maxZ) / 2 : FLOOD_CENTER_Z;
  return (
    <>
      <mesh position={[0, 1.2, FLOOD_CENTER_Z]} rotation={[-Math.PI / 2, 0, 0]}
        onPointerDown={down} onPointerMove={move} onPointerUp={up} visible={enabled}>
        <planeGeometry args={[264, 264]} />
        <meshBasicMaterial transparent opacity={0.015} depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
      {bounds && (
        <mesh position={[centerX, 1.35, centerZ]}>
          <boxGeometry args={[width, 0.12, depth]} />
          <meshBasicMaterial color="#22bfff" transparent opacity={0.18} wireframe />
        </mesh>
      )}
    </>
  );
}

function OnboardCamera() {
  const { camera } = useThree();

  useFrame(() => {
    const pose = liveDronePose;
    // Dedicated search mount: an oblique downward view preserves human body
    // appearance while covering useful ground at 20-60 m altitude. It is used
    // for both autonomous and manual flight so both modes share detection.
    const forwardX = -Math.sin(pose.yaw);
    const forwardZ = -Math.cos(pose.yaw);

    camera.position.set(
      pose.x + forwardX * 0.9,
      pose.y - 0.2,
      pose.z + forwardZ * 0.9,
    );
    camera.up.set(0, 1, 0);
    camera.lookAt(
      pose.x + forwardX * 14,
      pose.y - 24,
      pose.z + forwardZ * 14,
    );
  });

  return null;
}

function FpvDetectionCapture({ onDetections }) {
  const { gl, camera, scene } = useThree();
  const busyRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const detectionWidth = 1280;
    const detectionHeight = 720;
    const renderTarget = new THREE.WebGLRenderTarget(detectionWidth, detectionHeight, {
      depthBuffer: true,
      stencilBuffer: false,
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
    });
    const pixels = new Uint8Array(detectionWidth * detectionHeight * 4);
    const flipped = new Uint8ClampedArray(pixels.length);
    const encodeCanvas = document.createElement("canvas");
    encodeCanvas.width = detectionWidth;
    encodeCanvas.height = detectionHeight;
    const encodeContext = encodeCanvas.getContext("2d", { alpha: false });
    const rowBytes = detectionWidth * 4;

    const capture = () => {
      if (cancelled || busyRef.current) return;
      busyRef.current = true;
      // Render a separate high-resolution sensor frame. This never resizes or
      // stalls the visible FPV canvas; only the detector consumes this image.
      const previousTarget = gl.getRenderTarget();
      gl.setRenderTarget(renderTarget);
      gl.clear();
      gl.render(scene, camera);
      gl.readRenderTargetPixels(renderTarget, 0, 0, detectionWidth, detectionHeight, pixels);
      gl.setRenderTarget(previousTarget);
      for (let row = 0; row < detectionHeight; row += 1) {
        const source = (detectionHeight - 1 - row) * rowBytes;
        flipped.set(pixels.subarray(source, source + rowBytes), row * rowBytes);
      }
      encodeContext.putImageData(new ImageData(flipped, detectionWidth, detectionHeight), 0, 0);
      encodeCanvas.toBlob(async (blob) => {
        if (!blob || cancelled) {
          busyRef.current = false;
          return;
        }
        try {
          const form = new FormData();
          form.append("frame", blob, "fpv.jpg");
          const response = await fetch("/api/detection/frame", { method: "POST", body: form });
          if (!response.ok) throw new Error(`detector returned ${response.status}`);
          const result = await response.json();
          onDetections?.({ boxes: result.people ?? [], frame: result.frame });
          const candidates = (result.people ?? []).filter((box) => box.conf >= 0.50);
          if (candidates.length && result.frame) {
            for (const candidate of candidates) {
            const px = (candidate.x1 + candidate.x2) / 2;
            const py = (candidate.y1 + candidate.y2) / 2;
            const point = new THREE.Vector3((px / result.frame.width) * 2 - 1, 1 - (py / result.frame.height) * 2, 0.5).unproject(camera);
            const ray = new THREE.Raycaster(camera.position, point.sub(camera.position).normalize());
            const hits = ray.intersectObjects(scene.children, true);
            let world = hits[0]?.point;
            const personHit = hits.find((hit) => {
              let object = hit.object;
              while (object) {
                if (/^flood-person-\d+/.test(object.name)) return true;
                object = object.parent;
              }
              return false;
            });
            if (personHit) {
              let object = personHit.object;
              while (object && !/^flood-person-\d+/.test(object.name)) object = object.parent;
              const personId = Number(object?.name.match(/^flood-person-(\d+)/)?.[1]);
              const pose = livePersonPoses.get(personId);
              if (pose) world = new THREE.Vector3(pose.x, pose.y, pose.z);
            }
            if (world) registerSurveyDetection({ x: world.x, y: world.y, z: world.z, confidence: candidate.conf });
            }
          }
        } catch (error) {
          console.debug("FPV detector unavailable", error);
        } finally {
          busyRef.current = false;
        }
      }, "image/jpeg", 0.92);
    };

    // Busy guard prevents overlap; 150 ms targets ~6 FPS detection while the
    // visible WebGL feed continues at its normal animation-frame rate.
    const timer = window.setInterval(capture, 150);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      renderTarget.dispose();
    };
  }, [camera, gl, onDetections, scene]);

  return null;
}

/** Live first-person view from the drone's current position and heading. */
export function DroneCameraFeed() {
  const [detection, setDetection] = useState({ boxes: [], frame: null });
  const updateDetections = useCallback((result) => setDetection(result), []);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", minHeight: 0, background: "#081018" }}>
      <Canvas
        camera={{ position: [0, DRONE_PARKED_Y, FLOOD_CENTER_Z], fov: 72, near: 0.15, far: 800 }}
        dpr={[0.75, 1]}
        gl={{
          antialias: false,
          powerPreference: "high-performance",
          stencil: false,
          preserveDrawingBuffer: true,
        }}
        shadows={false}
      >
        <SceneFog />
        <ambientLight intensity={0.78} color="#b9cbd7" />
        <directionalLight position={[50, 80, 30]} intensity={1.25} color="#ffe8b0" />
        <hemisphereLight skyColor="#438ab5" groundColor="#354331" intensity={0.55} />
        <Suspense fallback={null}>
          <FloodScene />
          <LaunchRaft />
          <FloodPeople />
          <FloodObstacles />
          <Environment preset="sunset" />
        </Suspense>
        <OnboardCamera />
        <FpvDetectionCapture onDetections={updateDetections} />
      </Canvas>

      <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
        <div style={{
          position: "absolute", left: "50%", top: "50%", width: 18, height: 18,
          transform: "translate(-50%, -50%)", border: "1px solid rgba(120,220,255,0.72)", borderRadius: "50%",
        }} />
        <div style={{
          position: "absolute", left: 8, top: 7, color: "#8edcff",
          fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, letterSpacing: "0.08em",
          textShadow: "0 1px 3px #000",
        }}>
          CAM 01 · FPV
        </div>
        <div style={{
          position: "absolute", right: 8, top: 7, display: "flex", alignItems: "center", gap: 5,
          color: "#d6edf8", fontFamily: "'IBM Plex Mono', monospace", fontSize: 8,
          textShadow: "0 1px 3px #000",
        }}>
          <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#e8368f" }} /> LIVE
        </div>
        {[[6, 6], [94, 6], [6, 94], [94, 94]].map(([x, y], index) => (
          <span key={index} style={{
            position: "absolute", left: `${x}%`, top: `${y}%`, width: 12, height: 12,
            borderLeft: x < 50 ? "1px solid rgba(180,225,245,0.55)" : 0,
            borderRight: x > 50 ? "1px solid rgba(180,225,245,0.55)" : 0,
            borderTop: y < 50 ? "1px solid rgba(180,225,245,0.55)" : 0,
            borderBottom: y > 50 ? "1px solid rgba(180,225,245,0.55)" : 0,
            transform: "translate(-50%, -50%)",
          }} />
        ))}
        {detection.frame && detection.boxes.map((box, index) => {
          const left = (box.x1 / detection.frame.width) * 100;
          const top = (box.y1 / detection.frame.height) * 100;
          const width = ((box.x2 - box.x1) / detection.frame.width) * 100;
          const height = ((box.y2 - box.y1) / detection.frame.height) * 100;
          return (
            <div
              key={`${index}-${box.x1}-${box.y1}`}
              style={{
                position: "absolute",
                left: `${left}%`,
                top: `${top}%`,
                width: `${width}%`,
                height: `${height}%`,
                border: "2px solid #ff2f92",
                boxSizing: "border-box",
                boxShadow: "0 0 6px rgba(255,47,146,0.7)",
              }}
            >
              <span style={{
                position: "absolute",
                left: -2,
                bottom: "100%",
                padding: "2px 4px",
                color: "#fff",
                background: "#d91f75",
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: 8,
                whiteSpace: "nowrap",
              }}>
                PERSON {(box.conf * 100).toFixed(0)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const ROUTE_COLORS = ["#ff3b7f", "#31d17c", "#ff70d6", "#ffd43b"];

/** Live 2D safety map derived from the same editable scene poses as the 3D view. */
export function RescueRouteMap() {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setRevision((value) => value + 1), 750);
    return () => window.clearInterval(timer);
  }, []);

  const snapshot = useMemo(() => {
    const obstacles = FLOOD_OBSTACLES.map(({ id, type }) => ({ id, type, ...liveObstaclePoses.get(id) }));
    const targets = surveyMission.detections.map((detection) => ({ ...detection, marker: "confirmed" }));
    return {
      obstacles,
      targets,
      routes: planRescueRoutes({
        start: { x: 0, z: FLOOD_CENTER_Z },
        targets,
        obstacles,
        centerZ: FLOOD_CENTER_Z,
      }),
    };
  }, [revision]);

  const mapPoint = ({ x, z }) => `${x + 132},${z - (FLOOD_CENTER_Z - 132)}`;
  const gridLines = Array.from({ length: 23 }, (_, index) => 12 + index * 11);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", minHeight: 0, background: "#081018", overflow: "hidden" }}>
      <svg viewBox="0 0 264 264" width="100%" height="100%" preserveAspectRatio="xMidYMid meet" aria-label="Rescue route grid map">
        <defs>
          <clipPath id="flood-map-clip"><circle cx="132" cy="132" r="130" /></clipPath>
        </defs>
        <circle cx="132" cy="132" r="130" fill="#14272d" stroke="#437185" strokeWidth="1.5" />
        <g clipPath="url(#flood-map-clip)" opacity="0.26" stroke="#65a6b9" strokeWidth="0.45">
          {gridLines.map((value) => <line key={`v-${value}`} x1={value} y1="0" x2={value} y2="264" />)}
          {gridLines.map((value) => <line key={`h-${value}`} x1="0" y1={value} x2="264" y2={value} />)}
        </g>
        <g clipPath="url(#flood-map-clip)">
          {snapshot.routes.map((route, index) => route.reachable && (
            <polyline key={route.id} points={route.path.map(mapPoint).join(" ")} fill="none"
              stroke={ROUTE_COLORS[index % ROUTE_COLORS.length]} strokeWidth="2" strokeDasharray="4 2"
              strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {snapshot.obstacles.map((obstacle) => (
            <circle key={obstacle.id} cx={obstacle.x + 132} cy={obstacle.z - (FLOOD_CENTER_Z - 132)}
              r={obstacle.type === "tree" ? 3.8 : obstacle.type === "rock2" ? 5.5 : 4.8}
              fill={obstacle.type === "tree" ? "#467a55" : "#6f665b"} stroke="#a5b5ad" strokeWidth="0.65" />
          ))}
          {snapshot.targets.map((target, index) => (
            <g key={target.id} transform={`translate(${target.x + 132} ${target.z - (FLOOD_CENTER_Z - 132)})`}>
              <circle r="4.2" fill={ROUTE_COLORS[index]} stroke="#fff" strokeWidth="1" />
              <text y="-6" textAnchor="middle" fill="#fff" fontSize="7" fontFamily="monospace">V{target.id}</text>
            </g>
          ))}
        </g>
        <g transform="translate(132 132)">
          <rect x="-5" y="-4" width="10" height="8" rx="1" fill="#45b9ed" stroke="#d9f5ff" strokeWidth="1" />
          <circle r="9" fill="none" stroke="#45b9ed" strokeWidth="1" opacity="0.65" />
        </g>
      </svg>
      <div style={{ position: "absolute", left: 8, top: 7, color: "#8edcff", fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, letterSpacing: "0.08em" }}>
        WEIGHTED DIJKSTRA · SAFETY CLEARANCE
      </div>
      <div style={{ position: "absolute", right: 8, bottom: 6, color: "#7895a2", fontFamily: "'IBM Plex Mono', monospace", fontSize: 8 }}>
        GRID 4 m · RAFT = CENTER
      </div>
    </div>
  );
}

// ── Main exported component ───────────────────────────────────────────────────
export default function SceneViewer({
  victims = [],
  telemetry = null,
  className = "",
  surveyArea = null,
  surveyDrawing = false,
  onSurveyAreaChange,
  onSurveyDrawingChange,
}) {
  const [cameraRequest, setCameraRequest] = useState({ name: "reset", id: 0 });
  const [selectedPersonId, setSelectedPersonId] = useState(null);
  const [selectedObstacleId, setSelectedObstacleId] = useState(null);
  useEffect(() => {
    if (surveyDrawing) {
      setCameraRequest((current) => ({ name: "top", id: current.id + 1 }));
    }
  }, [surveyDrawing]);

  const selectPerson = useCallback((id) => {
    activeObstacleControlId = null;
    setSelectedObstacleId(null);
    activePersonControlId = id;
    setSelectedPersonId(id);
  }, []);

  const selectObstacle = useCallback((id) => {
    activePersonControlId = null;
    setSelectedPersonId(null);
    activeObstacleControlId = id;
    setSelectedObstacleId(id);
  }, []);

  const commitObstaclePosition = useCallback(() => {
    const saved = Object.fromEntries([...liveObstaclePoses.entries()].map(([id, pose]) => [id, { ...pose }]));
    window.localStorage.setItem(OBSTACLE_POSE_STORAGE_KEY, JSON.stringify(saved));
    activeObstacleControlId = null;
    setSelectedObstacleId(null);
  }, []);

  const commitPersonPosition = useCallback(() => {
    const saved = Object.fromEntries(
      [...livePersonPoses.entries()].map(([id, pose]) => [id, { ...pose }]),
    );
    window.localStorage.setItem(PERSON_POSE_STORAGE_KEY, JSON.stringify(saved));
    activePersonControlId = null;
    setSelectedPersonId(null);
  }, []);

  const selectCamera = (name) => {
    setCameraRequest((current) => ({ name, id: current.id + 1 }));
  };

  const personPositions = useMemo(() => {
    return victims.map((v, i) => ({
      id: v.id ?? `v${i + 1}`,
      gender: i % 2 === 0 ? "male" : "female",
      position: [
        ((i % 5) - 2) * 8 + (Math.sin(i * 1.3) * 2),
        0.5,
        FLOOD_CENTER_Z + (Math.floor(i / 5) - 1) * 8 + (Math.cos(i * 1.7) * 2),
      ],
      status: v.status ?? "provisional",
    }));
  }, [victims]);

  const dronePos = telemetry
    ? [
        (telemetry.lon - 81.6296) * 111320 * Math.cos((21.2514 * Math.PI) / 180),
        telemetry.alt * 0.3,
        FLOOD_CENTER_Z - (telemetry.lat - 21.2514) * 111320,
      ]
    : [0, DRONE_PARKED_Y, FLOOD_CENTER_Z];

  return (
    <div className={`scene-viewer ${className}`} style={{ width: "100%", height: "100%", position: "relative" }}>
      <Canvas
        camera={{ position: [0, 7, -120], fov: 35, near: 0.5, far: 2000 }}
        gl={{
          antialias: true,
          powerPreference: "high-performance",
          alpha: false,
          stencil: false,
          depth: true,
        }}
        performance={{ min: 0.5 }}
        shadows={false}
      >
        <SceneFog />
        <ambientLight intensity={0.7} color="#b0c8e0" />
        <directionalLight position={[50, 80, 30]} intensity={1.4} color="#ffe8b0" />
        <hemisphereLight skyColor="#3a8fc0" groundColor="#1a4060" intensity={0.5} />
        <Environment preset="sunset" />

        <Suspense fallback={<Loader />}>
          <FloodScene />
          <FloodPeople
            selectable
            selectedId={selectedPersonId}
            onSelect={selectPerson}
          />
          <FloodObstacles selectable onSelect={selectObstacle} />
          <SurveyAreaSelector
            enabled={surveyDrawing}
            bounds={surveyArea}
            onChange={onSurveyAreaChange}
            onComplete={() => onSurveyDrawingChange?.(false)}
          />
          {personPositions.map((p) => (
            <PersonMarker
              key={p.id}
              position={p.position}
              gender={p.gender}
              label={p.id.toUpperCase()}
              status={p.status}
            />
          ))}
        </Suspense>

        <PersonControlSystem
          selectedId={selectedPersonId}
          onCommit={commitPersonPosition}
        />
        <ObstacleControlSystem selectedId={selectedObstacleId} onCommit={commitObstaclePosition} />
        <DroneMarker position={dronePos} heading={telemetry?.heading ?? 0} />

        <CameraController request={cameraRequest} enabled={!surveyDrawing} />
      </Canvas>

      <div style={{
        position: "absolute",
        left: 12,
        bottom: 12,
        zIndex: 5,
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: 6,
        border: "1px solid rgba(97,113,126,0.55)",
        borderRadius: 3,
        background: "rgba(12,18,23,0.88)",
        fontFamily: "'IBM Plex Mono', monospace",
      }}>
        {["free", "top", "front", "reset"].map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => selectCamera(name)}
            style={{
              border: cameraRequest.name === name ? "1px solid #4a9fd8" : "1px solid #34414b",
              borderRadius: 2,
              padding: "5px 8px",
              color: cameraRequest.name === name ? "#dcecf7" : "#8fa0ad",
              background: cameraRequest.name === name ? "#235f86" : "#182129",
              font: "inherit",
              fontSize: 10,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              cursor: "pointer",
            }}
          >
            {name}
          </button>
        ))}
        <span style={{ marginLeft: 4, color: "#71818d", fontSize: 9, whiteSpace: "nowrap" }}>
          DRAG PAN · RIGHT-DRAG ROTATE · WHEEL ZOOM TO CURSOR
        </span>
      </div>
      <div style={{
        position: "absolute",
        right: 12,
        bottom: 12,
        zIndex: 5,
        padding: "7px 9px",
        border: "1px solid rgba(74,159,216,0.5)",
        borderRadius: 3,
        color: "#9ec8e4",
        background: "rgba(12,18,23,0.88)",
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 9,
        letterSpacing: "0.05em",
        whiteSpace: "nowrap",
      }}>
        {selectedPersonId != null
          ? `PERSON ${selectedPersonId} SELECTED · W/S FORWARD · A/D STRAFE · ↑/↓ HEIGHT · ←/→ ROTATE · ENTER SAVE`
          : selectedObstacleId != null
            ? `OBSTACLE ${selectedObstacleId.toUpperCase()} SELECTED · W/S FORWARD · A/D STRAFE · ↑/↓ HEIGHT · ←/→ ROTATE · ENTER SAVE`
            : "DRONE · W/S FORWARD · A/D STRAFE · ↑/↓ ALTITUDE · ←/→ ROTATE · P PROPELLERS"}
      </div>
    </div>
  );
}

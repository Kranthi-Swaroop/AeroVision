"""Geodesy and camera projection.

This module is the technical core of AeroVision's georeferencing claim.
Everything here is exact for a flat-earth / flat-ground assumption, which is
valid over the few-hundred-metre scan areas we operate in.

Frames
------
World (ENU): x = east (m), y = north (m), z = up (m), origin at scene anchor.
Camera:      x = image right, y = image down, z = optical axis (forward).
Image:       (u, v) pixels, origin top-left.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3


# --------------------------------------------------------------------------
# Local tangent plane (ENU) <-> WGS84
# --------------------------------------------------------------------------
class LocalENU:
    """Equirectangular local tangent plane.

    Error is well under 1 cm across a 1 km span, which is two orders of
    magnitude below our georeferencing error budget, so the extra cost of a
    full geodetic projection buys us nothing here.
    """

    def __init__(self, lat0: float, lon0: float):
        self.lat0 = lat0
        self.lon0 = lon0
        phi = math.radians(lat0)
        sin_phi = math.sin(phi)
        denom = 1.0 - WGS84_E2 * sin_phi * sin_phi
        # metres per radian of latitude / longitude at the anchor
        self.m_per_rad_lat = WGS84_A * (1 - WGS84_E2) / (denom ** 1.5)
        self.m_per_rad_lon = WGS84_A * math.cos(phi) / math.sqrt(denom)

    def to_enu(self, lat: float, lon: float) -> tuple[float, float]:
        e = math.radians(lon - self.lon0) * self.m_per_rad_lon
        n = math.radians(lat - self.lat0) * self.m_per_rad_lat
        return e, n

    def to_ll(self, e: float, n: float) -> tuple[float, float]:
        lat = self.lat0 + math.degrees(n / self.m_per_rad_lat)
        lon = self.lon0 + math.degrees(e / self.m_per_rad_lon)
        return lat, lon


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two nearby points.

    Uses the same WGS84 ellipsoid scale factors as LocalENU rather than a
    spherical haversine. That matters: a spherical earth radius disagrees with
    the ellipsoid by about 0.16% at this latitude, and since this function
    computes the georeferencing error the whole demo is judged on, it must not
    carry a systematic scale bias relative to the projection that produced the
    coordinates.
    """
    phi = math.radians((lat1 + lat2) / 2.0)
    sin_phi = math.sin(phi)
    denom = 1.0 - WGS84_E2 * sin_phi * sin_phi
    m_lat = WGS84_A * (1 - WGS84_E2) / (denom ** 1.5)
    m_lon = WGS84_A * math.cos(phi) / math.sqrt(denom)
    dn = math.radians(lat2 - lat1) * m_lat
    de = math.radians(lon2 - lon1) * m_lon
    return math.hypot(de, dn)


# kept so callers can read either name; they are the same function
haversine_m = distance_m


# --------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------
@dataclass
class Intrinsics:
    width: int
    height: int
    hfov_deg: float

    @property
    def fx(self) -> float:
        return (self.width / 2.0) / math.tan(math.radians(self.hfov_deg) / 2.0)

    @property
    def cx(self) -> float:
        return self.width / 2.0

    @property
    def cy(self) -> float:
        return self.height / 2.0

    @property
    def vfov_deg(self) -> float:
        return 2.0 * math.degrees(math.atan((self.height / 2.0) / self.fx))

    @property
    def K(self) -> np.ndarray:
        f = self.fx
        return np.array([[f, 0.0, self.cx], [0.0, f, self.cy], [0.0, 0.0, 1.0]])


@dataclass
class Pose:
    """Camera pose in the world frame."""

    east: float
    north: float
    alt: float           # metres above ground level
    heading_deg: float   # 0 = north, clockwise positive
    tilt_deg: float      # 0 = straight down (nadir), 90 = horizon


def rotation_world_from_camera(pose: Pose) -> np.ndarray:
    """R such that d_world = R @ d_camera.

    At tilt=0, heading=0 the camera stares straight down with image-up
    pointing north. Tilt pitches the optical axis forward (toward the
    heading direction); heading then yaws the whole rig about world z.
    """
    t = math.radians(pose.tilt_deg)
    # camera basis expressed in a north-facing world frame
    x_c = np.array([1.0, 0.0, 0.0])
    y_c = np.array([0.0, -math.cos(t), -math.sin(t)])
    z_c = np.array([0.0, math.sin(t), -math.cos(t)])
    r_base = np.column_stack([x_c, y_c, z_c])

    psi = math.radians(pose.heading_deg)
    c, s = math.cos(psi), math.sin(psi)
    # clockwise-from-north yaw about world up
    r_yaw = np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])
    return r_yaw @ r_base


def ground_homography(pose: Pose, intr: Intrinsics) -> np.ndarray:
    """3x3 H mapping world ground points [E, N, 1] -> image [u, v, 1].

    This is the homography referenced on the technical-approach slide. It is
    exact for points on the z = 0 plane, which is the flat-ground assumption.
    """
    r = rotation_world_from_camera(pose)
    a = r.T
    c = np.array([pose.east, pose.north, pose.alt])
    m = np.column_stack([a[:, 0], a[:, 1], -a @ c])
    return intr.K @ m


def image_to_ground(u: float, v: float, pose: Pose, intr: Intrinsics):
    """Back-project a pixel onto the ground plane. Returns (east, north) or None.

    None means the ray points at or above the horizon, i.e. that pixel sees
    sky rather than terrain.
    """
    d_c = np.array([(u - intr.cx) / intr.fx, (v - intr.cy) / intr.fx, 1.0])
    d_w = rotation_world_from_camera(pose) @ d_c
    if d_w[2] > -1e-6:
        return None
    t = pose.alt / -d_w[2]
    return pose.east + t * d_w[0], pose.north + t * d_w[1]


def ground_to_image(east: float, north: float, pose: Pose, intr: Intrinsics):
    """Project a ground point into the image. Returns (u, v) or None if behind."""
    r = rotation_world_from_camera(pose)
    p_c = r.T @ (np.array([east, north, 0.0]) - np.array([pose.east, pose.north, pose.alt]))
    if p_c[2] <= 1e-6:
        return None
    return intr.fx * p_c[0] / p_c[2] + intr.cx, intr.fx * p_c[1] / p_c[2] + intr.cy


def footprint(pose: Pose, intr: Intrinsics, max_range: float = 400.0):
    """Ground quadrilateral currently visible, as [(e, n), ...].

    Rays that miss the ground (above horizon) are clamped to max_range so the
    dashboard can still draw a sane coverage polygon at high tilt.
    """
    corners = [(0, 0), (intr.width, 0), (intr.width, intr.height), (0, intr.height)]
    out = []
    for u, v in corners:
        g = image_to_ground(u, v, pose, intr)
        if g is None:
            d_c = np.array([(u - intr.cx) / intr.fx, (v - intr.cy) / intr.fx, 1.0])
            d_w = rotation_world_from_camera(pose) @ d_c
            horiz = np.array([d_w[0], d_w[1]])
            nrm = np.linalg.norm(horiz) or 1.0
            horiz = horiz / nrm * max_range
            g = (pose.east + horiz[0], pose.north + horiz[1])
        out.append(g)
    return out


def along_track_length(pose: Pose, intr: Intrinsics) -> float:
    """Ground distance from the near edge of the frame to the far edge.

    Sets the camera trigger interval. Forward overlap is what guarantees each
    ground point appears in several frames, and several sightings is what lets
    the fusion stage tell a real person from a one-frame false positive.
    Computed from the actual footprint so tilt is accounted for: an oblique
    camera sees a much longer strip than a nadir one at the same altitude.
    """
    fp = footprint(pose, intr)
    near = ((fp[2][0] + fp[3][0]) / 2.0, (fp[2][1] + fp[3][1]) / 2.0)
    far = ((fp[0][0] + fp[1][0]) / 2.0, (fp[0][1] + fp[1][1]) / 2.0)
    return math.dist(near, far)


def swath_width(alt: float, intr: Intrinsics) -> float:
    """Nadir-equivalent across-track ground width.

    Used only to space the lawnmower legs. An oblique camera actually images a
    trapezoid that is wider than this at the far edge, so treating it as the
    nadir width is the conservative choice: real sidelap exceeds planned.
    """
    return 2.0 * alt * math.tan(math.radians(intr.hfov_deg) / 2.0)

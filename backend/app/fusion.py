"""Detection fusion: turn many overlapping detections into distinct victims.

With 25% sidelap the same person is imaged in five to ten consecutive frames.
Pinning every detection would put ten markers on one casualty and make the
victim count meaningless. Clustering by ground distance fixes that, and the
sighting count it produces doubles as a confidence signal: a real person is
seen repeatedly from different angles, a one-frame false positive is not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .geo import haversine_m


@dataclass
class VictimTrack:
    vid: str
    lat: float
    lon: float
    sightings: int = 1
    conf_sum: float = 0.0
    best_conf: float = 0.0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    thumb: str | None = None
    _w: float = 0.0

    @property
    def mean_conf(self) -> float:
        return self.conf_sum / max(1, self.sightings)

    def as_dict(self, confirm_at: int):
        return {
            "id": self.vid,
            "lat": round(self.lat, 7),
            "lon": round(self.lon, 7),
            "sightings": self.sightings,
            "confidence": round(self.best_conf, 3),
            "mean_confidence": round(self.mean_conf, 3),
            "status": "confirmed" if self.sightings >= confirm_at else "provisional",
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "thumb": self.thumb,
        }


class VictimFuser:
    """Online single-link clustering over geodesic distance."""

    def __init__(self, merge_radius_m: float = 6.0, confirm_at: int = 3):
        self.radius = merge_radius_m
        self.confirm_at = confirm_at
        self.tracks: dict[str, VictimTrack] = {}
        self._n = 0

    def add(self, lat: float, lon: float, conf: float, thumb: str | None = None):
        """Fold one detection in. Returns (track, is_new)."""
        best, best_d = None, self.radius
        for t in self.tracks.values():
            d = haversine_m(lat, lon, t.lat, t.lon)
            if d < best_d:
                best, best_d = t, d

        if best is None:
            self._n += 1
            vid = f"v{self._n}"
            t = VictimTrack(vid=vid, lat=lat, lon=lon, conf_sum=conf,
                            best_conf=conf, thumb=thumb)
            t._w = conf
            self.tracks[vid] = t
            return t, True

        # confidence-weighted running mean: sharper detections pull harder
        w = best._w + conf
        best.lat = (best.lat * best._w + lat * conf) / w
        best.lon = (best.lon * best._w + lon * conf) / w
        best._w = w
        best.sightings += 1
        best.conf_sum += conf
        best.best_conf = max(best.best_conf, conf)
        best.last_seen = time.time()
        if thumb and conf >= best.best_conf:
            best.thumb = thumb
        return best, False

    def confirmed(self):
        return [t for t in self.tracks.values() if t.sightings >= self.confirm_at]

    def snapshot(self):
        return [t.as_dict(self.confirm_at) for t in
                sorted(self.tracks.values(), key=lambda t: t.vid)]

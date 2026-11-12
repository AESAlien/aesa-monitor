"""방위·고각·거리와 ENU 좌표 사이의 변환.

방위는 북쪽 0도에서 시계방향이다. 모의기(TargetSIM)와 처리부가 쓰는 ENU 기준과 같다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

Vector = Tuple[float, float, float]


def wrap_degrees(value: float) -> float:
    """각도 차이를 [-180, 180) 범위로 바꾼다."""
    return (value + 180.0) % 360.0 - 180.0


def direction_vector(azimuth_deg: float, elevation_deg: float) -> Vector:
    azimuth = math.radians(azimuth_deg)
    elevation = math.radians(elevation_deg)
    return (
        math.cos(elevation) * math.sin(azimuth),
        math.cos(elevation) * math.cos(azimuth),
        math.sin(elevation),
    )


def position_m(range_km: float, azimuth_deg: float, elevation_deg: float) -> Vector:
    east, north, up = direction_vector(azimuth_deg, elevation_deg)
    range_m = range_km * 1000.0
    return east * range_m, north * range_m, up * range_m


def distance_m(first: Vector, second: Vector) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))


def dot(first: Vector, second: Vector) -> float:
    return sum(a * b for a, b in zip(first, second))


@dataclass(frozen=True)
class BeamFrame:
    """예측 방향(빔 중심)을 기준으로 한 단면 좌표계.

    cross_m은 방위가 커지는 쪽, up_m은 고각이 커지는 쪽의 수직 거리이고
    along_m은 빔 축 방향 거리다.
    """

    azimuth_deg: float
    elevation_deg: float

    def offset(self, point: Vector) -> Tuple[float, float, float]:
        azimuth = math.radians(self.azimuth_deg)
        elevation = math.radians(self.elevation_deg)
        axis = direction_vector(self.azimuth_deg, self.elevation_deg)
        cross = (math.cos(azimuth), -math.sin(azimuth), 0.0)
        up = (
            -math.sin(elevation) * math.sin(azimuth),
            -math.sin(elevation) * math.cos(azimuth),
            math.cos(elevation),
        )
        return dot(point, cross), dot(point, up), dot(point, axis)

    def normalized_offset(
        self,
        azimuth_deg: float,
        elevation_deg: float,
        azimuth_width_deg: float,
        elevation_width_deg: float,
    ) -> Tuple[float, float]:
        """모의기의 빔 안 판정과 같은 정규화 오프셋. 두 값의 제곱합이 1 이하이면 빔 안이다.

        모의기는 cos(고각) 보정 없이 방위 차이를 그대로 비교한다.
        """
        if azimuth_width_deg <= 0 or elevation_width_deg <= 0:
            return math.nan, math.nan
        return (
            2.0 * wrap_degrees(azimuth_deg - self.azimuth_deg) / azimuth_width_deg,
            2.0 * (elevation_deg - self.elevation_deg) / elevation_width_deg,
        )

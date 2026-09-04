"""
backend/domain/spatial_voxel.py

Base Map点群を、そのLocal Space自身のfinest(3cm)Local Spatial IDへ集約した
結果を表すデータ構造(ロードマップStep 1)。

【SpatialVoxelLabel(構造ラベル)との違い】
このSpatialVoxelは「そのvoxelに何点あったか・どこにあるか」という、
Base Map点群そのものの集約結果であり、構造(WALL/FLOOR/CEILING等)の意味は
一切持たない。構造ラベル(backend/domain/structural_label.py の
SpatialVoxelLabel)とは責務も保存形式も別であり、このモジュールは
structural_label.py を一切importしない・変更しない。

両者は同じキー(space_id, local_spatial_id)を持つため、後から必要になれば
呼び出し側でこのキーによりjoinできる(このモジュール自体はjoin処理を
持たない。将来Step 3以降で必要になった時点で、利用側に実装する)。

【voxel_center と point_centroid の区別(ユーザー指示: 2026-08-29)】
最初の実装では、point群の重心だけを"center"として持っていたが、これは
Spatial ID voxelの幾何学的中心ではなく、Viewerでcube instanceを配置する
用途には使えない(点分布・点数に依存してしまう)ため、責務を分離した。

- voxel_center: Local Spatial ID(f/x/y)と、そのLocal Space自身の
  CoordinateDefinition(origin/rad/unit-size)だけから決まる、理論上の
  グリッドセル中心(ワールド座標)。点密度・点配置に依存しない。
  Viewerのinstance position(cubeの配置座標)にはこちらを使うこと。
- point_centroid(任意): そのvoxelに実際に含まれるBase Map点の重心
  (ワールド座標)。点分布に応じて変化する、参考値としての実測重心。

両者はvoxel対角線未満の誤差で近い値になるが、同一の値になる保証はない。

【重要】local_spatial_idは、Local Spaceごとに独立した意味を持つ文字列
(同じ"z/f/x/y"でも別Local Spaceでは別voxel)。識別は必ず
(space_id, local_spatial_id)の組で行うこと。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SpatialVoxel:
    """Base Map点群の、finest Local Spatial IDへの集約結果。"""

    space_id: str
    local_spatial_id: str
    zoom_level: int
    voxel_size: float
    point_count: int
    voxel_center: list  # [x, y, z] ワールド座標。Spatial ID grid cellの幾何学的中心(点分布非依存)
    point_centroid: Optional[list] = None  # [x, y, z] ワールド座標。このvoxelに属する点の実測重心(任意)
    mean_color: Optional[list] = None  # [r, g, b] (0-1)、Base Mapに色情報がある場合のみ

    def __post_init__(self):
        if not self.space_id or not self.local_spatial_id:
            raise ValueError("SpatialVoxelにはspace_id・local_spatial_idが必須です。")
        if self.point_count <= 0:
            raise ValueError(f"point_countは1以上である必要があります: {self.point_count}")
        if len(self.voxel_center) != 3:
            raise ValueError(f"voxel_centerは3要素([x,y,z])である必要があります: {self.voxel_center!r}")
        if self.point_centroid is not None and len(self.point_centroid) != 3:
            raise ValueError(f"point_centroidは3要素([x,y,z])である必要があります: {self.point_centroid!r}")
        if self.voxel_size <= 0:
            raise ValueError(f"voxel_sizeは正の値である必要があります: {self.voxel_size}")
        if self.mean_color is not None and len(self.mean_color) != 3:
            raise ValueError(f"mean_colorは3要素([r,g,b])である必要があります: {self.mean_color!r}")

    @staticmethod
    def from_dict(data: dict) -> "SpatialVoxel":
        return SpatialVoxel(
            space_id=data["space_id"],
            local_spatial_id=data["local_spatial_id"],
            zoom_level=data["zoom_level"],
            voxel_size=data["voxel_size"],
            point_count=data["point_count"],
            voxel_center=list(data["voxel_center"]),
            point_centroid=list(data["point_centroid"]) if data.get("point_centroid") is not None else None,
            mean_color=list(data["mean_color"]) if data.get("mean_color") is not None else None,
        )

    def to_dict(self) -> dict:
        return {
            "space_id": self.space_id,
            "local_spatial_id": self.local_spatial_id,
            "zoom_level": self.zoom_level,
            "voxel_size": self.voxel_size,
            "point_count": self.point_count,
            "voxel_center": self.voxel_center,
            "point_centroid": self.point_centroid,
            "mean_color": self.mean_color,
        }


@dataclass
class AggregatedSpatialVoxel:
    """finestより粗いzoom levelへの、Visualization用derived aggregation結果
    (ロードマップStep 3)。

    【SpatialVoxelとの違い】SpatialVoxelはBase Map点群の直接の集約結果
    (point_count・point_centroidという「実点」に基づく属性を持つ)。
    AggregatedSpatialVoxelは、そのfinest SpatialVoxel群をさらに上位
    zoom levelへ束ねたものであり、「実点」ではなく「いくつのfinest voxelが
    この親に属したか」(source_voxel_count)を持つ。source of truthは
    あくまでfinestのSpatialVoxel(のcache)であり、AggregatedSpatialVoxel
    はいつでも再導出できるderived data(保存してよいが、それ自体を
    source of truthにしないこと)。

    voxel_centerは、親Local Spatial IDが定義する理論上のgrid cell中心
    (backend/spatial_voxel_aggregation.py参照)であり、子finest voxelの
    centroid平均ではない(SpatialVoxel.point_centroidに相当する概念は
    ここには無い)。
    """

    space_id: str
    local_spatial_id: str  # 集約先(親)のLocal Spatial ID
    zoom_level: int
    voxel_size: float
    voxel_center: list  # [x, y, z] ワールド座標。親IDが定義する理論上のgrid cell中心
    source_voxel_count: int  # この親に集約されたfinest voxelの個数(点数ではない)

    def __post_init__(self):
        if not self.space_id or not self.local_spatial_id:
            raise ValueError("AggregatedSpatialVoxelにはspace_id・local_spatial_idが必須です。")
        if self.source_voxel_count <= 0:
            raise ValueError(f"source_voxel_countは1以上である必要があります: {self.source_voxel_count}")
        if len(self.voxel_center) != 3:
            raise ValueError(f"voxel_centerは3要素([x,y,z])である必要があります: {self.voxel_center!r}")
        if self.voxel_size <= 0:
            raise ValueError(f"voxel_sizeは正の値である必要があります: {self.voxel_size}")

    @staticmethod
    def from_dict(data: dict) -> "AggregatedSpatialVoxel":
        return AggregatedSpatialVoxel(
            space_id=data["space_id"],
            local_spatial_id=data["local_spatial_id"],
            zoom_level=data["zoom_level"],
            voxel_size=data["voxel_size"],
            voxel_center=list(data["voxel_center"]),
            source_voxel_count=data["source_voxel_count"],
        )

    def to_dict(self) -> dict:
        return {
            "space_id": self.space_id,
            "local_spatial_id": self.local_spatial_id,
            "zoom_level": self.zoom_level,
            "voxel_size": self.voxel_size,
            "voxel_center": self.voxel_center,
            "source_voxel_count": self.source_voxel_count,
        }

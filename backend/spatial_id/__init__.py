"""
backend/spatial_id/

標準空間ID(Global Spatial ID)・ローカル空間IDに関する、汎用的な変換・解析
ロジックを置く場所(V2指示書§14のディレクトリ構成に対応)。

- global_spatial_id.py: GlobalSpatialIdResolver(parse/get_center/get_bounds
  実装済み、ロードマップPhase 3.5)。get_center/get_boundsはGeographicPoint
  (経度・緯度・標高、度単位)を返す。
- geographic_projection.py: GeographicPoint→ProjectedPoint(投影座標、メートル
  単位)への変換(ロードマップPhase 3.5b、pyproj使用)。既定CRSはEPSG:6677
  (JGD2011 / Japan Plane Rectangular CS IX)。
- local_spatial_id.py: LocalSpatialIdResolver(ロードマップPhase 3.1)。
  (space_id, local_spatial_id) から2種類の座標系を明示的に分離して解決する
  (2026-09-02): resolve_local_center()(座標系1、intrinsic local、
  Nodal correspondence専用)と resolve_provisional_world_center()
  (座標系2、Viewer専用)。詳細はlocal_spatial_id.pyのモジュールdocstring参照。
"""
from .geographic_projection import DEFAULT_TARGET_EPSG, ProjectedPoint, to_projected
from .global_spatial_id import (
    GeographicPoint,
    GlobalSpatialIdResolver,
    ParsedGlobalId,
    StandardSpatialIdResolver,
)
from .local_spatial_id import (
    LocalSpatialIdResolver,
    resolve_local_center,
    resolve_provisional_world_center,
)

__all__ = [
    "GeographicPoint",
    "GlobalSpatialIdResolver",
    "ParsedGlobalId",
    "StandardSpatialIdResolver",
    "DEFAULT_TARGET_EPSG",
    "ProjectedPoint",
    "to_projected",
    "LocalSpatialIdResolver",
    "resolve_local_center",
    "resolve_provisional_world_center",
]

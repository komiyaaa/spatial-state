"""
backend/spatial_id/local_spatial_id.py

Local Spatial ID("z/f/x/y")を、そのLocal Space自身のCoordinateDefinition
を使って物理座標へ解決する(ロードマップPhase 3.1)。

【2つの座標系を明確に分離する(ユーザー指示: 2026-09-02、座標系の調査結果を
踏まえた修正)】
このモジュールは、意味の異なる2種類の座標系を、別々の名前の関数/メソッドで
提供する。**この2つを混同しないこと。**

1. intrinsic local physical coordinate
   (`resolve_local_center()` / `LocalSpatialIdResolver.resolve_local_center()`)

   = ((x+0.5)*s, (y+0.5)*s, (f+0.5)*s)

   そのLocal Space自身のunit-sizeだけを使う。origin・rotationは一切使わない
   (使ってはいけない)。

   **Nodal correspondence / RigidTransform2D推定(Phase 3.2以降)は、
   必ずこちらを使う。** origin/radは「Local Space内部を整理するための
   暫定値であり、Globalに対する真の方位角ではない」(domain/local_space.py
   のCoordinateDefinition docstring参照)。これをtransform推定の入力に
   混ぜると、各Local Space固有の(Globalとは無関係な)恣意的な回転・並進を、
   本来推定したい相対transformに二重反映してしまう危険がある
   (2026-09-02の座標系調査で発見。詳細は下記「経緯」参照)。

2. provisional/world coordinate
   (`resolve_provisional_world_center()` /
   `LocalSpatialIdResolver.resolve_provisional_world_center()`)

   = 1にorigin/rotationを適用し、Base Map点群と同じ座標系
   (そのLocal Space自身のprovisional world座標)へ戻したもの。

   **Viewer(Three.jsでのvoxel表示、ロードマップStep 1〜4)は、従来通り
   こちらを使う。** Base Map点群と同じシーンに描画するため、点群と同じ
   座標系が必要(voxel_centerの本来の用途)。

`LocalSpatialIdResolver.resolve_center()`は、Viewer関連の既存呼び出しとの
後方互換のため、2の別名(alias)として残している。**新規のPhase 3コードから
使用禁止**(名前が曖昧で、1と2のどちらを返すか読み手に伝わらないため。
この曖昧さそのものが2026-09-02の調査で問題として指摘された)。

【経緯: なぜ2種類に分離したか】
Phase 3.1着手時点では、Step 1(ロードマップ、Three.js Viewer用)で先行実装
されていた逆変換関数(`_voxel_center_from_local_spatial_id`、常に2を返す)を
「同じreverse coordinate logicを二重実装しない」という方針でそのまま
`resolve_center()`の実体として再利用した。しかしPhase 3(Nodal
correspondence)が実際に必要とするのは1であり、2ではない
(Phase 3設計メモ§7・§19.4参照、「各Local Spaceの自分自身のローカル座標系上の
点」同士を比較する設計)。`resolve_center()`という名前のまま2を返し続けると、
Phase 3.2以降の実装者が「まだorigin/rotation適用前の値だ」と誤解し、
correspondence点の比較時に二重にorigin/radを適用してしまう危険があったため、
名前を分離した。

【既存reverse coordinate logicとの統合】
`resolve_provisional_world_center()`は内部で`resolve_local_center()`を
呼び出し、その結果にorigin/rotationを適用するだけ(2 = f(1)、二重実装しない)。
voxel中心を求める数式そのものは、backend/point_cloud_voxelization.pyの
`_voxel_center_from_local_spatial_id()`(ロードマップStep 1で先行実装されて
いたもの)と全く同じであり、`resolve_provisional_world_center()`がその
唯一の実装(point_cloud_voxelization.py・spatial_voxel_aggregation.pyの
両方がここから呼び出す。挙動・数式は一切変更していない)。

数学的根拠(既存ドキュメントを踏襲): point_to_spatial_id.world_points_to_spatial_ids()
が使う回転式(local_x = rel_x*cos+rel_y*sin, local_y = rel_x*sin-rel_y*cos)は、
2x2行列 M=[[cos,sin],[sin,-cos]] による変換であり、Mは対合行列
(M @ M = 単位行列、det(M)=-1の鏡映行列の性質)である。したがって、同じ式を
local_x/local_yに再適用するとrel_x/rel_yに戻る
(rel_x = local_x*cos+local_y*sin, rel_y = local_x*sin-local_y*cos)。
forward変換の回転式そのものは変更していない(同じ式を逆向きに使うだけ)。

【既存設計の維持(ユーザー指示: 2026-09-02)】
- semantic identityは必ず(space_id, local_spatial_id)の組であり、
  local_spatial_id文字列単体はglobal unique keyとして扱わない
  (Local↔Localで同一ID文字列を比較しない。各space_idは常に自分自身の
  CoordinateDefinition/unit-sizeでのみ解決する。座標系1・2のどちらでも
  このルールは共通)。
- 異なるzoom level同士のcorrespondenceも許可する(resolve_local_centerは
  local_spatial_id自身のzを使うだけで、他のzoomとの関係を強制しない)。
- RigidTransform2D(domain/transform.py)ではscale/reflectionを推定しない
  (このモジュールは座標解決のみを担当し、transform推定自体はPhase 3.2以降。
  ここではscale/reflectionに関わる操作を一切行わない)。
"""
from __future__ import annotations

import math
from typing import List

from domain.transform import Point3
from local_spatial_id_hierarchy import parse_local_spatial_id
from repositories.local_space_repository import LocalSpaceRepository


def _validate_unit_size(coordinate_definition: dict) -> None:
    if "unit-size" not in coordinate_definition:
        raise ValueError(
            f"coordinate_definitionが不正です('unit-size'が必要): {sorted(coordinate_definition.keys())}"
        )
    if not coordinate_definition["unit-size"]:
        raise ValueError("coordinate_definition['unit-size']が空です(zoom levelを1つも決定できません)。")


def _validate_origin_and_rad(coordinate_definition: dict) -> None:
    for key in ("origin", "rad"):
        if key not in coordinate_definition:
            raise ValueError(
                f"coordinate_definitionが不正です('{key}'が必要): {sorted(coordinate_definition.keys())}"
            )


def resolve_local_center(local_spatial_id: str, coordinate_definition: dict) -> List[float]:
    """座標系1: intrinsic local physical coordinateを返す。

    ((x+0.5)*s, (y+0.5)*s, (f+0.5)*s)。そのLocal Space自身のunit-sizeだけを
    使う。coordinate_definitionにorigin/radが含まれていても一切参照しない
    (無くても呼び出せる)。

    Nodal correspondence / RigidTransform2D推定(Phase 3.2以降)は、必ず
    この関数(またはLocalSpatialIdResolver.resolve_local_center)を使うこと。
    """
    _validate_unit_size(coordinate_definition)
    zoom_level, f_idx, x_idx, y_idx = parse_local_spatial_id(local_spatial_id)

    unit_size = coordinate_definition["unit-size"]
    if str(zoom_level) not in unit_size:
        raise ValueError(
            f"zoom_level {zoom_level}(local_spatial_id '{local_spatial_id}' 由来)は、"
            f"このCoordinateDefinitionのunit-sizeに存在しません"
            f"(有効なzoom_level: {sorted(int(k) for k in unit_size)})。"
        )
    voxel_size = unit_size[str(zoom_level)]

    return [(x_idx + 0.5) * voxel_size, (y_idx + 0.5) * voxel_size, (f_idx + 0.5) * voxel_size]


def resolve_provisional_world_center(local_spatial_id: str, coordinate_definition: dict) -> List[float]:
    """座標系2: provisional/world coordinateを返す(Viewer専用)。

    座標系1(resolve_local_center)に、そのLocal Space自身のorigin/rotationを
    適用し、Base Map点群と同じ座標系へ戻したもの。Nodal correspondence等、
    Phase 3の新規コードからは使用禁止(モジュールdocstring参照)。
    """
    _validate_origin_and_rad(coordinate_definition)
    local_x, local_y, local_z = resolve_local_center(local_spatial_id, coordinate_definition)

    theta = coordinate_definition["rad"]
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    # forward変換の回転行列は対合(M@M=単位行列)なので、同じ式を
    # local_x/local_yに再適用するとrel_x/rel_yに戻る(モジュールdocstring参照)
    rel_x = local_x * cos_t + local_y * sin_t
    rel_y = local_x * sin_t - local_y * cos_t

    origin = coordinate_definition["origin"]
    return [rel_x + origin[0], rel_y + origin[1], local_z + origin[2]]


class LocalSpatialIdResolver:
    """(space_id, local_spatial_id) を物理座標へ解決する。

    モジュールdocstring参照。semantic identityは(space_id, local_spatial_id)
    の組であり、space_id経由でLocalSpaceRepositoryから取得した「そのLocal
    Space自身の」CoordinateDefinitionだけを使う(他のLocal Spaceの
    CoordinateDefinitionを取り違えない)。
    """

    def __init__(self, local_space_repository: LocalSpaceRepository):
        self._local_space_repository = local_space_repository

    def resolve_local_center(self, space_id: str, local_spatial_id: str) -> Point3:
        """座標系1(intrinsic local physical coordinate)。Nodal correspondence /
        RigidTransform2D推定は必ずこちらを使う(origin/rotationは使わない)。
        """
        coordinate_definition = self._get_coordinate_definition(space_id)
        center = resolve_local_center(local_spatial_id, coordinate_definition)
        return (center[0], center[1], center[2])

    def resolve_provisional_world_center(self, space_id: str, local_spatial_id: str) -> Point3:
        """座標系2(provisional/world coordinate)。Viewer専用。"""
        coordinate_definition = self._get_coordinate_definition(space_id)
        center = resolve_provisional_world_center(local_spatial_id, coordinate_definition)
        return (center[0], center[1], center[2])

    def resolve_center(self, space_id: str, local_spatial_id: str) -> Point3:
        """【非推奨・Viewer互換専用】resolve_provisional_world_center()の別名。

        名前が曖昧(座標系1・2のどちらを指すか読み手に伝わらない)なため、
        新規のPhase 3コードからは使用禁止。既存のViewer関連呼び出しとの
        後方互換のためだけに残している。
        """
        return self.resolve_provisional_world_center(space_id, local_spatial_id)

    def _get_coordinate_definition(self, space_id: str) -> dict:
        local_space = self._local_space_repository.get(space_id)
        if local_space is None:
            raise ValueError(
                f"space_id '{space_id}' が見つかりません(Local Space Repositoryに存在しません)。"
            )
        if local_space.coordinate_definition is None:
            raise ValueError(f"space_id '{space_id}' にはCoordinateDefinitionがありません。")
        return local_space.coordinate_definition.to_dict()

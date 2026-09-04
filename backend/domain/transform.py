"""
backend/domain/transform.py

Phase 3.0: 剛体変換(回転+並進のみ)の値オブジェクト。

【規約】
`RigidTransform2D` という名前の変数 `T_A_to_B` は、「フレームAで表現した点を
フレームBで表現した点に変換する」変換を表す:

    p_B = T_A_to_B.apply(p_A)

回転はZ軸まわりのyawのみ(反時計回り正、標準的な数学の向き)。
scale(拡大縮小)・reflection(鏡映)は表現できない(常に行列式+1の真の回転)。

    R(yaw) = [[cos(yaw), -sin(yaw)],
              [sin(yaw),  cos(yaw)]]

【重要】backend/server.py の _world_to_spatial_ids が使う
「ワールド座標→ある空間の左手系ローカル座標」変換(行列式-1、鏡映込み)とは
目的も数学的性質も異なる別物である。混同しないこと。あちらはガイドライン
仕様に基づく1方向の変換であり、こちらはNodal Information由来の
Local↔Local / Local↔Global 相互変換(reflection禁止)である。

このモジュールは純粋な値オブジェクトのみを提供する。spatial_id変換・
transform推定(correspondenceからのfit)・グラフ構築・Placement更新は
Phase 3.0の対象外(後続のPhase 3.1以降)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

Point3 = Tuple[float, float, float]


@dataclass(frozen=True)
class RigidTransform2D:
    """yaw(rad) + 3次元並進のみを保持する剛体変換。scale/reflectionのAPIは存在しない。"""

    yaw_rad: float
    translation: Point3  # (tx, ty, tz)

    @staticmethod
    def identity() -> "RigidTransform2D":
        return RigidTransform2D(yaw_rad=0.0, translation=(0.0, 0.0, 0.0))

    @staticmethod
    def from_dict(data: dict) -> "RigidTransform2D":
        """derived resultの永続化(ロードマップPhase 3.6)向けの
        シリアライズ補助。数式・規約は無変更、単純な辞書化/復元のみ。"""
        return RigidTransform2D(yaw_rad=data["yaw_rad"], translation=tuple(data["translation"]))

    def to_dict(self) -> dict:
        return {"yaw_rad": self.yaw_rad, "translation": list(self.translation)}

    def apply(self, point: Point3) -> Point3:
        """p_B = self.apply(p_A) (selfが T_A_to_B の場合)。"""
        x, y, z = point
        cos_t = math.cos(self.yaw_rad)
        sin_t = math.sin(self.yaw_rad)
        tx, ty, tz = self.translation
        return (
            cos_t * x - sin_t * y + tx,
            sin_t * x + cos_t * y + ty,
            z + tz,
        )

    def inverse(self) -> "RigidTransform2D":
        """T_B_to_A = T_A_to_B.inverse()。"""
        inv_yaw = -self.yaw_rad
        cos_t = math.cos(inv_yaw)
        sin_t = math.sin(inv_yaw)
        tx, ty, tz = self.translation
        inv_tx = -(cos_t * tx - sin_t * ty)
        inv_ty = -(sin_t * tx + cos_t * ty)
        return RigidTransform2D(yaw_rad=inv_yaw, translation=(inv_tx, inv_ty, -tz))


def compose(outer: RigidTransform2D, inner: RigidTransform2D) -> RigidTransform2D:
    """T_A_to_C = compose(T_B_to_C, T_A_to_B)

    保証: compose(outer, inner).apply(p) == outer.apply(inner.apply(p))
    (通常の関数合成 f∘g と同じ引数順: 先に適用される方を後ろに書く)
    """
    yaw = outer.yaw_rad + inner.yaw_rad
    cos_o = math.cos(outer.yaw_rad)
    sin_o = math.sin(outer.yaw_rad)
    itx, ity, itz = inner.translation
    otx, oty, otz = outer.translation
    tx = cos_o * itx - sin_o * ity + otx
    ty = sin_o * itx + cos_o * ity + oty
    tz = itz + otz
    return RigidTransform2D(yaw_rad=yaw, translation=(tx, ty, tz))

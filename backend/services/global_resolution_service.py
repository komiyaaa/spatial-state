"""
backend/services/global_resolution_service.py

Global anchor(Local↔GLOBALのNodalConnection)を持つconnected componentの
Global Resolutionを行う(ロードマップPhase 3.4)。

【最重要(ユーザー指示: 2026-09-02)】
GlobalSpatialIdResolver.get_center()/get_bounds()は未実装
(NotImplementedErrorを送出するプレースホルダのまま、
backend/spatial_id/global_spatial_id.py参照)。このモジュールは、
get_center()が使えない場合にダミー座標・仮座標へフォールバックすることを
一切しない。NotImplementedErrorを検出したら、そのanchorを
ANCHOR_UNRESOLVABLE(unresolvable_reason=GLOBAL_RESOLVER_NOT_IMPLEMENTED)
として扱い、そのまま未解決の状態で結果に残す。get_center()を実装する
プロジェクトの別タスクが完了すれば、このモジュールのコードは無変更のまま
実際にGlobal座標を解決できるようになる設計(この「境界」を明示的に
テストしている、テストファイル参照)。

【状態機械の分離】
- domain.component_placement.ComponentPlacementResult(Phase 3.3、Local↔Local
  の相対配置)のstatus(RESOLVED/CONFLICT)と、
- domain.global_resolution.ComponentGlobalResolutionのstatus
  (NO_ANCHOR/BLOCKED_BY_LOCAL_CONFLICT/ANCHOR_UNRESOLVABLE/
  ANCHOR_INSUFFICIENT/RESOLVED/GLOBAL_CONFLICT)
は完全に別の状態機械であり、混同しない。Local↔Local側がCONFLICTの
componentは、Global側の解決を一切試みず、即座にBLOCKED_BY_LOCAL_CONFLICT
を返す(「Local側component CONFLICT時はglobalへ昇格しない」)。

【anchorの推定方法】
1つのLocal↔GLOBAL anchor(GlobalAnchorCandidate)につき、
services.transform_estimation_service.fit_rigid_transform_2d()
(Phase 3.2のLocal↔Local推定と共通のKabsch core、数式・scale/reflection
禁止の保証は完全に同一)を使い、T_local_to_globalを推定する。
- 対応点が2点未満なら、フィットを試みる前にANCHOR_INSUFFICIENTとして
  打ち切る(「1点だけのLocal↔Global対応ではyawを確定しない」)。
- GlobalSpatialIdResolver.get_center()がNotImplementedErrorを送出したら、
  ANCHOR_UNRESOLVABLEとして打ち切る(ダミー座標で埋めない)。
- 実際にfitできた場合、Local側component(Phase 3.3の結果)が保持する
  T_local_space_to_root(component-local placement)を使って、
  T_root_to_global = compose(T_local_to_global, T_local_space_to_root.inverse())
  へ変換する(root自身がanchorの場合はT_local_space_to_rootが恒等なので
  この式のままでよい)。

【複数anchorの整合性検証】
同じcomponent内に複数の有効なanchor(異なるLocal Spaceのものでもよい)が
あれば、それぞれから導いたT_root_to_global候補同士を比較する。全て
許容誤差以内で一致すれば最初の候補を採用してRESOLVEDにする。1つでも
許容誤差を超えて食い違えばGLOBAL_CONFLICTとし、どちらの値も
自動採用しない(fail closed)。

【degree座標を直接Kabschへ渡さない(ユーザー指示: 2026-09-02、Phase 3.5b)】
GlobalSpatialIdResolver.get_center()はGeographicPoint(経度緯度、度単位)を
返す。これをそのままfit_rigid_transform_2d()へ渡すと、Euclid距離を前提と
するKabschフィットが緯度による歪みで正しく機能しない
(2026-09-02のOuranos-GEX公式ライブラリとの比較調査で確認・整理済み)。
そのため、estimate_anchor()は必ず
spatial_id.geographic_projection.to_projected()を経由してProjectedPoint
(メートル単位)へ変換してからfit_rigid_transform_2d()へ渡す。使用した
target_epsgは、ProjectedPoint内部だけで消費せず、AnchorEstimate.target_epsg
/ ComponentGlobalResolution.target_epsgとして最終結果まで残す
(domain.global_resolution参照)。

【対象範囲外(今回はここまで、ユーザー指示: 2026-09-02)】
GUI・Spatial State・point-cloud registrationは対象外。connected component
抽出自体(Local↔Local)はPhase 3.3のservices.spatial_graph /
transform_propagation_serviceの結果をそのまま利用する(ここでは再計算しない)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from domain.component_placement import ComponentPlacementResult, ComponentStatus
from domain.global_resolution import (
    AnchorEstimate,
    AnchorUnresolvableReason,
    ComponentGlobalResolution,
    GlobalAnchorConflict,
    GlobalResolutionStatus,
)
from domain.nodal_connection import SolutionStatus
from domain.transform import RigidTransform2D, compose
from services.transform_estimation_service import (
    DEFAULT_DEGENERACY_EPS_M2,
    DEFAULT_WARNING_RMSE_THRESHOLD_M,
    LocalCorrespondencePoint,
    fit_rigid_transform_2d,
)
from spatial_id.geographic_projection import DEFAULT_TARGET_EPSG, to_projected
from spatial_id.global_spatial_id import GlobalSpatialIdResolver
from spatial_id.local_spatial_id import LocalSpatialIdResolver

# 較正前の初期値(実データでの調整が別途必要。Phase 3.3のcycle許容誤差と同じ値)。
DEFAULT_YAW_TOLERANCE_RAD = 0.01
DEFAULT_TRANSLATION_TOLERANCE_M = 0.05


@dataclass(frozen=True)
class GlobalAnchorCandidate:
    """1つのLocal↔GLOBAL NodalConnection分の対応点(推定前)。

    correspondences: [(そのLocal Space自身のID, global_spatial_id文字列), ...]。
    全ペアで「Local側の空間」が同一である前提(1つのNodalConnectionの
    correspondences配列に対応)。
    """

    connection_id: str
    correspondences: List[Tuple[LocalCorrespondencePoint, str]]


def estimate_anchor(
    anchor: GlobalAnchorCandidate,
    local_space_id: str,
    local_resolver: LocalSpatialIdResolver,
    global_resolver: GlobalSpatialIdResolver,
    warning_rmse_threshold_m: float,
    degeneracy_eps_m2: float,
    target_epsg: int,
) -> AnchorEstimate:
    n = len(anchor.correspondences)
    if n < 2:
        return AnchorEstimate(
            connection_id=anchor.connection_id,
            local_space_id=local_space_id,
            fit_status=SolutionStatus.UNSOLVABLE,
            unresolvable_reason=AnchorUnresolvableReason.INSUFFICIENT_CORRESPONDENCES,
            n_correspondences=n,
        )

    local_points = [
        local_resolver.resolve_local_center(local_point.space_id, local_point.local_spatial_id)
        for local_point, _ in anchor.correspondences
    ]
    try:
        geographic_points = [global_resolver.get_center(global_id) for _, global_id in anchor.correspondences]
    except NotImplementedError:
        return AnchorEstimate(
            connection_id=anchor.connection_id,
            local_space_id=local_space_id,
            fit_status=SolutionStatus.UNSOLVABLE,
            unresolvable_reason=AnchorUnresolvableReason.GLOBAL_RESOLVER_NOT_IMPLEMENTED,
            n_correspondences=n,
        )

    # degree座標をfit_rigid_transform_2d()へ直接渡さない: 必ずProjectedPoint
    # (メートル単位、target_epsg)へ変換してからKabschフィットへ渡す
    # (モジュールdocstring参照)。
    projected_points = [to_projected(p, target_epsg) for p in geographic_points]
    global_points = [(pp.x, pp.y, pp.alt) for pp in projected_points]

    fit = fit_rigid_transform_2d(
        np.asarray(local_points), np.asarray(global_points), warning_rmse_threshold_m, degeneracy_eps_m2
    )

    if fit.status == "UNSOLVABLE":
        return AnchorEstimate(
            connection_id=anchor.connection_id,
            local_space_id=local_space_id,
            fit_status=SolutionStatus.UNSOLVABLE,
            unresolvable_reason=AnchorUnresolvableReason.DEGENERATE,
            n_correspondences=n,
            target_epsg=target_epsg,
        )

    return AnchorEstimate(
        connection_id=anchor.connection_id,
        local_space_id=local_space_id,
        fit_status=SolutionStatus(fit.status),
        transform_local_to_global=RigidTransform2D(yaw_rad=fit.yaw_rad, translation=tuple(fit.translation)),
        rmse_m=fit.rmse_m,
        max_residual_m=fit.max_residual_m,
        n_correspondences=n,
        target_epsg=target_epsg,
    )


def _yaw_diff_rad(yaw_a: float, yaw_b: float) -> float:
    diff = (yaw_a - yaw_b + math.pi) % (2 * math.pi) - math.pi
    return abs(diff)


def _translation_diff_m(t_a, t_b) -> float:
    return math.dist(t_a, t_b)


def resolve_component_global_placement(
    component: ComponentPlacementResult,
    anchors: List[GlobalAnchorCandidate],
    local_resolver: LocalSpatialIdResolver,
    global_resolver: GlobalSpatialIdResolver,
    yaw_tolerance_rad: float = DEFAULT_YAW_TOLERANCE_RAD,
    translation_tolerance_m: float = DEFAULT_TRANSLATION_TOLERANCE_M,
    warning_rmse_threshold_m: float = DEFAULT_WARNING_RMSE_THRESHOLD_M,
    degeneracy_eps_m2: float = DEFAULT_DEGENERACY_EPS_M2,
    target_epsg: int = DEFAULT_TARGET_EPSG,
) -> ComponentGlobalResolution:
    """1つのconnected component(Phase 3.3の結果)と、そのcomponentに属する
    Local Spaceを起点とするLocal↔GLOBAL anchor候補一覧から、Global
    Resolutionを行う。anchorsに他のcomponentのLocal Spaceを起点とするものが
    混ざっていても、このcomponentのmemberでなければ無視する。

    target_epsg: GeographicPoint(度)をProjectedPoint(メートル)へ変換する際の
    投影先CRS。既定はEPSG:6677(JGD2011 / Japan Plane Rectangular CS IX、
    現在の実験地域である東京都新宿区市谷田町付近向け)。他地域を扱う場合は
    呼び出し側が明示的に指定すること(decide-once-hardcodeにしない)。
    """
    if component.status == ComponentStatus.CONFLICT:
        return ComponentGlobalResolution(
            component_id=component.component_id,
            status=GlobalResolutionStatus.BLOCKED_BY_LOCAL_CONFLICT,
        )

    relevant_anchors = []
    for anchor in anchors:
        local_space_ids = {point.space_id for point, _ in anchor.correspondences}
        if not local_space_ids:
            continue
        if len(local_space_ids) > 1:
            raise ValueError(
                f"anchor '{anchor.connection_id}' のLOCAL側に複数のspace_idが混在しています: "
                f"{sorted(local_space_ids)}(1つのNodalConnectionは単一のLocal Spaceを指すべきです)。"
            )
        local_space_id = next(iter(local_space_ids))
        if local_space_id in component.member_space_ids:
            relevant_anchors.append((anchor, local_space_id))

    if not relevant_anchors:
        return ComponentGlobalResolution(component_id=component.component_id, status=GlobalResolutionStatus.NO_ANCHOR)

    anchor_estimates = [
        estimate_anchor(
            anchor, local_space_id, local_resolver, global_resolver,
            warning_rmse_threshold_m, degeneracy_eps_m2, target_epsg,
        )
        for anchor, local_space_id in relevant_anchors
    ]

    resolved = [e for e in anchor_estimates if e.transform_local_to_global is not None]

    if not resolved:
        reasons = {e.unresolvable_reason for e in anchor_estimates}
        if AnchorUnresolvableReason.GLOBAL_RESOLVER_NOT_IMPLEMENTED in reasons:
            status = GlobalResolutionStatus.ANCHOR_UNRESOLVABLE
        elif AnchorUnresolvableReason.INSUFFICIENT_CORRESPONDENCES in reasons:
            status = GlobalResolutionStatus.ANCHOR_INSUFFICIENT
        else:
            status = GlobalResolutionStatus.ANCHOR_INSUFFICIENT
        return ComponentGlobalResolution(
            component_id=component.component_id, status=status, anchor_estimates=anchor_estimates,
            target_epsg=target_epsg,
        )

    # 各resolved anchorから、component root基準のGlobal transform候補を計算する。
    candidates = []
    for estimate in resolved:
        t_member_to_root = component.transforms[estimate.local_space_id]
        t_root_to_global = compose(estimate.transform_local_to_global, t_member_to_root.inverse())
        candidates.append((estimate, t_root_to_global))

    conflicts: List[GlobalAnchorConflict] = []
    base_estimate, base_transform = candidates[0]
    for other_estimate, other_transform in candidates[1:]:
        yaw_diff = _yaw_diff_rad(base_transform.yaw_rad, other_transform.yaw_rad)
        translation_diff = _translation_diff_m(base_transform.translation, other_transform.translation)
        if yaw_diff > yaw_tolerance_rad or translation_diff > translation_tolerance_m:
            conflicts.append(
                GlobalAnchorConflict(
                    connection_id_a=base_estimate.connection_id,
                    connection_id_b=other_estimate.connection_id,
                    local_space_id_a=base_estimate.local_space_id,
                    local_space_id_b=other_estimate.local_space_id,
                    yaw_diff_rad=yaw_diff,
                    translation_diff_m=translation_diff,
                )
            )

    if conflicts:
        return ComponentGlobalResolution(
            component_id=component.component_id,
            status=GlobalResolutionStatus.GLOBAL_CONFLICT,
            anchor_estimates=anchor_estimates,
            conflicts=conflicts,
            target_epsg=target_epsg,
        )

    member_transforms_to_global = {
        space_id: compose(base_transform, component.transforms[space_id])
        for space_id in component.member_space_ids
    }

    return ComponentGlobalResolution(
        component_id=component.component_id,
        status=GlobalResolutionStatus.RESOLVED,
        transform_root_to_global=base_transform,
        member_transforms_to_global=member_transforms_to_global,
        anchor_estimates=anchor_estimates,
        target_epsg=target_epsg,
    )

"""
backend/services/transform_estimation_service.py

Local↔Local Nodal correspondenceから、RigidTransform2D(yaw+translation)を
推定する(ロードマップPhase 3.2)。

【前提(ユーザー指示: 2026-09-02)】
- 各correspondence点は、必ずそのspace_id所有のCoordinateDefinitionを使い、
  LocalSpatialIdResolver.resolve_local_center()(座標系1: intrinsic local
  physical coordinate、origin/rotation不使用)で解決する。
  resolve_provisional_world_center()/resolve_center()(origin/rotation適用、
  Viewer専用、座標系2)は絶対に使わない
  (spatial_id.local_spatial_idのモジュールdocstring参照。2026-09-01の
  座標系調査で「二重反映の危険」が指摘されたのはこの区別のため)。
- local_spatial_id文字列そのものをspace間で直接比較しない。常に
  (space_id, local_spatial_id)の組として、それぞれ自分自身のresolverで
  解決してから、解決済みの物理座標同士を比較する。
- 異なるzoom level同士のcorrespondenceを許可する。LocalSpatialIdResolver.
  resolve_local_center()はlocal_spatial_id自身のzを使うだけで、
  correspondenceの両側でzoomを揃えることを要求しない。
- scale推定・reflection推定は行わない。domain.transform.RigidTransform2Dの
  APIにscale/reflectionを表すフィールド・引数が存在しないこと自体が
  この禁止の保証になっている。2D Kabsch/Procrustesのreflection補正
  (d = sign(det(V @ U^T)))により、常にdet(R)=+1の真の回転のみを出力する
  (推定に使う点群がreflection関係にあっても、reflectionそのものは
  採用せず、単に当てはまりの悪い回転として残差に現れる)。

【対象範囲(今回はここまで、ユーザー指示: 2026-09-02)】
Local↔LocalのNodalConnectionのみを扱う。Local↔Global
(GlobalSpatialIdResolver経由)・connected component解決・Placement永続化・
GUI統合・Spatial State・point-cloud registrationは対象外
(将来の別Phaseで扱う)。

【アルゴリズム(2D Kabsch/Procrustes、2点・N点を同一アルゴリズムで扱う。
2点専用のad-hoc分岐は作らない)】
    1. source点群 P(space Aの座標系1)、target点群 Q(space Bの座標系1)を
       correspondenceの順に並べる
    2. 重心 c_p, c_q。中心化 P', Q'(x, yのみ。z(鉛直)は回転させない)
    3. H = sum_i P'_i (Q'_i)^T (2x2)
    4. SVD: H = U Σ V^T
    5. d = sign(det(V U^T))(reflection防止)
    6. R = V diag(1, d) U^T(det(R) = +1を保証)
    7. yaw = atan2(R[1,0], R[0,0])
    8. translation_xy = c_q - R @ c_p
    9. translation_z = mean(Q_z - P_z)(zは単純オフセット)
    10. 各対応点のresidual_i = |q_i - T.apply(p_i)|、
        RMSE = sqrt(mean(residual_i^2))、max_residual_m = max(residual_i)

domain.transform.RigidTransform2D・compose()の数式・規約(T_A_to_B.apply(p_A)
== p_B、compose(outer, inner)の合成順)は一切変更しない。ここではapply()を
residual計算のためだけに使う。

【UNSOLVABLE判定】
- correspondenceが2点未満(0点・1点)では、回転を一意に決定できないため
  常にUNSOLVABLE(1点ではtransformを確定しない、というユーザー指示に対応)。
- 2点以上あっても、中心化後の点群の広がりが極端に小さい(全対応点が
  ほぼ同一点に潰れている等)場合も、回転が数値的に不定なためUNSOLVABLE
  とする(閾値はdegeneracy_eps_m2引数で外から指定でき、決め打ちにしない)。

【WARNING_HIGH_RESIDUAL判定】
RMSEがwarning_rmse_threshold_mを超える場合はWARNING_HIGH_RESIDUALとする
(reflection関係にある点群等、真の回転では説明できないcorrespondenceを
機械的に検出するための閾値。較正前の初期値であり、実データでの調整が
別途必要)。

【2026-09-02追記: Kabsch coreの共通化】
2D Kabsch/Procrustesの実装本体(fit_rigid_transform_2d)は、Local↔Local用
(この関数)だけでなく、ロードマップPhase 3.4のLocal↔Global anchor推定
(services/global_resolution_service.py)からも再利用する、共通のcore関数
として切り出した。fit_rigid_transform_2d自体は「2つの物理座標点群Nx3を
Kabschで合わせる」という純粋な計算だけを行い、その点群がLocal resolver
由来かGlobal resolver由来かは一切関知しない(呼び出し側の責務)。
estimate_local_to_local_transform()の外部からの見え方(引数・戻り値の型・
数式・既定値)は一切変更していない。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from domain.nodal_connection import ConnectionSolution, SolutionStatus
from domain.transform import RigidTransform2D
from spatial_id.local_spatial_id import LocalSpatialIdResolver

# 中心化後の点群の広がり(2乗和、P側+Q側)がこれ未満なら、回転を数値的に
# 決定できないとみなしUNSOLVABLEにする(較正前の初期値)。
DEFAULT_DEGENERACY_EPS_M2 = 1e-12

# RMSEがこれを超えたらWARNING_HIGH_RESIDUALにする(較正前の初期値)。
DEFAULT_WARNING_RMSE_THRESHOLD_M = 0.5


@dataclass(frozen=True)
class LocalCorrespondencePoint:
    """Local↔Local correspondenceの片側1点(その空間自身のID)。"""

    space_id: str
    local_spatial_id: str


@dataclass(frozen=True)
class RigidPointSetFitResult:
    """fit_rigid_transform_2d()の結果(ConnectionSolutionへ変換する前の、
    Local↔Local・Local↔Global共通の中間結果)。"""

    status: str  # "SOLVED" | "WARNING_HIGH_RESIDUAL" | "UNSOLVABLE"
    yaw_rad: Optional[float]
    translation: Optional[List[float]]
    rmse_m: Optional[float]
    max_residual_m: Optional[float]
    residuals: List[float] = field(default_factory=list)
    n: int = 0


def fit_rigid_transform_2d(
    p_points: np.ndarray,
    q_points: np.ndarray,
    warning_rmse_threshold_m: float = DEFAULT_WARNING_RMSE_THRESHOLD_M,
    degeneracy_eps_m2: float = DEFAULT_DEGENERACY_EPS_M2,
) -> RigidPointSetFitResult:
    """既に物理座標として解決済みの対応点群P(Nx3)・Q(Nx3)から、
    T(P→Q)のyaw+translationを2D Kabsch/Procrustesで推定する(モジュール
    docstringのアルゴリズム参照)。P・Qの由来(どのresolverで解決したか)は
    関知しない。scale推定・reflection推定は行わない。
    """
    n = len(p_points)
    if n < 2:
        return RigidPointSetFitResult(status="UNSOLVABLE", yaw_rad=None, translation=None, rmse_m=None, max_residual_m=None, n=n)

    P = np.asarray(p_points, dtype=np.float64)
    Q = np.asarray(q_points, dtype=np.float64)

    P_xy, Q_xy = P[:, :2], Q[:, :2]
    c_p = P_xy.mean(axis=0)
    c_q = Q_xy.mean(axis=0)
    P_centered = P_xy - c_p
    Q_centered = Q_xy - c_q

    spread = float(np.sum(P_centered ** 2) + np.sum(Q_centered ** 2))
    if spread < degeneracy_eps_m2:
        return RigidPointSetFitResult(status="UNSOLVABLE", yaw_rad=None, translation=None, rmse_m=None, max_residual_m=None, n=n)

    h_matrix = P_centered.T @ Q_centered
    u_mat, _singular_values, vt_mat = np.linalg.svd(h_matrix)
    v_mat = vt_mat.T
    d = 1.0 if np.linalg.det(v_mat @ u_mat.T) >= 0 else -1.0
    rotation = v_mat @ np.diag([1.0, d]) @ u_mat.T

    yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    translation_xy = c_q - rotation @ c_p
    translation_z = float(np.mean(Q[:, 2] - P[:, 2]))

    transform = RigidTransform2D(
        yaw_rad=yaw,
        translation=(float(translation_xy[0]), float(translation_xy[1]), translation_z),
    )

    residuals = []
    for p, q in zip(P, Q):
        predicted = transform.apply((float(p[0]), float(p[1]), float(p[2])))
        residuals.append(math.dist(predicted, (float(q[0]), float(q[1]), float(q[2]))))

    rmse = math.sqrt(sum(r ** 2 for r in residuals) / n)
    max_residual = max(residuals)
    status = "SOLVED" if rmse <= warning_rmse_threshold_m else "WARNING_HIGH_RESIDUAL"

    return RigidPointSetFitResult(
        status=status,
        yaw_rad=yaw,
        translation=list(transform.translation),
        rmse_m=rmse,
        max_residual_m=max_residual,
        residuals=residuals,
        n=n,
    )


def estimate_local_to_local_transform(
    resolver: LocalSpatialIdResolver,
    correspondences: List[Tuple[LocalCorrespondencePoint, LocalCorrespondencePoint]],
    warning_rmse_threshold_m: float = DEFAULT_WARNING_RMSE_THRESHOLD_M,
    degeneracy_eps_m2: float = DEFAULT_DEGENERACY_EPS_M2,
) -> ConnectionSolution:
    """1つのLocal↔Local NodalConnection分のcorrespondence一覧から、
    T_A_to_B(space Aの座標系1で表した点をspace Bの座標系1で表した点に
    変換する)を推定し、ConnectionSolutionとして返す。

    - correspondences: [(point_in_space_a, point_in_space_b), ...]。
      全ペアで「Aの空間」「Bの空間」の役割が一貫している前提
      (1つのNodalConnectionのcorrespondences配列に対応)。呼び出し側の責務。
    - 各pointはresolver.resolve_local_center(space_id, local_spatial_id)
      (座標系1)で解決する。座標系2(resolve_provisional_world_center /
      resolve_center)は使わない。
    """
    n = len(correspondences)
    if n < 2:
        return ConnectionSolution(
            status=SolutionStatus.UNSOLVABLE, n_correspondences=n, updated_at=_now()
        )

    p_points = np.asarray(
        [resolver.resolve_local_center(a.space_id, a.local_spatial_id) for a, _ in correspondences]
    )
    q_points = np.asarray(
        [resolver.resolve_local_center(b.space_id, b.local_spatial_id) for _, b in correspondences]
    )

    fit = fit_rigid_transform_2d(p_points, q_points, warning_rmse_threshold_m, degeneracy_eps_m2)

    return ConnectionSolution(
        status=SolutionStatus(fit.status),
        n_correspondences=n,
        yaw_rad=fit.yaw_rad,
        translation=fit.translation,
        rmse_m=fit.rmse_m,
        max_residual_m=fit.max_residual_m,
        residuals=fit.residuals,
        updated_at=_now(),
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")

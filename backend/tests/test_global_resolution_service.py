"""
backend/services/global_resolution_service.py の動作確認テスト
(ロードマップPhase 3.4: Global anchorを持つcomponentのGlobal Resolution)。

【2026-09-02追記】GlobalSpatialIdResolver.get_center()はPhase 3.5で実装済み
(backend/spatial_id/global_spatial_id.py の StandardSpatialIdResolver、
ガイドライン§2.4.2の逆変換、GeographicPoint(lon,lat,alt)を返す)。
Phase 3.5bでは、degree座標を直接fit_rigid_transform_2d()へ渡さないよう、
spatial_id.geographic_projection.to_projected()(pyproj、既定EPSG:6677)を
経由する変換を追加した。

本ファイルの大半のテストは、「anchorが解決できた場合」のロジックを
検証するためだけに、テスト専用のフェイク実装(_FakeGlobalSpatialIdResolver、
本番コードには一切含まれない)を使い続ける。ただし、フェイクが返す
GeographicPointは、実際にpyproj(本番のto_projected()と同じ変換)で
EPSG:6677→EPSG:4326の逆投影をして作った、実在の投影座標に対応する値にして
いる(_geographic_point_for_projected参照)。これにより、本番の
「degree→meter変換を経由する」パイプライン全体をフェイクのままでも
実際に検証できる。

加えて、本番のStandardSpatialIdResolverを実際に使ったテストを2本用意して
いる: 1つは「実装済みのget_center()→to_projected()を経由してRESOLVEDまで
到達できる」ことを確認する配線テスト、もう1つは極地空間ID等、今回も
スコープ外のままの経路がANCHOR_UNRESOLVABLEになることの確認。

実行方法(リポジトリルートから):
    python backend/tests/test_global_resolution_service.py
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

import pyproj

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from domain.component_placement import ComponentPlacementResult, ComponentStatus  # noqa: E402
from domain.global_resolution import AnchorUnresolvableReason, GlobalResolutionStatus  # noqa: E402
from domain.nodal_connection import (  # noqa: E402
    ConnectionEndpointRef,
    ConnectionEndpointType,
    ConnectionSolution,
    NodalConnection,
    SolutionStatus,
)
from domain.transform import RigidTransform2D  # noqa: E402
from repositories.local_space_repository import LocalSpaceRepository  # noqa: E402
from services.global_resolution_service import GlobalAnchorCandidate, resolve_component_global_placement  # noqa: E402
from services.transform_estimation_service import LocalCorrespondencePoint  # noqa: E402
from services.transform_propagation_service import resolve_component_placements  # noqa: E402
from spatial_id.geographic_projection import DEFAULT_TARGET_EPSG, to_projected  # noqa: E402
from spatial_id.global_spatial_id import GeographicPoint, StandardSpatialIdResolver  # noqa: E402
from spatial_id.local_spatial_id import LocalSpatialIdResolver  # noqa: E402

_TOL = 1e-6
_VOXEL_SIZE = 1e-9

# EPSG:6677(x,y)→EPSG:4326(lon,lat)の逆変換(テストのfixture作成専用。
# 本番のto_projected()と対になる、テスト側だけの逆方向ヘルパー)。
_INVERSE_TRANSFORMER = pyproj.Transformer.from_crs(
    f"EPSG:{DEFAULT_TARGET_EPSG}", "EPSG:4326", always_xy=True
)


def _geographic_point_for_projected(x: float, y: float, alt: float) -> GeographicPoint:
    """指定した投影座標(EPSG:6677、メートル)に対応するGeographicPointを作る
    (テスト用。本番のStandardSpatialIdResolver.get_center()の代わりに、
    フェイク resolver が返す値として使う)。"""
    lon, lat = _INVERSE_TRANSFORMER.transform(x, y)
    return GeographicPoint(lon=lon, lat=lat, alt=alt)


class _FakeGlobalSpatialIdResolver:
    """テスト専用のGlobalSpatialIdResolver実装(本番コードには含まれない)。
    Phase 3.4/3.5bのロジック(anchor推定・複数anchor整合性検証・degree→meter
    変換の配線)を検証するために、global_spatial_id文字列に対する固定の
    GeographicPointをテスト側で用意するだけのテストダブル。本番の
    StandardSpatialIdResolver.get_center()はNotImplementedErrorのまま
    (別テストで確認する)。"""

    def __init__(self, centers: dict):
        self._centers = centers

    def parse(self, global_spatial_id):
        raise NotImplementedError("このフェイクはparse()を検証しない")

    def get_center(self, global_spatial_id: str) -> GeographicPoint:
        return self._centers[global_spatial_id]

    def get_bounds(self, global_spatial_id: str):
        raise NotImplementedError("このフェイクはget_bounds()を検証しない")


def _make_coordinate_definition(zoom: str = "0", voxel_size: float = _VOXEL_SIZE) -> dict:
    return {
        "id": "test", "degree": 0.0, "rad": 0.0, "height": 1.0,
        "origin": [0.0, 0.0, 0.0],
        "unit-size": {zoom: voxel_size},
        "bounds": [[0.0, 0.0, 0.0] for _ in range(8)],
    }


def _id_for_point(zoom: str, point, voxel_size: float = _VOXEL_SIZE) -> str:
    x, y, z = point
    x_idx = round(x / voxel_size - 0.5)
    y_idx = round(y / voxel_size - 0.5)
    f_idx = round(z / voxel_size - 0.5)
    return f"{zoom}/{f_idx}/{x_idx}/{y_idx}"


def _add_space(repo, space_def_dir, building_id, tokutei_code) -> str:
    space_def = _make_coordinate_definition()
    (space_def_dir / f"{tokutei_code}.json").write_text(json.dumps(space_def), encoding="utf-8")
    repo.create(building_id=building_id, tokutei_code=tokutei_code, floor=1, zoom_level=0)
    return f"{building_id}-{tokutei_code}"


def _make_repo_and_resolver(tmp):
    registry_dir = Path(tmp) / "registry"
    space_def_dir = Path(tmp) / "space_definitions"
    space_def_dir.mkdir()
    repo = LocalSpaceRepository(registry_dir, space_def_dir)
    return repo, space_def_dir, LocalSpatialIdResolver(repo)


def _trivial_component(space_id: str) -> ComponentPlacementResult:
    """Local↔Local接続を一切持たない、単独のLocal Spaceだけのcomponent
    (Phase 3.3の実行結果を模した、テスト用の最小構成)。"""
    return ComponentPlacementResult(
        component_id=space_id,
        member_space_ids=[space_id],
        root_space_id=space_id,
        transforms={space_id: RigidTransform2D.identity()},
        status=ComponentStatus.RESOLVED,
        conflicts=[],
    )


def _make_anchor(connection_id, space_id, local_points, global_transform: RigidTransform2D, resolver):
    """local_points(そのspaceの座標系1での点、メートル)をglobal_transformで
    Global側の投影座標(EPSG:6677、メートル)へ写像し、それを実際にpyprojで
    逆投影したGeographicPointをフェイクresolverのcentersとして持たせる。

    こうすることで、本番の
    「GeographicPoint(度) → to_projected()(pyproj) → メートル」という
    パイプライン全体を、フェイクのままでも実際に通して検証できる
    (単にglobal_transform.apply(p)の生tupleをそのままcenterとして
    使うわけではない、2026-09-02のPhase 3.5b変更に対応)。
    """
    correspondences = []
    centers = {}
    for i, p in enumerate(local_points):
        local_id = _id_for_point("0", p)
        global_id = f"GLOBAL/{connection_id}/{i}"
        correspondences.append((LocalCorrespondencePoint(space_id, local_id), global_id))
        x, y, alt = global_transform.apply(p)
        centers[global_id] = _geographic_point_for_projected(x, y, alt)
    return GlobalAnchorCandidate(connection_id=connection_id, correspondences=correspondences), centers


def test_no_anchor_component_is_connected_but_global_unresolved():
    """Local↔Local側はRESOLVED(接続されている)が、Global anchorが1つも
    無い場合、Global側はNO_ANCHOR(未解決)として区別されることを確認する。"""
    space_a, space_b = "b1-A", "b1-B"
    known = RigidTransform2D(yaw_rad=0.2, translation=(1.0, 1.0, 0.0))
    connection = NodalConnection(
        connection_id="c1", building_id="b1",
        endpoint_space_a=ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_a),
        endpoint_space_b=ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_b),
        correspondences=[],
        solution=ConnectionSolution(
            status=SolutionStatus.SOLVED, n_correspondences=2,
            yaw_rad=known.yaw_rad, translation=list(known.translation), rmse_m=0.001, max_residual_m=0.001,
        ),
    )
    components = resolve_component_placements([connection])
    assert len(components) == 1
    component = components[0]
    assert component.status == ComponentStatus.RESOLVED  # Local↔Local側は正常に接続されている

    with tempfile.TemporaryDirectory() as tmp:
        _repo, _dir, local_resolver = _make_repo_and_resolver(tmp)
        global_resolver = _FakeGlobalSpatialIdResolver({})
        result = resolve_component_global_placement(component, [], local_resolver, global_resolver)

    assert result.status == GlobalResolutionStatus.NO_ANCHOR
    assert result.transform_root_to_global is None
    print("test_no_anchor_component_is_connected_but_global_unresolved: OK")


def test_single_point_anchor_leaves_global_pose_undetermined():
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, local_resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        component = _trivial_component(space_a)

        known = RigidTransform2D(yaw_rad=0.4, translation=(5.0, -2.0, 0.0))
        anchor, centers = _make_anchor("anchor1", space_a, [(0.0, 0.0, 0.0)], known, local_resolver)  # 1点のみ
        global_resolver = _FakeGlobalSpatialIdResolver(centers)

        result = resolve_component_global_placement(component, [anchor], local_resolver, global_resolver)

    assert result.status == GlobalResolutionStatus.ANCHOR_INSUFFICIENT
    assert result.transform_root_to_global is None
    assert result.anchor_estimates[0].unresolvable_reason == AnchorUnresolvableReason.INSUFFICIENT_CORRESPONDENCES
    print("test_single_point_anchor_leaves_global_pose_undetermined: OK")


def test_valid_anchor_resolves_global_pose():
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, local_resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        component = _trivial_component(space_a)

        known = RigidTransform2D(yaw_rad=0.7, translation=(10.0, -4.0, 1.0))
        local_points = [(0.0, 0.0, 0.0), (3.0, 1.0, 0.0), (1.0, 4.0, 0.0)]
        anchor, centers = _make_anchor("anchor1", space_a, local_points, known, local_resolver)
        global_resolver = _FakeGlobalSpatialIdResolver(centers)

        result = resolve_component_global_placement(component, [anchor], local_resolver, global_resolver)

    assert result.status == GlobalResolutionStatus.RESOLVED
    assert math.isclose(result.transform_root_to_global.yaw_rad, known.yaw_rad, abs_tol=1e-4)
    for a, b in zip(result.transform_root_to_global.translation, known.translation):
        assert math.isclose(a, b, abs_tol=1e-4)
    assert space_a in result.member_transforms_to_global
    # target_epsgが最終結果まで残っていること(ProjectedPoint内部だけで
    # 消費して捨てない、ユーザー指示: 2026-09-02)。
    assert result.target_epsg == DEFAULT_TARGET_EPSG
    assert result.anchor_estimates[0].target_epsg == DEFAULT_TARGET_EPSG
    print("test_valid_anchor_resolves_global_pose: OK")


def test_anchor_on_non_root_member_propagates_to_root():
    """anchorがcomponentのroot以外のmemberに付いていても、Phase 3.3の
    component-local placementを使って正しくroot基準へ変換されることを確認する。"""
    space_a, space_b = "b1-A", "b1-B"  # root は辞書順最小の b1-A
    local_to_local = RigidTransform2D(yaw_rad=0.35, translation=(4.0, 2.0, 0.0))
    connection = NodalConnection(
        connection_id="c1", building_id="b1",
        endpoint_space_a=ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_a),
        endpoint_space_b=ConnectionEndpointRef(type=ConnectionEndpointType.LOCAL, space_id=space_b),
        correspondences=[],
        solution=ConnectionSolution(
            status=SolutionStatus.SOLVED, n_correspondences=2,
            yaw_rad=local_to_local.yaw_rad, translation=list(local_to_local.translation),
            rmse_m=0.001, max_residual_m=0.001,
        ),
    )
    component = resolve_component_placements([connection])[0]
    assert component.root_space_id == space_a

    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, local_resolver = _make_repo_and_resolver(tmp)
        _add_space(repo, space_def_dir, "b1", "A")
        _add_space(repo, space_def_dir, "b1", "B")

        known_b_to_global = RigidTransform2D(yaw_rad=-0.2, translation=(100.0, 50.0, 0.0))
        local_points = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0)]
        anchor, centers = _make_anchor("anchor_on_b", space_b, local_points, known_b_to_global, local_resolver)
        global_resolver = _FakeGlobalSpatialIdResolver(centers)

        result = resolve_component_global_placement(component, [anchor], local_resolver, global_resolver)

    assert result.status == GlobalResolutionStatus.RESOLVED
    # root(A)からの物理点round-tripで検証: p_A -> (local↔local)p_B -> (anchor)global が、
    # root基準のmember_transforms_to_global["b1-A"]で直接p_A -> globalとしても一致するはず。
    p_a = (1.0, 1.0, 0.0)
    p_b = local_to_local.apply(p_a)
    expected_global = known_b_to_global.apply(p_b)
    actual_global = result.member_transforms_to_global[space_a].apply(p_a)
    for x, y in zip(actual_global, expected_global):
        assert math.isclose(x, y, abs_tol=1e-4)
    print("test_anchor_on_non_root_member_propagates_to_root: OK")


def test_multiple_consistent_anchors_resolve():
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, local_resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        component = _trivial_component(space_a)

        known = RigidTransform2D(yaw_rad=0.9, translation=(-3.0, 7.0, 0.0))
        anchor1, centers1 = _make_anchor(
            "anchor1", space_a, [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 3.0, 0.0)], known, local_resolver
        )
        anchor2, centers2 = _make_anchor(
            "anchor2", space_a, [(5.0, 1.0, 0.0), (5.0, 4.0, 0.0), (1.0, 5.0, 0.0)], known, local_resolver
        )
        global_resolver = _FakeGlobalSpatialIdResolver({**centers1, **centers2})

        result = resolve_component_global_placement(component, [anchor1, anchor2], local_resolver, global_resolver)

    assert result.status == GlobalResolutionStatus.RESOLVED
    assert result.conflicts == []
    assert math.isclose(result.transform_root_to_global.yaw_rad, known.yaw_rad, abs_tol=1e-4)
    print("test_multiple_consistent_anchors_resolve: OK")


def test_multiple_inconsistent_anchors_cause_global_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, local_resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        component = _trivial_component(space_a)

        known1 = RigidTransform2D(yaw_rad=0.9, translation=(-3.0, 7.0, 0.0))
        known2 = RigidTransform2D(yaw_rad=0.9 + 0.5, translation=(-3.0 + 20.0, 7.0, 0.0))  # 大きくずらす

        anchor1, centers1 = _make_anchor(
            "anchor1", space_a, [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 3.0, 0.0)], known1, local_resolver
        )
        anchor2, centers2 = _make_anchor(
            "anchor2", space_a, [(5.0, 1.0, 0.0), (5.0, 4.0, 0.0), (1.0, 5.0, 0.0)], known2, local_resolver
        )
        global_resolver = _FakeGlobalSpatialIdResolver({**centers1, **centers2})

        result = resolve_component_global_placement(component, [anchor1, anchor2], local_resolver, global_resolver)

    assert result.status == GlobalResolutionStatus.GLOBAL_CONFLICT
    assert result.transform_root_to_global is None  # どちらの値も自動採用しない
    assert len(result.conflicts) == 1
    print("test_multiple_inconsistent_anchors_cause_global_conflict: OK")


def test_local_conflict_component_is_not_promoted_to_global():
    """Local↔Local側がCONFLICTのcomponentは、有効なanchorがあってもGlobal側の
    解決を一切試みず、BLOCKED_BY_LOCAL_CONFLICTになることを確認する。"""
    component = ComponentPlacementResult(
        component_id="b1-A", member_space_ids=["b1-A", "b1-B"], root_space_id="b1-A",
        transforms={"b1-A": RigidTransform2D.identity(), "b1-B": RigidTransform2D.identity()},
        status=ComponentStatus.CONFLICT,
    )
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, local_resolver = _make_repo_and_resolver(tmp)
        _add_space(repo, space_def_dir, "b1", "A")
        known = RigidTransform2D(yaw_rad=0.1, translation=(1.0, 1.0, 0.0))
        anchor, centers = _make_anchor(
            "anchor1", "b1-A", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)], known, local_resolver
        )
        global_resolver = _FakeGlobalSpatialIdResolver(centers)
        result = resolve_component_global_placement(component, [anchor], local_resolver, global_resolver)

    assert result.status == GlobalResolutionStatus.BLOCKED_BY_LOCAL_CONFLICT
    assert result.anchor_estimates == []  # anchorの推定すら試みていない
    print("test_local_conflict_component_is_not_promoted_to_global: OK")


def test_real_global_resolver_reports_not_implemented_boundary():
    """本番のStandardSpatialIdResolverを実際に使い、今回もスコープ外のまま
    残っている経路(極地空間ID、z<0)では、ダミー座標へフォールバックせず、
    ANCHOR_UNRESOLVABLE(GLOBAL_RESOLVER_NOT_IMPLEMENTED)として扱われる
    ことを確認する(Phase 3.5でget_center()自体は実装したが、極地空間IDは
    引き続き未対応であることの境界確認。フェイクは一切使わない)。"""
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, local_resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        component = _trivial_component(space_a)

        correspondences = [
            (LocalCorrespondencePoint(space_a, _id_for_point("0", (0.0, 0.0, 0.0))), "-9/0/0/0"),
            (LocalCorrespondencePoint(space_a, _id_for_point("0", (1.0, 0.0, 0.0))), "-9/0/1/0"),
        ]
        anchor = GlobalAnchorCandidate(connection_id="anchor1", correspondences=correspondences)
        real_global_resolver = StandardSpatialIdResolver()  # 本番実装(極地空間IDは未実装のまま)

        result = resolve_component_global_placement(component, [anchor], local_resolver, real_global_resolver)

    assert result.status == GlobalResolutionStatus.ANCHOR_UNRESOLVABLE
    assert result.transform_root_to_global is None
    assert result.anchor_estimates[0].unresolvable_reason == AnchorUnresolvableReason.GLOBAL_RESOLVER_NOT_IMPLEMENTED
    # get_center()に到達する前に失敗しているため、投影は一切行われていない
    # (target_epsgはNoneのまま、実際に使われた場合とは区別される)。
    assert result.anchor_estimates[0].target_epsg is None
    print("test_real_global_resolver_reports_not_implemented_boundary: OK")


def test_real_global_resolver_reaches_resolved_with_valid_anchor():
    """Phase 3.5でget_center()を、Phase 3.5bでto_projected()(pyproj、
    EPSG:6677)を実装したことを受けて、本物のStandardSpatialIdResolver
    (フェイクではない)を使い、degree座標を経由しても正しくmetricな
    RigidTransform2Dが復元できることを確認する(配線・単位変換の正しさの
    両方の確認)。

    実際の(度単位の)Global Spatial ID centerをto_projected()でメートルへ
    変換し、既知のtransform(恒等変換ではない)をその逆で適用してLocal側の
    点を作る。fitが実際にmetric空間で行われていなければ、この既知transform
    (yaw=0.3rad、並進5m/-2m/1m)を正しく復元できないはず。
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo, space_def_dir, local_resolver = _make_repo_and_resolver(tmp)
        space_a = _add_space(repo, space_def_dir, "b1", "A")
        component = _trivial_component(space_a)

        real_global_resolver = StandardSpatialIdResolver()
        global_ids = ["16/0/58000/25000", "16/0/58001/25000", "16/0/58000/25001"]
        geographic_centers = [real_global_resolver.get_center(gid) for gid in global_ids]
        projected_centers = [to_projected(p, DEFAULT_TARGET_EPSG) for p in geographic_centers]

        known = RigidTransform2D(yaw_rad=0.3, translation=(5.0, -2.0, 1.0))
        correspondences = []
        for gid, pp in zip(global_ids, projected_centers):
            # known.apply(local_metric_point) == (pp.x, pp.y, pp.alt) となるような
            # local側の点(既知transformの逆で求める)。
            local_metric_point = known.inverse().apply((pp.x, pp.y, pp.alt))
            local_id = _id_for_point("0", local_metric_point)
            correspondences.append((LocalCorrespondencePoint(space_a, local_id), gid))

        anchor = GlobalAnchorCandidate(connection_id="anchor_real", correspondences=correspondences)
        result = resolve_component_global_placement(
            component, [anchor], local_resolver, real_global_resolver, target_epsg=DEFAULT_TARGET_EPSG
        )

    assert result.status == GlobalResolutionStatus.RESOLVED
    assert result.target_epsg == DEFAULT_TARGET_EPSG
    assert result.anchor_estimates[0].target_epsg == DEFAULT_TARGET_EPSG
    assert math.isclose(result.transform_root_to_global.yaw_rad, known.yaw_rad, abs_tol=1e-4)
    for a, b in zip(result.transform_root_to_global.translation, known.translation):
        assert math.isclose(a, b, abs_tol=1e-4)
    print("test_real_global_resolver_reaches_resolved_with_valid_anchor: OK")


if __name__ == "__main__":
    test_no_anchor_component_is_connected_but_global_unresolved()
    test_single_point_anchor_leaves_global_pose_undetermined()
    test_valid_anchor_resolves_global_pose()
    test_anchor_on_non_root_member_propagates_to_root()
    test_multiple_consistent_anchors_resolve()
    test_multiple_inconsistent_anchors_cause_global_conflict()
    test_local_conflict_component_is_not_promoted_to_global()
    test_real_global_resolver_reports_not_implemented_boundary()
    test_real_global_resolver_reaches_resolved_with_valid_anchor()
    print()
    print("全テスト成功。")

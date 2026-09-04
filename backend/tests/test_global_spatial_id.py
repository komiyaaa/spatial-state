"""
backend/spatial_id/global_spatial_id.py の get_center()/get_bounds() 動作確認
テスト(ロードマップPhase 3.5)。

実装根拠: 4dspatio-temporal-id-guideline-v1_2.pdf §2.4.2(標準空間IDの各
インデックス算出方法、ラジアン統一の等価式)。本テストファイルは、この
ガイドラインのforward式を独立に再実装した_forward_standard_id()を使い、
get_center()がその逆変換になっていることをround-tripで確認する
(「既存Spatial ID変換処理との整合」の確認)。

get_center()/get_bounds()はGeographicPoint(lon, lat, alt)を返す
(2026-09-02、Ouranos-GEX公式Pythonライブラリとの比較調査でこの軸順序に
統一した)。

実行方法(リポジトリルートから):
    python backend/tests/test_global_spatial_id.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))
from spatial_id.global_spatial_id import GeographicPoint, StandardSpatialIdResolver  # noqa: E402

_VERTICAL_UNIT_ZOOM = 25
_EARTH_CIRCUMFERENCE_M = 40_075_016.68  # ガイドライン表2-1のzoom0水平サイズ(赤道)


def _forward_standard_id(lng_deg: float, lat_deg: float, h_m: float, z: int) -> str:
    """ガイドライン§2.4.2のforward式(ラジアン統一の等価式)を、
    get_center()の実装とは独立に再実装したもの(round-trip検証専用)。"""
    n = 2 ** z
    h_vertical = 2 ** _VERTICAL_UNIT_ZOOM
    lng_rad = math.radians(lng_deg)
    lat_rad = math.radians(lat_deg)
    f = math.floor(n * h_m / h_vertical)
    x = math.floor(n * (0.5 + lng_rad / (2 * math.pi)))
    y = math.floor(n * (0.5 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / (2 * math.pi)))
    return f"{z}/{f}/{x}/{y}"


def test_get_center_from_parsed_id():
    resolver = StandardSpatialIdResolver()
    z = 10
    n = 2 ** z
    center = resolver.get_center(f"{z}/3/500/300")

    expected_lng = ((500 + 0.5) / n) * 360.0 - 180.0
    expected_elevation = (3 + 0.5) * (2 ** _VERTICAL_UNIT_ZOOM) / n
    assert math.isclose(center.lon, expected_lng, abs_tol=1e-9)
    assert math.isclose(center.alt, expected_elevation, abs_tol=1e-6)
    assert -90.0 < center.lat < 90.0
    print("test_get_center_from_parsed_id: OK")


def test_adjacent_indices_move_in_expected_direction():
    resolver = StandardSpatialIdResolver()
    z = 12
    base = resolver.get_center(f"{z}/0/1000/1000")

    # xインデックス+1 → 東(経度が増える)方向へ移動する
    point_dx = resolver.get_center(f"{z}/0/1001/1000")
    assert point_dx.lon > base.lon
    assert math.isclose(point_dx.lat, base.lat, abs_tol=1e-9)  # yを変えていないので緯度は不変

    # yインデックス+1 → 南(緯度が減る)方向へ移動する
    point_dy = resolver.get_center(f"{z}/0/1000/1001")
    assert point_dy.lat < base.lat
    assert math.isclose(point_dy.lon, base.lon, abs_tol=1e-9)

    # fインデックス+1 → 標高が増える方向へ移動する
    point_df = resolver.get_center(f"{z}/1/1000/1000")
    assert point_df.alt > base.alt
    print("test_adjacent_indices_move_in_expected_direction: OK")


def test_zoom_level_changes_cell_size():
    resolver = StandardSpatialIdResolver()
    z, x, y, f = 8, 100, 100, 0

    min_a, max_a = resolver.get_bounds(f"{z}/{f}/{x}/{y}")
    # 1段細かいzoom levelでは、同じ親領域が2x2x2に分割されるため、
    # 対応する子(x*2, y*2, f*2)のセルサイズは半分になる。
    min_b, max_b = resolver.get_bounds(f"{z + 1}/{f * 2}/{x * 2}/{y * 2}")

    lng_extent_a = max_a.lon - min_a.lon
    lng_extent_b = max_b.lon - min_b.lon
    elev_extent_a = max_a.alt - min_a.alt
    elev_extent_b = max_b.alt - min_b.alt

    assert math.isclose(lng_extent_b, lng_extent_a / 2, rel_tol=1e-9)
    assert math.isclose(elev_extent_b, elev_extent_a / 2, rel_tol=1e-9)
    # 子セルの最小経度・最小標高は親セルの最小側と一致するはず(左手前隅が共通)
    assert math.isclose(min_a.lon, min_b.lon, abs_tol=1e-9)
    assert math.isclose(min_a.alt, min_b.alt, abs_tol=1e-9)
    print("test_zoom_level_changes_cell_size: OK")


def test_center_is_inside_bounds():
    resolver = StandardSpatialIdResolver()
    for spatial_id in ("5/2/10/10", "15/-3/12345/23456", "0/0/0/0", "0/-1/0/0"):
        center = resolver.get_center(spatial_id)
        bounds_min, bounds_max = resolver.get_bounds(spatial_id)
        assert bounds_min.lat <= center.lat <= bounds_max.lat, spatial_id
        assert bounds_min.lon <= center.lon <= bounds_max.lon, spatial_id
        assert bounds_min.alt <= center.alt <= bounds_max.alt, spatial_id
    print("test_center_is_inside_bounds: OK")


def test_invalid_id_raises():
    resolver = StandardSpatialIdResolver()

    # 書式が不正(既存parse()がそのまま検出する)
    for bad_format in ("not-an-id", "10/0/0", "10/0/0/0/0", "10/a/0/0"):
        try:
            resolver.get_center(bad_format)
            raise AssertionError(f"不正な書式が受理されてしまった: {bad_format!r}")
        except ValueError:
            pass

    # 書式は正しいが、そのzoom levelのx/y/f有効範囲外
    z = 4
    n = 2 ** z
    for out_of_range in (f"{z}/0/{n}/0", f"{z}/0/-1/0", f"{z}/0/0/{n}", f"{z}/{n}/0/0", f"{z}/{-n - 1}/0/0"):
        try:
            resolver.get_center(out_of_range)
            raise AssertionError(f"範囲外のインデックスが受理されてしまった: {out_of_range!r}")
        except ValueError:
            pass

    # 極地空間ID(z<0)は、今回のスコープ外としてNotImplementedError
    try:
        resolver.get_center("-10/0/0/0")
        raise AssertionError("極地空間IDが受理されてしまった")
    except NotImplementedError:
        pass
    print("test_invalid_id_raises: OK")


def test_round_trip_matches_forward_transform():
    """既存のforward変換(ガイドライン§2.4.2の式を独立実装したもの)とget_center()
    のround-trip整合を、複数の実在しそうな緯度経度・複数zoomで確認する。"""
    resolver = StandardSpatialIdResolver()
    samples = [
        (139.6917, 35.6895, 40.0),   # 東京都庁付近
        (127.6809, 26.2124, 5.0),    # 那覇市付近
        (141.3545, 43.0621, 20.0),   # 札幌市付近
        (0.0, 0.0, 0.0),             # 赤道・本初子午線
        (-179.9, -85.0, 0.0),        # 範囲の端に近い点
    ]
    for lng, lat, h in samples:
        for z in (10, 16, 20):
            spatial_id = _forward_standard_id(lng, lat, h, z)
            center = resolver.get_center(spatial_id)
            bounds_min, bounds_max = resolver.get_bounds(spatial_id)

            # 元の点は、逆算したcenterが属するboundsの内部に入っているはず
            assert bounds_min.lat <= lat <= bounds_max.lat, (lng, lat, h, z, spatial_id)
            assert bounds_min.lon <= lng <= bounds_max.lon, (lng, lat, h, z, spatial_id)
            assert bounds_min.alt <= h <= bounds_max.alt, (lng, lat, h, z, spatial_id)

            # centerとboundsの整合(前のtestと同じ性質だが、ここでも再確認)
            assert bounds_min.lat <= center.lat <= bounds_max.lat
            assert bounds_min.lon <= center.lon <= bounds_max.lon
    print("test_round_trip_matches_forward_transform: OK")


def test_matches_published_zoom0_and_zoom16_cell_size_at_equator():
    """ガイドライン表2-1(赤道における空間ボクセルのサイズ例)と、
    get_bounds()から導出したセル幅(度→メートル換算)が一致することを確認する
    (既存の公式リファレンス値との整合確認)。"""
    resolver = StandardSpatialIdResolver()

    # zoom 0: 全体が1セル(全経度範囲、-180〜180度)
    min0, max0 = resolver.get_bounds("0/0/0/0")
    assert math.isclose(max0.lon - min0.lon, 360.0, abs_tol=1e-9)
    width_m_0 = (max0.lon - min0.lon) * (_EARTH_CIRCUMFERENCE_M / 360.0)
    assert math.isclose(width_m_0, _EARTH_CIRCUMFERENCE_M, rel_tol=1e-6)  # 表2-1: 40,075,016.68m

    # zoom 16: 赤道(y=n/2付近)でのセル幅が表2-1の611.50mと一致する
    z = 16
    n = 2 ** z
    min16, max16 = resolver.get_bounds(f"{z}/0/0/{n // 2}")
    width_m_16 = (max16.lon - min16.lon) * (_EARTH_CIRCUMFERENCE_M / 360.0)
    assert math.isclose(width_m_16, 611.50, rel_tol=1e-3)  # 表2-1: 611.50m
    print("test_matches_published_zoom0_and_zoom16_cell_size_at_equator: OK")


def test_get_center_returns_geographic_point_with_lon_first():
    """戻り値がGeographicPoint(lon, lat, altの順、公式Ouranos-GEXライブラリの
    Point(lon,lat,alt)に合わせた順序)であり、生tupleではないことを確認する
    (2026-09-02の設計変更、緯度経度の取り違え防止)。"""
    resolver = StandardSpatialIdResolver()
    center = resolver.get_center("10/3/500/300")
    assert isinstance(center, GeographicPoint)
    assert not isinstance(center, tuple)
    # フィールド名でアクセスできること自体が、位置引数の取り違えを防ぐ
    assert isinstance(center.lon, float) and isinstance(center.lat, float) and isinstance(center.alt, float)
    print("test_get_center_returns_geographic_point_with_lon_first: OK")


if __name__ == "__main__":
    test_get_center_from_parsed_id()
    test_get_center_returns_geographic_point_with_lon_first()
    test_adjacent_indices_move_in_expected_direction()
    test_zoom_level_changes_cell_size()
    test_center_is_inside_bounds()
    test_invalid_id_raises()
    test_round_trip_matches_forward_transform()
    test_matches_published_zoom0_and_zoom16_cell_size_at_equator()
    print()
    print("全テスト成功。")

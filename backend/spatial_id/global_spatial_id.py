"""
backend/spatial_id/global_spatial_id.py

標準空間ID(Global Spatial ID、4dspatio-temporal-id-guideline-v1_2.pdf §2.4)の
解析・変換を担うインターフェース。

【設計方針(ユーザー指示: 2026-08-28、2026-09-02にget_center/get_bounds実装)】
GUIからは Global Spatial ID を文字列としてそのまま入力・保存する。ただし単なる
不透明なラベルとして扱わず、「parse(書式検証・分解)」
「get_center/get_bounds(物理座標への変換)」をこのモジュールのインターフェース
として正式に定義する。Nodal Information側(NodalEndpoint)は global_spatial_id
文字列のみを source of truth として保持し、緯度経度等の物理座標を重複して
保存しない(get_center/get_bounds経由でその都度導出する)。

【get_center()/get_bounds()の実装根拠(ガイドライン§2.4.2)】
標準空間IDの各インデックス算出式(経度・緯度・標高・ズームレベル→f,x,y)は
以下の通り(出典: 4dspatio-temporal-id-guideline-v1_2.pdf §2.4.2、
経度緯度をラジアンで統一した等価式)。

    n = 2^z
    Z = 25 (ボクセルの高さが1mとなるズームレベル、水平zoomとは独立の固定値)
    H = 2^Z [m]
    f = floor(n * h / H)                                  … h: 標高[m]
    x = floor(n * (1/2 + lng_rad/(2π)))                    … lng_rad: 経度[rad]
    y = floor(n * (1/2 - log(tan(lat_rad) + 1/cos(lat_rad))/(2π)))  … lat_rad: 緯度[rad]

get_center()/get_bounds()は、この式の逆演算(floorの逆=区間の代表点・区間端)を
実装したもの。x/f方向の逆演算は単純な線形の逆算だが、y方向は非線形
(Web Mercatorと同型の正規化，"tan(lat)+sec(lat)"の対数、標準的な
Slippy map tilenamesのtile→緯度変換と同じ形)であり、
lat = atan(sinh(π * (1 - 2*y_norm))) というGudermannian関数の形で逆算する
(出典: ガイドライン自身が参照するSlippy map tilenames
https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames の
"Tile numbers to lon./lat." の式と同型。ガイドライン§2.4.2の脚注も
「出典：国連ベクトルタイルツールキット https://github.com/unvt/zfxy-spec」と
明記しており、同種のXYZタイル系列であることを裏付ける)。

【スコープ外・既知の未対応(ダミー値で埋めず、明示的にNotImplementedErrorとする)】
- 極地空間ID(z<0、ガイドライン§2.5.4、横メルカトル図法)は、上記forward式が
  そもそも適用対象外(x/yの算出式が異なる)。今回は実装しない
  (get_center/get_boundsを呼ぶとNotImplementedError)。
- 高さ情報を持たない{z}/{x}/{y}形式(§2.4.3)への対応も、parse()同様に
  未対応のまま(既存のスコープを維持)。

【CRS・座標系に関する確認結果、および既存実装との定義差(2026-09-02調査、
勝手に補正せず報告する)】
1. 水平方向の測地系はガイドライン§2.3.2(2)②により「世界測地系(JGD2024または
   WGS84)」と定義されている。ただし、上記のx/y算出式自体は経度・緯度の
   数値(ラジアン)だけを使う正規化されたXYZタイル分割であり、楕円体パラメータ
   (長半径・扁平率等)を式中で直接参照しない。そのため、どちらの測地系を
   採用するかは「呼び出し側が渡すlng/latの値がどちらの測地系に基づくか」に
   依存し、このモジュールが変換・判定するものではない
   (WGS84とJGD2024は地殻変動によるプレート移動分の差異があるが、
   この差はセンチメートル〜メートル级であり、本モジュールの計算式には
   現れない。メタデータとして別途明示する、というガイドラインの方針を
   踏襲し、本モジュールも測地系の変換は行わない)。
2. 高さの基準はガイドライン§2.3.2(1)①により「標高(ジオイド基準)」であり、
   GNSS等で得られる「楕円体高」とは異なる(標高 = 楕円体高 - ジオイド高)。
   本モジュールのget_center()/get_bounds()が返す第3要素は、あくまで
   f インデックスから逆算した「標高」の値であり、ジオイド補正計算は
   一切行わない(補正済みの標高がforward変換の入力hだった、という前提を
   そのまま引き継ぐだけ)。
3. **(2026-09-02、Ouranos-GEX公式Pythonライブラリとの比較調査で判明・解消
   済み)** 旧Protocol docstringはget_center()の戻り値を
   「(緯度[度], 経度[度], 標高[m])」と定義していたが、公式実装
   (https://github.com/ODS-IS-STID/ouranos-gex-lib-for-Python、
   `src/SpatialId/common/object/point.py`の`Point(lon, lat, alt)`)は
   経度を先に置く順序を採用している。生tupleの位置引数だと呼び出し側が
   順序を取り違えても実行時に検出できないため、`GeographicPoint`という
   フィールド名付きdataclass(lon, lat, altの順、公式ライブラリに合わせる)
   を導入し、戻り値の型自体で意味を明示するようにした。
   また、度(degree)単位の地理座標を、Nodal correspondence /
   RigidTransform2D推定(services/transform_estimation_service.pyの
   fit_rigid_transform_2d)へ直接渡すと、Euclid距離を前提とするKabsch
   フィットが緯度による歪みで正しく機能しない問題があったが、これは
   spatial_id/geographic_projection.py(Phase 3.5b、pyproj経由でEPSG:6677等の
   投影座標(メートル)へ変換する専用モジュール)を新設して解消した
   (degree座標をfit_rigid_transform_2d()へ直接渡さない、という設計)。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol, Tuple

# ボクセルの高さが1mとなるズームレベル(ガイドライン§2.4.2の固定値。
# 水平方向のzoom levelとは独立)。
_VERTICAL_UNIT_ZOOM = 25

# "{z}/{f}/{x}/{y}"形式(極地空間IDの場合はzが負)。
# 高さ情報を持たない"{z}/{x}/{y}"形式(§2.4.3)は今回のスコープでは未対応。
_STANDARD_ID_PATTERN = re.compile(r"^(-?\d+)/(-?\d+)/(-?\d+)/(-?\d+)$")


@dataclass(frozen=True)
class GeographicPoint:
    """地理座標(経度・緯度・標高、度/メートル単位、WGS84想定)。

    フィールド順序はOuranos-GEX公式PythonライブラリのPoint(lon, lat, alt)に
    合わせている(2026-09-02の比較調査より。生のTuple[float,float,float]だと
    呼び出し側が緯度経度の順序を取り違えても検出できないため、名前付き
    フィールドのdataclassにした)。
    """

    lon: float  # 経度[度]
    lat: float  # 緯度[度]
    alt: float  # 標高[m]


@dataclass
class ParsedGlobalId:
    """"{z}/{f}/{x}/{y}" を分解した結果。

    zが負の場合は極地空間ID(ガイドライン§2.5.4)を表す(このモジュールでは
    判別のみ行い、極地空間ID特有の座標変換には未対応)。
    """

    z: int
    f: int
    x: int
    y: int
    is_polar: bool

    @property
    def zoom_level(self) -> int:
        return abs(self.z)


class GlobalSpatialIdResolver(Protocol):
    """標準空間ID(Global Spatial ID)の解析・物理座標変換インターフェース。"""

    def parse(self, global_spatial_id: str) -> ParsedGlobalId:
        """"{z}/{f}/{x}/{y}" 形式の文字列を z,f,x,y に分解する(書式検証のみ)。"""
        ...

    def get_center(self, global_spatial_id: str) -> GeographicPoint:
        """該当ボクセル中心の物理座標(GeographicPoint: 経度[度], 緯度[度],
        標高[m])を返す。4dspatio-temporal-id-guideline-v1_2.pdf §2.4.2 の
        逆変換に相当する。
        """
        ...

    def get_bounds(self, global_spatial_id: str) -> Tuple[GeographicPoint, GeographicPoint]:
        """該当ボクセルの範囲(最小点, 最大点)をGeographicPointの組で返す。"""
        ...


class StandardSpatialIdResolver:
    """GlobalSpatialIdResolverの現時点での実装。

    parse()に加え、get_center()/get_bounds()もガイドライン§2.4.2の逆変換式
    (モジュールdocstring参照)で実装済み(2026-09-02)。極地空間ID(z<0)は
    スコープ外のままNotImplementedErrorを送出する(ダミー座標では埋めない)。
    """

    def parse(self, global_spatial_id: str) -> ParsedGlobalId:
        m = _STANDARD_ID_PATTERN.match(global_spatial_id.strip())
        if not m:
            raise ValueError(
                f"不正な空間ID形式です(期待する形式: '{{z}}/{{f}}/{{x}}/{{y}}'): {global_spatial_id!r}"
            )
        z, f, x, y = (int(v) for v in m.groups())
        return ParsedGlobalId(z=z, f=f, x=x, y=y, is_polar=z < 0)

    def _validate_standard_range(self, parsed: ParsedGlobalId, global_spatial_id: str) -> int:
        """標準空間ID(極地空間IDではない)として意味を持つ範囲かを検証し、
        n(=2^z)を返す。ガイドライン§2.3.2(2)③・(3)②が定義するx/y/fの
        有効範囲(西端x=0〜東端x=2^z-1、北端y=0〜南端y=2^z-1、
        fはプラス/マイナス方向にそれぞれn個)に基づく。"""
        if parsed.is_polar:
            raise NotImplementedError(
                f"極地空間ID(z<0)のget_center()/get_bounds()は未実装です"
                f"(ガイドライン§2.5.4の横メルカトル図法は今回のスコープ外): {global_spatial_id!r}"
            )
        n = 2 ** parsed.z
        if not (0 <= parsed.x < n):
            raise ValueError(
                f"xインデックスがこのzoom levelの有効範囲外です"
                f"(0 <= x < {n} が必要、実際は x={parsed.x}): {global_spatial_id!r}"
            )
        if not (0 <= parsed.y < n):
            raise ValueError(
                f"yインデックスがこのzoom levelの有効範囲外です"
                f"(0 <= y < {n} が必要、実際は y={parsed.y}): {global_spatial_id!r}"
            )
        if not (-n <= parsed.f < n):
            raise ValueError(
                f"fインデックスがこのzoom levelの有効範囲外です"
                f"(-{n} <= f < {n} が必要、実際は f={parsed.f}): {global_spatial_id!r}"
            )
        return n

    def get_center(self, global_spatial_id: str) -> GeographicPoint:
        """該当ボクセル中心の物理座標(GeographicPoint: 経度[度], 緯度[度],
        標高[m])を返す(ガイドライン§2.4.2の逆変換、モジュールdocstring参照)。
        """
        parsed = self.parse(global_spatial_id)
        n = self._validate_standard_range(parsed, global_spatial_id)
        h_vertical = 2 ** _VERTICAL_UNIT_ZOOM

        lng_deg = ((parsed.x + 0.5) / n) * 360.0 - 180.0
        y_norm = (parsed.y + 0.5) / n
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y_norm)))
        lat_deg = math.degrees(lat_rad)
        elevation_m = (parsed.f + 0.5) * h_vertical / n

        return GeographicPoint(lon=lng_deg, lat=lat_deg, alt=elevation_m)

    def get_bounds(self, global_spatial_id: str) -> Tuple[GeographicPoint, GeographicPoint]:
        """該当ボクセルの範囲(最小点, 最大点)をGeographicPointの組で返す
        (ガイドライン§2.4.2の逆変換、区間の両端)。"""
        parsed = self.parse(global_spatial_id)
        n = self._validate_standard_range(parsed, global_spatial_id)
        h_vertical = 2 ** _VERTICAL_UNIT_ZOOM

        lng_min = (parsed.x / n) * 360.0 - 180.0
        lng_max = ((parsed.x + 1) / n) * 360.0 - 180.0

        # yはMercator型の非線形写像で、yが増える(南へ行く)ほど緯度は下がる
        # ため、区間の下端(y+1)が最小緯度、上端(y)が最大緯度になる。
        lat_at_north_edge = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (parsed.y / n)))))
        lat_at_south_edge = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ((parsed.y + 1) / n)))))
        lat_min, lat_max = lat_at_south_edge, lat_at_north_edge

        elevation_min = parsed.f * h_vertical / n
        elevation_max = (parsed.f + 1) * h_vertical / n

        return (
            GeographicPoint(lon=lng_min, lat=lat_min, alt=elevation_min),
            GeographicPoint(lon=lng_max, lat=lat_max, alt=elevation_max),
        )

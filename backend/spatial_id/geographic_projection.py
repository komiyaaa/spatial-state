"""
backend/spatial_id/geographic_projection.py

GeographicPoint(経度・緯度・標高、度単位)を、pyprojを使って投影座標系
(メートル単位)のProjectedPointへ変換する(ロードマップPhase 3.5b)。

【責務分離(ユーザー指示: 2026-09-02)】
- Global Spatial ID文字列→GeographicPointの変換(ガイドライン§2.4.2の数式、
  pyproj非依存)はspatial_id.global_spatial_id.StandardSpatialIdResolverの
  責務のまま(このモジュールでは変更しない)。
- このモジュールはその後段、GeographicPoint→ProjectedPoint(度→メートル)の
  変換だけを担当する。
- services.transform_estimation_service.fit_rigid_transform_2d()は無変更
  (座標の由来・単位を一切関知しない、既存の設計方針を維持する)。度単位の
  座標をfit_rigid_transform_2d()へ直接渡さない、というパイプライン全体の
  方針を、この変換層を挟むことで実現する。

【CRS選定(2026-09-02、Ouranos-GEX公式ライブラリとの比較調査 D-2で確定)】
既定のtarget CRSはEPSG:6677(JGD2011 / Japan Plane Rectangular CS IX、
日本の平面直角座標系第9系、投影方式: Transverse Mercator、単位: メートル)。
epsg.ioで直接確認した適用範囲は「Japan - onshore - Honshu - Tokyo-to.
(小笠原諸島等の外洋離島を除く)」、原点は北緯36度・東経139度50分。
本プロジェクトの現在の実験地域(ichigaya_tamachi-G002、東京都新宿区
市谷田町)はこの範囲内かつ原点から数kmしか離れておらず、投影歪みは
無視できる水準。Web Mercator(EPSG:3857)は緯度による水平縮尺の歪みが
大きいため不採用とした。target_epsgは常に外部指定可能な引数とし、
決め打ちにしない(実験地域が東京都本州部以外に拡張された場合は、該当地域の
平面直角座標系(系I〜XIX、EPSG:6669〜6687)に差し替える)。

【pyprojのバージョンについて】
公式Ouranos-GEXライブラリ(https://github.com/ODS-IS-STID/ouranos-gex-lib-for-Python)
が指定するpyproj 3.6.1はPython >=3.9必須であり、本プロジェクトの実行環境
(Python 3.8.10、pygicpのcp38専用バイナリに拘束されている)では使えない。
pyproj本体の変更履歴(docs/history.rst)を直接確認した結果、3.6.0で
「DEP: Minimum supported Python version 3.9」と明記されており、3.5.0が
Python 3.8をサポートする最後のリリースであることを確認した
(requires_python ">=3.8"、Windows cp38 wheel配布あり)。2026-09-02に
実際にpyproj==3.5.0をインストールし、EPSG:4326→EPSG:6677のTransformerが
正しく動作する(往復変換が実用上完全に一致する)ことを確認済み。
手書きのTransverse Mercator実装はしない(車輪の再発明を避ける、ユーザー指示)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pyproj

from spatial_id.global_spatial_id import GeographicPoint

# 東京(市谷田町周辺)を現在の実験地域とする、metric CRSの既定値
# (JGD2011 / Japan Plane Rectangular CS IX)。他地域への拡張時はtarget_epsgを
# 明示的に差し替えること(このモジュールはEPSG:6677をハードコードした
# ロジックにはしていない)。
DEFAULT_TARGET_EPSG = 6677

# GeographicPointは常にWGS84(EPSG:4326)の度単位という前提
# (StandardSpatialIdResolver.get_center/get_boundsの契約、モジュールdocstring参照)。
_SOURCE_EPSG = 4326

_transformer_cache: Dict[int, "pyproj.Transformer"] = {}


@dataclass(frozen=True)
class ProjectedPoint:
    """投影座標(メートル単位)。Kabschフィット(services.transform_estimation_
    service.fit_rigid_transform_2d)にそのまま渡せる、Euclid距離が意味を持つ
    平面直交座標。

    epsgフィールドを持つのは、この値オブジェクト内だけで使って捨てるのでは
    なく、呼び出し側(domain.global_resolution.AnchorEstimate /
    ComponentGlobalResolution)にも「どの投影を使って得られたmetric座標か」を
    残すため(ユーザー指示: 2026-09-02)。
    """

    x: float  # 投影X[m]
    y: float  # 投影Y[m]
    alt: float  # 標高[m](水平投影の対象外、そのまま引き継ぐ)
    epsg: int  # 使用した投影CRSのEPSGコード


def _get_transformer(target_epsg: int) -> "pyproj.Transformer":
    if target_epsg not in _transformer_cache:
        _transformer_cache[target_epsg] = pyproj.Transformer.from_crs(
            f"EPSG:{_SOURCE_EPSG}", f"EPSG:{target_epsg}", always_xy=True
        )
    return _transformer_cache[target_epsg]


def to_projected(point: GeographicPoint, target_epsg: int = DEFAULT_TARGET_EPSG) -> ProjectedPoint:
    """GeographicPoint(WGS84度単位)を、target_epsgの投影座標(メートル)へ変換する。

    always_xy=Trueで構築したpyproj.Transformerを使い、(経度, 緯度)の順で
    transform()に渡す(公式Ouranos-GEXライブラリの
    transformer.transform(lon, lat, alt, ...)呼び出しと同じ軸順序、
    2026-09-02の比較調査で確認済み)。標高(alt)は水平投影の対象外として
    そのまま引き継ぐ。
    """
    transformer = _get_transformer(target_epsg)
    x, y = transformer.transform(point.lon, point.lat)
    return ProjectedPoint(x=x, y=y, alt=point.alt, epsg=target_epsg)

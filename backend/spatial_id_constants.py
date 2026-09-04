"""
backend/spatial_id_constants.py

Local Spatial ID生成における、XY方向・Z方向で共通の最小voxel size定数。

【単一の定義箇所にする理由(ユーザー指示: 2026-08-29)】
Local Spatial IDのfinest level(最小voxel size)は、Spatial State更新・
Structural Labelのsource of truthとして、XY・Zとも3cm(0.03m)に統一する
方針。この値を
- backend/space_definition_generator.py(XY方向unit-size系列の起点)
- backend/point_to_spatial_id.py(Z方向unit-size系列の起点)
の双方が別々にリテラル値として持つと、片方だけ変更されて食い違う
(実際に2026-08-29、Z側だけ0.1mのまま残っていた不具合が発生した)。
これを防ぐため、値そのものをこの依存の無い(numpyにも依存しない)モジュールに
集約し、両モジュールがここから参照する。
"""
MIN_VOXEL_SIZE = 0.03

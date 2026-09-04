"""
backend/repositories/spatial_voxel_cache_repository.py

SpatialVoxel(finest、Base Map点群のfinest Local Spatial ID集約結果、
ロードマップStep 1)・AggregatedSpatialVoxel(finestより粗いzoom levelへの
derived aggregation、ロードマップStep 3)を、Viewer向けの軽量derived
cacheとして永続化する。

【重要】これはsource of truthの複製ではない。source of truthはあくまで
Base Map点群+CoordinateDefinitionであり、finestは
point_cloud_voxelization.voxelize_base_map_points()で、finestより粗い
levelはspatial_voxel_aggregation.aggregate_finest_positions_to_zoom_level()
(finestのこのキャッシュを入力にする)でいつでも再計算できる。ここに保存
するのは、その再計算コスト(実測: G002finest、約123万点で約30〜40秒)を
毎リクエスト払わずに済むようにする、Visualization専用のキャッシュ。

【zoom levelごとに別ファイル(ユーザー指示: 2026-08-30)】
同一space_idでも、表示level切り替えのため複数のzoom levelのキャッシュが
同時に存在しうる。1 (space_id, zoom_level) の組 = 3ファイル:
- {space_id}__z{zoom_level}.meta.json:
  {space_id, zoom_level, voxel_size, voxel_count, order_fingerprint,
  generated_at}
  (voxel_sizeはそのzoom levelで全voxel共通の1つの値なので、voxelごとに
  繰り返さない)
- {space_id}__z{zoom_level}.positions.bin:
  各voxelのvoxel_center(x,y,z)をFloat32・little-endianで連結しただけの
  バイナリ(voxel_count*3個のfloat32)。local_spatial_id昇順にソート済み
  (再現性のため)。
- {space_id}__z{zoom_level}.ids.bin:
  各voxelのLocal Spatial ID("z/f/x/y")のうち、zoom部分はこのキャッシュの
  キー自体が示すため冗長として省き、(f, x, y)のみをInt32・little-endianで
  連結したバイナリ(voxel_count*3個のint32)。positions.binと厳密に同じ
  instance順序(local_spatial_id昇順)で、同じsave()呼び出し内の同じ
  `ordered`配列から並列に書き出す(ユーザー指示: 2026-08-31。
  「座標からIDを復元して色をjoinする」という往復依存を無くすため、
  Local Spatial IDそのものを、巨大な文字列JSONではなく、この
  compact整数バイナリとして永続化する)。

【order_fingerprint】
ids.binのバイト列そのもののsha256 hexdigest。ある(space_id, zoom_level)の
positions.bin/ids.binが「同じ1回のsave()呼び出しから生成された、同一の
instance順序を持つペアであること」を、他のderived cache
(voxel_color_cache_repository)が値を再解釈せずに突き合わせ確認できるように
するための識別子(数値の再計算をせずに順序一致を検証する手段)。

巨大JSON(voxel1件ごとに数百バイトのdictを、数十万〜百万件並べる)を返さない
ため、実データ配列は必ずこのバイナリ形式で提供する。JSONで返すのはmetaのみ。

save()は、SpatialVoxel(Step 1)・AggregatedSpatialVoxel(Step 3)のどちらも
受け付ける(voxel_center/voxel_size/zoom_level/local_spatial_id属性を
持っていればよい、ダックタイピング)。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import List, Optional

import numpy as np

from local_spatial_id_hierarchy import parse_local_spatial_id


class SpatialVoxelCacheRepository:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, space_id: str, zoom_level: int) -> str:
        return f"{space_id}__z{zoom_level}"

    def _meta_path(self, space_id: str, zoom_level: int) -> Path:
        return self.base_dir / f"{self._key(space_id, zoom_level)}.meta.json"

    def positions_path(self, space_id: str, zoom_level: int) -> Path:
        return self.base_dir / f"{self._key(space_id, zoom_level)}.positions.bin"

    def ids_path(self, space_id: str, zoom_level: int) -> Path:
        return self.base_dir / f"{self._key(space_id, zoom_level)}.ids.bin"

    def load_meta(self, space_id: str, zoom_level: int) -> Optional[dict]:
        """キャッシュが存在すればmetaを返す(3ファイルすべて揃っている場合のみ有効とみなす)。"""
        meta_path = self._meta_path(space_id, zoom_level)
        if (
            not meta_path.exists()
            or not self.positions_path(space_id, zoom_level).exists()
            or not self.ids_path(space_id, zoom_level).exists()
        ):
            return None
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def load_positions(self, space_id: str, zoom_level: int) -> np.ndarray:
        """キャッシュ済みのvoxel_center配列((N,3), float32)を返す。無ければ例外。"""
        path = self.positions_path(space_id, zoom_level)
        if not path.exists():
            raise ValueError(f"space_id '{space_id}' zoom_level {zoom_level} のキャッシュがありません。")
        return np.frombuffer(path.read_bytes(), dtype=np.float32).reshape(-1, 3)

    def load_local_spatial_ids(self, space_id: str, zoom_level: int) -> List[str]:
        """positions.binと厳密に同じinstance順序のLocal Spatial ID一覧を返す。

        ids.bin(compact int32の(f,x,y)triplet)をそのまま文字列化するだけで、
        voxel_center等の物理座標を一切経由しない(座標→ID変換の再実行をしない)。
        """
        path = self.ids_path(space_id, zoom_level)
        if not path.exists():
            raise ValueError(f"space_id '{space_id}' zoom_level {zoom_level} のIDキャッシュがありません。")
        arr = np.frombuffer(path.read_bytes(), dtype=np.int32).reshape(-1, 3)
        return [f"{zoom_level}/{f}/{x}/{y}" for f, x, y in arr.tolist()]

    def load_order_fingerprint(self, space_id: str, zoom_level: int) -> Optional[str]:
        """このキャッシュのinstance順序を示すfingerprint(sha256 hexdigest)。
        無ければNone。"""
        meta = self.load_meta(space_id, zoom_level)
        return meta.get("order_fingerprint") if meta else None

    def save(self, space_id: str, voxels: List) -> dict:
        """voxel一覧(SpatialVoxel または AggregatedSpatialVoxel)をバイナリ+meta
        として保存し、metaを返す(既存キャッシュは上書き)。zoom_levelは
        voxels[0].zoom_levelから取得し、キャッシュのファイル名(キー)に使う。

        positions.bin・ids.binは、同じ`ordered`配列(local_spatial_id昇順)から
        並列に生成する。「座標からIDを復元してjoinする」という往復依存を
        避けるため、instance順序の一次情報(source of ordering)は常にこの
        ordered配列自身であり、voxel_centerではない(ユーザー指示: 2026-08-31)。
        """
        if not voxels:
            raise ValueError("voxelsが空です(zoom_levelを特定できません)。")
        zoom_level = voxels[0].zoom_level
        ordered = sorted(voxels, key=lambda v: v.local_spatial_id)

        positions = np.zeros((len(ordered), 3), dtype=np.float32)
        ids = np.zeros((len(ordered), 3), dtype=np.int32)
        for i, v in enumerate(ordered):
            positions[i] = v.voxel_center
            _, f, x, y = parse_local_spatial_id(v.local_spatial_id)
            ids[i] = (f, x, y)
        self.positions_path(space_id, zoom_level).write_bytes(positions.tobytes())
        ids_bytes = ids.tobytes()
        self.ids_path(space_id, zoom_level).write_bytes(ids_bytes)

        meta = {
            "space_id": space_id,
            "zoom_level": zoom_level,
            "voxel_size": ordered[0].voxel_size,
            "voxel_count": len(ordered),
            "order_fingerprint": hashlib.sha256(ids_bytes).hexdigest(),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._meta_path(space_id, zoom_level).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return meta

    def invalidate(self, space_id: str, zoom_level: Optional[int] = None) -> None:
        """Base Map・CoordinateDefinitionが変わった場合等、キャッシュを破棄する。
        zoom_level省略時は、このspace_idの全zoom levelのキャッシュを破棄する。"""
        if zoom_level is not None:
            self._meta_path(space_id, zoom_level).unlink(missing_ok=True)
            self.positions_path(space_id, zoom_level).unlink(missing_ok=True)
            self.ids_path(space_id, zoom_level).unlink(missing_ok=True)
            return
        prefix = f"{space_id}__z"
        for path in self.base_dir.glob(f"{prefix}*"):
            path.unlink(missing_ok=True)

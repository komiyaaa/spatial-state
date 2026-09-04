"""
backend/repositories/voxel_color_cache_repository.py

voxel_color_strategy.build_color_codes_for_mode()の結果(color_codes +
legend)を、Viewer向けの軽量derived cacheとして永続化する(ロードマップ
Step 4)。

【重要】これはsource of truthの複製ではない。source of truthはあくまで
Structural Labelのsource data(SpatialVoxelLabelRepository)・Base Map・
CoordinateDefinitionであり、color_codeはいつでも
voxel_color_strategy.build_color_codes_for_mode()で再計算できる。ここに
保存するのは、その再計算コスト(STRUCTURAL_LABELモードでは、finest
labelの全件走査が必要)を毎リクエスト払わずに済むようにする、
Visualization専用のキャッシュ。upper level labelそのものを永続化する
ものではない(集約結果のcolor_codeだけをキャッシュする)。

保存形式(1 (space_id, zoom_level, mode) の組 = 2ファイル):
- {space_id}__z{zoom_level}__{mode}.meta.json:
  {space_id, zoom_level, mode, voxel_count, legend,
  position_order_fingerprint, generated_at}
- {space_id}__z{zoom_level}__{mode}.codes.bin:
  color_codeをuint8で連結しただけのバイナリ(voxel_count個)。
  spatial_voxel_cache_repositoryのpositions.bin/ids.binと同じinstance順序
  (local_spatial_id昇順)に対応する。

【position_order_fingerprint(ユーザー指示: 2026-08-31)】
このcolor codeを計算した時点で使用した
SpatialVoxelCacheRepository.load_order_fingerprint(space_id, zoom_level)の
値をそのまま複製したもの。座標やIDを再計算せずに、「このcolors.binは、
positions.binと同じinstance順序(同じids.bin)から生成されたものである」
ことを、値の突き合わせだけで検証できるようにするための識別子。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import numpy as np


class VoxelColorCacheRepository:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, space_id: str, zoom_level: int, mode: str) -> str:
        return f"{space_id}__z{zoom_level}__{mode}"

    def _meta_path(self, space_id: str, zoom_level: int, mode: str) -> Path:
        return self.base_dir / f"{self._key(space_id, zoom_level, mode)}.meta.json"

    def codes_path(self, space_id: str, zoom_level: int, mode: str) -> Path:
        return self.base_dir / f"{self._key(space_id, zoom_level, mode)}.codes.bin"

    def load_meta(self, space_id: str, zoom_level: int, mode: str) -> Optional[dict]:
        meta_path = self._meta_path(space_id, zoom_level, mode)
        if not meta_path.exists() or not self.codes_path(space_id, zoom_level, mode).exists():
            return None
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def load_codes(self, space_id: str, zoom_level: int, mode: str) -> np.ndarray:
        path = self.codes_path(space_id, zoom_level, mode)
        if not path.exists():
            raise ValueError(f"space_id '{space_id}' zoom_level {zoom_level} mode {mode} のキャッシュがありません。")
        return np.frombuffer(path.read_bytes(), dtype=np.uint8)

    def save(
        self,
        space_id: str,
        zoom_level: int,
        mode: str,
        codes: np.ndarray,
        legend: dict,
        position_order_fingerprint: Optional[str] = None,
    ) -> dict:
        codes = np.asarray(codes, dtype=np.uint8)
        self.codes_path(space_id, zoom_level, mode).write_bytes(codes.tobytes())

        meta = {
            "space_id": space_id,
            "zoom_level": zoom_level,
            "mode": mode,
            "voxel_count": int(codes.shape[0]),
            "legend": legend,
            "position_order_fingerprint": position_order_fingerprint,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._meta_path(space_id, zoom_level, mode).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return meta

    def invalidate(self, space_id: str, zoom_level: Optional[int] = None, mode: Optional[str] = None) -> None:
        """Structural Labelのsource dataが更新された場合等、キャッシュを破棄する。
        zoom_level/mode省略時は、このspace_idの全キャッシュを破棄する。"""
        if zoom_level is not None and mode is not None:
            self._meta_path(space_id, zoom_level, mode).unlink(missing_ok=True)
            self.codes_path(space_id, zoom_level, mode).unlink(missing_ok=True)
            return
        prefix = f"{space_id}__z"
        for path in self.base_dir.glob(f"{prefix}*"):
            path.unlink(missing_ok=True)

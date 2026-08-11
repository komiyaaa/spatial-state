# 空間IDベース永続状態モデル 設計メモ(2026-08-06時点)

## 1. スキーマ(確定分)

### SPACE_DEFINITION(建物・空間ごとに1行)
Local Spatial IDの座標定義情報そのもの。

| フィールド | 内容 |
|---|---|
| space_id (PK) | |
| origin_lat, origin_lng | 原点の緯度・経度 |
| rotation_angle | Z軸まわりの回転角 |
| extent_L, extent_H | 水平・垂直方向の一辺の長さ |
| zoom_level | 基準ズームレベル |

### STRUCTURAL_LABEL_POLICY(ラベルごとに1行、少数)
ベースマップ由来の構造ラベルが、事前分布としてどれだけ強く効くかを決める。

| フィールド | 内容 |
|---|---|
| label (PK) | "WALL" / "FLOOR" / "CEILING" / null(ラベル無し=家具等) |
| prior_log_odds | このラベルの初期ログオッズ(占有寄りなら正) |
| prior_pseudo_count | この事前分布が「実測何回分」に相当するかの仮想カウント |
| kappa_th_override | (任意)このラベルだけ確信度閾値を変える場合 |

**目安の値**(ベースマップの作り方に基づく、シンプルな一律値でよい):
- 床面: `prior_pseudo_count`大(例: 100) — 実形状をトレースしているため最も正確
- 壁面: 中程度(例: 15〜20) — 大まかな位置は正しいが、梁・長押等の凹凸は未反映
- 天井面: 小さめ(例: 5〜10) — 天井高からの単純な平面仮定で、照明・配管等が未反映
- ラベル無し(家具等): ほぼゼロ — 事前知識なし、実測をすぐ信じてよい
- 平面からの距離による減衰は**今回は入れない**(シンプルさ優先の判断)

### SPATIAL_VOXEL(空間IDごとに1行、主テーブル)

| フィールド | 内容 |
|---|---|
| spatial_id (PK) | `{z}/{f}/{x}/{y}` |
| space_id (FK) | |
| z, f, x, y | 分解済みのインデックス(範囲検索用) |
| structural_label (FK, nullable) | |
| state | NEVER_OBSERVED / SCAN_COVERED / OCCUPIED / DECAYED |
| log_odds | 累積ログオッズ |
| observation_count | このIDに実際に触れた回数(素のカウント) |
| last_observed_at | 最終観測時刻 |
| last_state_change_at | 状態が最後に切り替わった時刻 |
| transition_count | 状態切り替わりの累積回数 |
| mobility_flag | STATIC / DYNAMIC / PENDING |
| created_at | このIDが初めて現れた時刻 |

### SCAN_SESSION(スキャン1回=1行)
位置合わせは別手法で完了済みという前提なので、変換情報は持たない。

| フィールド | 内容 |
|---|---|
| session_id (PK) | |
| space_id (FK) | |
| device_id | |
| started_at | |

### OBSERVATION(監査ログ、任意・後で間引き可)

| フィールド | 内容 |
|---|---|
| observation_id (PK) | |
| spatial_id (FK) | |
| session_id (FK) | |
| observed_at | |
| hit_count | そのスキャンでの点数 |
| is_free_t | 「見たが無かった」証拠として扱われたか |

---

## 2. 事前分布と実測の混ざり方(考え方)

```
ell(v) の初期値 = prior_log_odds(structural_label)  ※ラベル無しなら0
その後は、既存の core/log_odds.py と同じ加算式で実測を積み上げる

observation_count が prior_pseudo_count 未満のうちは:
  → 状態遷移(特にOCCUPIED以外への変化)を許可しない、または慎重にする
  → mobility_flag は PENDING のまま

observation_count が prior_pseudo_count を超えたら:
  → 実測(ell の値)だけで通常通り判定してよい
```

`prior_pseudo_count`が有限である限り、どんなに事前分布を信頼していても、
実測を積み重ねれば必ずいつかは上書きされる(絶対視しない設計)。

## 3. 更新アルゴリズムの流れ(叩き台、Step4の具体式は未確定)

```
1回のスキャンセッションが来たら:

Step 1: ヒットした空間ID集合を求める(点群から通常通り計算)

Step 2: 「今回のスキャンが実際にカバーしていた範囲」を推定する
  → ヒットした空間IDの近傍(半径r)を「カバー済み」とみなす
  → (Free_tの近傍密度チェックを、占有判定ではなく
     「今回スキャンされたかどうか」の判定に転用するアイデア。未検証)

Step 3: 「カバーされたが非ヒット」の集合を求める(Step2 − Step1)
  → これが消失(負の証拠)の対象

Step 4: Step1・Step3それぞれの空間IDの行を更新する
  (無ければ新規作成、ラベルがあれば prior を適用)
  → log_odds, observation_count, mobility_flag, last_observed_at 等
  ※ 具体的な更新式・mobility_flagの遷移条件(STATIC/DYNAMICの閾値)は未確定

Step 5: Step1・Step3どちらにも含まれない空間ID → 一切触らない(自動的に保留)
```

## 4. まだ決まっていないこと(実装時に判断が必要)

1. Step2の「カバー範囲」の半径rの決め方
2. mobility_flagがPENDINGを抜ける具体的な観測回数の閾値、STATIC/DYNAMICの判定窓
3. Step4のログオッズ更新式の詳細(既存`core/log_odds.py`の加算式をそのまま使えるはずだが、prior_log_oddsを初期値にする形へ変更が必要)
4. κ(確信度)は、既存の`core/confidence.py`の`compute_kappa_selective`をほぼそのまま転用できる見込み(cons/dens/agrの計算自体は空間IDテーブルでも同じ考え方で使えるはず)

---

## 5. ズームレベル対応表: 水平・鉛直で別系列にする(2026-08-10決定)

実際のローカル空間定義JSON(`G002v3.json`等)を確認したところ、`height`フィールドの
意味が未確定であることが判明した。あわせて、「天井高に合わせるとボクセルが
つぶれる」「大きい立方体にすると低ズームでの表現が不自然になる」という
トレードオフが議論になった。

**結論: 水平方向(`L`)と鉛直方向(`H`)は、別々の起点・別々の等比数列を持つ
(ボクセルは立方体である必要はなく、縦長の直方体でよい)。** Local Spatial ID
仕様がもともとL・Hを独立したパラメータとして定義しているため、これは仕様に
沿った解決策である。

```
水平方向(既存、L0=51.2m): 51.2, 25.6, 12.8, 6.4, 3.2, 1.6, 0.8, 0.4, 0.2, 0.1 (m)
鉛直方向(新規、H0=3.2m。教室の天井高2.5m前後を想定した例):
                          3.2, 1.6, 0.8, 0.4, 0.2, 0.1, 0.05, 0.025, ... (m)
```

**アスペクト比(L0/H0)は、ズームレベルによらず常に一定になる**(今回の例では16倍)。
「ボクセルの縦横比は変わらず、全体が均等に縮小・拡大していく」という、
素直な性質を持つ。

**重要: `L0`・`H0`は、建物・空間ごとに可変**(Local Spatial IDの強みそのもの)。
`COORDINATE_DEFINITION`の`extent_L`・`extent_H`に、空間ごとの実際の値を
持たせる(全空間で共通の固定値にはしない)。学校の教室(天井高2.5m程度)
であれば`H0=3.2m`程度が目安だが、天井高が違う空間では別の値になる。

`ZOOM_VOXEL_SIZE`テーブルの`voxel_size_xy`・`voxel_size_z`は、この2系列を
別々に計算して埋める(`voxel_size_xy = extent_L / 2^zoom_level`、
`voxel_size_z = extent_H / 2^zoom_level`)。

---

このメモに無い判断が必要になったら、そのとき聞いてください。

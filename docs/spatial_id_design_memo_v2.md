# 空間IDベース永続状態モデル 設計メモ(2026-08-25更新、v4定式化を反映)

## 0. 2026-08-06版からの主な変更点

本メモは、`docs/stage1_formalization_v4.md`(段階1本体の完全定式化)の
確定に伴い、データモデルを全面的に更新したもの。旧版(2026-08-06時点)
との対応は以下の通り。

| 旧スキーマ | 新スキーマ | 変更理由 |
|---|---|---|
| `log_odds`(単一値) | `alpha, beta`(2値) | ベータ分布に統一。$P_{occ},\kappa$が同じ2数から導出される(v3) |
| `mobility_flag`(STATIC/DYNAMIC/PENDINGの列挙型) | `alpha_mu, beta_mu`(2値) | $\mu(v)$専用の投票箱に統一(v4 §2.2.1)。連続値$\mu\in[0,1]$になった |
| `transition_count`(単純カウント、忘却なし) | 廃止(`alpha_mu,beta_mu`の更新に統合) | 忘却が無く、誤判定が永久に傷として残る不具合があった(実験10) |
| (無し) | `miss_streak` | 連続ミスのゲート(§2.3.1)。1回だけの測定ミスを無視するために必要 |
| (無し) | `ever_evidenced`, `was_ever_occupied_candidate` | 状態確定ロジックの正確な判定に必要(実装時に発見) |
| `state`のみ | `state` + `confidence_flag` | 「何を推定しているか」と「どれだけ確定しているか」を分離(CONFIRMED/PENDING) |
| `STRUCTURAL_LABEL_POLICY`のみ | `+ LABEL_FITNESS_HISTORY`(新設) | $w_{fit}$(§2.7)、パッチ品質の相場比較に必要 |
| (無し) | `SCAN_SESSION.patch_fitness_score` | VGICPの`fitness_score`を、$w_{fit}$計算のために保存する |

---

## 1. スキーマ(v4対応版)

### SPACE_DEFINITION(建物・空間ごとに1行、変更なし)
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
**フィールド名を、v4の記法($\alpha_0,\beta_0$、§5.4)に合わせて更新。**

| フィールド | 内容 |
|---|---|
| label (PK) | "WALL" / "FLOOR" / "CEILING" / null(ラベル無し=家具等) |
| prior_alpha0, prior_beta0 | このラベルの初期$(\alpha_0,\beta_0)$(旧`prior_log_odds`から変更。占有寄りなら$\alpha_0>\beta_0$) |
| prior_pseudo_count | この事前分布が「実測何回分」に相当するかの仮想カウント(=$\alpha_0+\beta_0$) |
| kappa_th_override | (任意)このラベルだけ確信度閾値を変える場合 |

**目安の値**(旧版から変更なし、考え方はそのまま):
- 床面: `prior_pseudo_count`大(例: 100) — 実形状をトレースしているため最も正確
- 壁面: 中程度(例: 15〜20) — 大まかな位置は正しいが、梁・長押等の凹凸は未反映
- 天井面: 小さめ(例: 5〜10) — 天井高からの単純な平面仮定で、照明・配管等が未反映
- ラベル無し(家具等): ほぼゼロ — 事前知識なし、実測をすぐ信じてよい

### LABEL_FITNESS_HISTORY(新設、ラベル×パッチごとに1行)
$w_{fit}$(§2.7)の計算に必要な、「同じラベルの過去の`fitness_score`」を
蓄積するテーブル。ラベルごとの中央値$\tilde{f}$は、このテーブルから
その都度計算する(集計クエリ、または定期バッチで`STRUCTURAL_LABEL_POLICY`に
キャッシュしてもよい)。

| フィールド | 内容 |
|---|---|
| history_id (PK) | |
| label (FK) | |
| session_id (FK) | どのスキャンセッション由来の`fitness_score`か |
| fitness_score | VGICPの適合度(値が小さいほど良い) |
| recorded_at | |

**このラベルの蓄積件数が`N_fit_min`未満のうちは、$w_{fit}=1$(割り引きなし)
として扱う(§2.7)。**

### SPATIAL_VOXEL(空間IDごとに1行、主テーブル。v4で最も変更が大きい)

| フィールド | 内容 |
|---|---|
| spatial_id (PK) | `{z}/{f}/{x}/{y}` |
| space_id (FK) | |
| z, f, x, y | 分解済みのインデックス(範囲検索用) |
| structural_label (FK, nullable) | |
| **state** | NEVER_OBSERVED / SCAN_COVERED / OCCUPIED / DECAYED |
| **confidence_flag**(新設) | CONFIRMED / PENDING(旧版はstateに未分離だった) |
| **alpha, beta**(旧`log_odds`から変更) | 占有判定用の投票箱。$P_{occ}=\alpha/(\alpha+\beta)$、$\kappa=(\alpha+\beta)/(\alpha+\beta+n_0)$ |
| **alpha_mu, beta_mu**(新設、旧`mobility_flag`を置き換え) | 動的度合い専用の投票箱(§2.2.1)。$\mu=\kappa_\mu\cdot\frac{\alpha_\mu}{\alpha_\mu+\beta_\mu}+(1-\kappa_\mu)\cdot0.5$ |
| **miss_streak**(新設) | 連続で支持が無い回数(§2.3.1、$M_{min}$ゲートに使用) |
| **ever_evidenced**(新設) | このボクセルが一度でも証拠を持ったか(真偽値) |
| **was_ever_occupied_candidate**(新設) | $P_{occ}$が一度でも閾値を超えたか(DECAYED/SCAN_COVERED判定に必要) |
| observation_count(`n_obs`) | このIDに実際に触れた回数(素のカウント、変更なし) |
| last_observed_at | 最終観測時刻(変更なし) |
| last_state_change_at | 状態が最後に切り替わった時刻(変更なし) |
| ~~transition_count~~ | **廃止**。`alpha_mu, beta_mu`の更新履歴に統合された(遷移の有無は、更新の都度$e^+_\mu,e^-_\mu$として反映され、個別のカウンタを持つ必要がなくなった) |
| created_at | このIDが初めて現れた時刻(変更なし) |

### SCAN_SESSION(スキャン1回=1行)

| フィールド | 内容 |
|---|---|
| session_id (PK) | |
| space_id (FK) | |
| device_id | |
| started_at | |
| **patch_fitness_score**(新設) | VGICPの`fitness_score`。$w_{fit}$計算に必須(§2.7.1、最初に品質チェックを適用するため) |

### OBSERVATION(監査ログ、任意・後で間引き可)

| フィールド | 内容 |
|---|---|
| observation_id (PK) | |
| spatial_id (FK) | |
| session_id (FK) | |
| observed_at | |
| hit_count(`c_t`) | そのスキャンでの生の点数 |
| **c_eff**(新設) | $w_{fit}$適用後の実効点数(§2.7.1、$c_t^{eff}=w_{fit}\cdot c_t$) |
| is_free_t | 「見たが無かった」証拠として扱われたか(変更なし) |

---

## 2. 事前分布と実測の混ざり方(v4で確定)

```
alpha(v), beta(v) の初期値 = prior_alpha0, prior_beta0(structural_label)
                              ※ラベル無しなら、中立に近い小さな値
その後は、stage1_formalization_v4.md §2.4 の更新式で実測を積み上げる:
  alpha_t = gamma・alpha_{t-1} + e^+
  beta_t  = gamma・beta_{t-1}  + e^-

alpha_mu(v), beta_mu(v) の初期値 = 中立(0.1, 0.1程度)
  こちらは構造ラベルの事前分布を持たない(動的/静的は、ラベルからは
  決まらないため)。§2.2.1の更新式で、遷移の有無を積み上げる。

n_t(v) = alpha_t(v) + beta_t(v) が育つにつれて:
  kappa(v) = n_t(v) / (n_t(v) + n_0) が上がっていく
  kappa >= kappa_th になって初めて、state が CONFIRMED になる
  (旧版の「observation_countがprior_pseudo_count未満はPENDING」という
  硬い閾値は、v4では全てkappa系の滑らかな信頼度に統一された)
```

**旧版からの重要な変更**: 「観測回数が事前分布の仮想カウントを超えたら
実測だけで判定する」という硬い切り替えは無くなった。$\gamma$による
忘却があるため、$\alpha,\beta$は事前分布の影響を受けつつ、実測が
積み重なるほど自然に実測優位になっていく(連続的な移行)。

## 3. 更新アルゴリズムの流れ(v4で確定、stage1_formalization_v4.md §4と対応)

```
1回のスキャンセッションが来たら、観測対象の各空間IDについて:

Step 1: c_t(v)(生の点数)を観測する
Step 2: SCAN_SESSION.patch_fitness_score から、w_fit(v,t) を計算する(§2.7)
        (同じstructural_labelのLABEL_FITNESS_HISTORYと比較する。
         蓄積件数がN_fit_min未満ならw_fit=1)
Step 3: c_t^eff(v) = w_fit(v,t)・c_t(v) を計算する(§2.7.1、品質チェックを最初に適用)
Step 4: s1(v,t), s3(v,t) を計算する(柱1・柱3、§2.1。ピーク判定には
        c_t^effの生値を使う。面隣接6方向の近傍を使うことを推奨、
        既存のFree_t近傍密度チェックの仕組みを、covered判定に転用する)
Step 5: covered(v,t) を判定する(近傍のヒット密度から、今回のスキャンが
        実際にカバーしていたかを推定。旧版のStep2と同じ考え方)
Step 6: mu(v) を計算する(柱2、alpha_mu・beta_muから導出、§2.2.1)
Step 7: miss_streak(v,t) を更新する(§2.3.1、連続で支持が無い回数)
Step 8: e^+(v,t), e^-(v,t) を計算する(§2.3。e^-はcoveredかつ
        miss_streak >= M_min の時だけ発生)
Step 9: alpha(v), beta(v) を更新する(§2.4)
Step 10: P_occ, kappa を計算する
Step 11: kappaとkappa_thを比較し、(state, confidence_flag) を確定する(§3)
Step 12: 確定状態(OCCUPIED/DECAYED)が切り替わった場合、
         alpha_mu(v), beta_mu(v) を更新する(§2.2.1、双方向カウント)
Step 13: 上記いずれの対象にもならない空間ID → 一切触らない(自動的に保留)
```

**旧版のStep1〜5との対応**: 旧版の「Step1(ヒット集合)・Step2(カバー範囲
推定)・Step3(カバーされたが非ヒット)・Step4(該当行の更新)・Step5(対象外は
放置)」という骨格は、そのまま維持されている。**v4で具体化されたのは、
Step4の中身(旧版で「具体式は未確定」としていた部分)**であり、これは
`spatial_state`パッケージ(`voxel.py`の`update()`メソッド)として、
既に実装・検証済み。

## 4. まだ決まっていないこと(v4時点で更新)

**旧版で「未決定」としていた項目のうち、v4で解決済みのもの:**

| 旧版の未決定事項 | v4での解決 |
|---|---|
| mobility_flagがPENDINGを抜ける閾値 | $n_{0,\mu}$による、滑らかな$\kappa_\mu$に統一(§2.2.1)。硬い閾値は廃止 |
| ログオッズ更新式の詳細 | $(\alpha,\beta)$の更新式として確定(§2.4) |
| κ(確信度)の計算式 | $\kappa=n/(n+n_0)$に確定、$\kappa$専用の重みは不要と判明(§2.6) |

**実データでの較正が、まだ必要なもの**(§7・`point_cloud_requirements_full_report.md`参照):

1. Step5(カバー範囲)の半径・閾値(`h_cov`)の具体的な値
2. $\gamma, \gamma_\mu, n_0, n_{0,\mu}, \kappa_{th}, M_{min}, N_{fit,min}$の実データでの較正
3. **【新規、実装時に発見】$n_0$は、実際に使う近傍数(面隣接6・全26等)による
   $s_3$の希釈を考慮しないと、孤立した物体が永久にCONFIRMEDへ到達できない
   場合がある(理論制約式は`spatial_state/params.py`の`validate()`に実装済み。
   近傍の定義を変える場合は、必ずこの検証を通すこと)**
4. `LABEL_FITNESS_HISTORY`の蓄積方法(セッションごとに1件か、パッチごとに
   複数件になりうるか)は、実際のVGICP呼び出し粒度に合わせて設計する必要がある

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

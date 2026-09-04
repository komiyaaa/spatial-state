/**
 * shared/viewer-theme.js
 *
 * 全Three.jsビューワ(メインビューワ・Registration・Nodal・Integrated View・
 * Plane/Rotation Preview)で共通の背景色。visual design基盤整備(2026-09-03)
 * により、各モジュールが個別にリテラル値(旧: 0xf5f4f1 のクリーム色)を
 * 持っていたものを1箇所に集約しただけで、ビューワのpick/レイヤー/カメラ等の
 * ロジックには一切触れない。
 *
 * shared/design-tokens.css の --bg-canvas と同じ色(CSS変数はThree.jsの
 * 数値カラーとして直接使えないため、同じ値をJS側にも定数として複製している)。
 */
export const VIEWER_BACKGROUND_COLOR = 0x10141a;

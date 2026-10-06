/** 视觉真源的单一出口：数据在 theme.generated.ts，类型与颜色数学在这里。
 *
 * 主题数值（版式 / 字体 / 动画参数 / 配色）原本是这个文件里的一份**手工复刻**，
 * 靠 tests/test_theme_parity.py 341 行逐字段盯着 Python 侧别改。现在数值由
 * `python scripts/gen_theme_ts.py` 从 scripts/_template.py + scripts/_theme.py
 * 生成到 theme.generated.ts，本文件不再有第二份真源：
 *
 *   改视觉参数 → 只改 Python → 跑一次 gen_theme_ts.py → 连同生成物一起提交。
 *
 * 剩下留在这里的只有生成器投影不出来的东西：段类型标注，以及 WCAG 对比度
 * 保底的颜色数学（_theme.py 的 TS 移植）。
 *
 * 组件只允许写"两画幅同值、单值即终态"的观感常量（字重、透明度、辉光浓度），
 * 其余一律走这里。
 */

import {
  ANIM,
  FONT_STACK,
  LANDSCAPE_ONLY,
  LAYOUTS as LAYOUTS_RAW,
  MONO_STACK,
  THEME,
  TYPO,
} from "./theme.generated";

export {ANIM, FONT_STACK, MONO_STACK, THEME, TYPO};

export type Aspect = "vertical" | "landscape";
export type SegmentLayout = "slot" | "canvas" | "agenda";
export type OpeningAnimation = "apple" | null;

export interface Layout {
  verse: {
    windowHeight: number; bottom: number; clipPad: number; linePad: number;
    lineHeight: number;
    /** 当前句下方高亮规则的厚度（em，随字号缩放）。 */
    activeRule: number;
  };
  title: {top: number; fontSize: number; maxLines: number; glow: number; lineHeight: number};
  tagline: {fontSize: number; marginTop: number; lineHeight: number; indent: number; tickWidth: number};
  image: {width: number; height: number; top: number; borderRadius: number; marginSide: number; glow: number};
  /** verse 句流的字号（Python 侧 layout.<画幅>.subtitle.fontSize）。 */
  subtitle: {fontSize: number};
  progressBar: {height: number};
  grid: {size: number};
  ambience: {rx: number; ry: number; cx: number; cy: number; alpha: number; edge: number; agendaCx: number; agendaCy: number};
  agenda: {
    insetX: number; insetTop: number; insetBottom: number;
    titleSize: number; titleLineHeight: number; titleMarginTop: number;
    listMarginTop: number; kickerSize: number; idxSize: number; idxMinWidth: number;
    nameSize: number; durSize: number; rowGap: number; rowPad: number;
    verseMaxWidth: number; maxRows: number; nameTrim: number; titleGlow: number;
  };
}

/** 少一个画幅、或某画幅少一个字段，这里就红——也就是 Python 侧真源改字段名
 * 却忘了重新生成。多一个字段这里不红：`marginSide` / `clipPad` 这类值组件
 * 虽不读，但它们是 Python 版式的真实参数，投影过来就该在。真正的死字段不是
 * 「组件没读」，而是「改了不会有任何画面变化」（已删掉的 ANIM.*.ease 就是）。 */
export const LAYOUTS: Record<Aspect, Layout> = LAYOUTS_RAW;

/** 横屏专属：左文字栏几何（Python 侧 layout.landscape.{margin,textCol}）
 * + 标题按字数降档（Remotion 独有——几何精确复刻需要布局引擎，按字数近似）。
 * 单列导出：竖屏没有左文字栏，这些数在竖屏上没有对应物。 */
export {LANDSCAPE_ONLY};

/** 动画参数：与 _template.py 的 animation 块一一对应。
 *
 * 揭幕曲线只有一档：line 档是模板默认（`segmentWipe.style == "line"`），引导线
 * 与 clip-path 揭幕共用 propLine 这一组（ease 同为 power2.inOut）。生成期会
 * 挡掉非 line 的 style（gen_remotion_project._is_line），所以这里不保留
 * segmentWipe 那组从未被渲染端读过的参数。
 *
 * 这里**没有** ease 字段：渲染端的缓动真源是 easing.ts 的具名导出，组件直接
 * 调 backOut() / power2Out()。ANIM 里再存一份字符串副本，改了不会有任何画面
 * 变化——两处都以为自己在管，不如一处都没有。 */

// ── 颜色数学（_theme.py 的 TS 移植：hex 解析 / 混合 / WCAG 对比度保底）────

const hexToRgb = (hex: string): [number, number, number] | null => {
  let h = hex.trim().replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  if (h.length !== 6 || !/^[0-9a-fA-F]{6}$/.test(h)) return null;
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
};

export const rgba = (hex: string, alpha: number): string => {
  const rgb = hexToRgb(hex) ?? [0, 0, 0];
  return `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`;
};

/** 按比例混两色（0=全 a，1=全 b；_theme.mix 的 TS 移植）。 */
export const mixColors = (a: string, b: string, ratio: number): string => {
  const ra = hexToRgb(a);
  const rb = hexToRgb(b);
  if (!ra || !rb) return a;
  const c = ra.map((v, i) => Math.round(v * (1 - ratio) + rb[i] * ratio));
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
};

const relativeLuminance = (hex: string): number | null => {
  const rgb = hexToRgb(hex);
  if (!rgb) return null;
  const lin = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
};

const contrastRatio = (a: string, b: string): number => {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  if (la === null || lb === null) return 0;
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
};

/** accent 文字色的取值：accent 在主题背景上对比度不足 4.5 时朝黑/白
 * 推移到位（与 _theme.ensure_text_contrast 同一口径，背景取主题渐变最坏一档）。 */
export const ensureTextContrast = (accent: string): string => {
  const backgrounds = THEME.bgStops;
  const worst = Math.min(...backgrounds.map((b) => contrastRatio(accent, b)));
  if (worst >= 4.5) return accent;
  const bg = backgrounds.reduce((minB, b) =>
    contrastRatio(accent, b) < contrastRatio(accent, minB) ? b : minB);
  const lum = relativeLuminance(bg) ?? 0;
  const toward = lum < 0.4 ? "#ffffff" : "#000000";
  for (let k = 1; k < 19; k++) {
    const c = mixColors(accent, toward, k * 0.05);
    const w = Math.min(...backgrounds.map((b) => contrastRatio(c, b)));
    if (w >= 4.5) return mixColors(accent, toward, Math.min(0.95, (k + 1) * 0.05));
  }
  return mixColors(accent, toward, 0.95);
};
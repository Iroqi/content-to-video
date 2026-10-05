/** 视觉真源：与 scripts/_theme.py / scripts/_template.py 一一对应的 TS 副本。
 *
 * 注意：HTML 版以 Python 模板为唯一真源、CSS 变量派生；Remotion 版组件无法
 * 读 CSS 变量，所以把模板数值复刻成这份 TS 常量。改视觉参数时两边要同步改
 * （references/rendering.md「版式真源」的纪律在这里变成"py 与 theme.ts 各改
 * 一处"，README 已写明该代价）。
 */

export type Aspect = "vertical" | "landscape";

export const THEME = {
  bgGradient: "linear-gradient(135deg,#060709 0%,#0d0f13 45%,#080a0c 100%)",
  // 主题渐变里的 hex 色标（文字对比度保底按最坏一档算，见 ensureTextContrast）
  bgStops: ["#060709", "#0d0f13", "#080a0c"],
  gridColor: "rgba(0,220,150,0.055)",
  textColor: "#eef2ee",
};

export const FONT_STACK =
  '"Microsoft YaHei", "PingFang SC", "Noto Sans SC", "Noto Sans CJK SC", "Noto Sans CJK JP", sans-serif';
export const MONO_STACK =
  'ui-monospace, "SF Mono", Consolas, "Courier New", monospace';

export const TYPO = {
  titleWeight: 900,
  taglineWeight: 600,
  titleLineHeight: 1.45,
  titleTracking: "-0.02em",
};

export interface Layout {
  verse: {windowHeight: number; bottom: number; clipPad: number; linePad: number; lineHeight: number};
  title: {top: number; fontSize: number; maxLines: number; glow: number; lineHeight: number};
  tagline: {fontSize: number; marginTop: number; lineHeight: number; indent: number; tickWidth: number};
  image: {width: number; height: number; top: number; borderRadius: number; marginSide: number; glow: number};
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

export const LAYOUTS: Record<Aspect, Layout> = {
  vertical: {
    verse: {windowHeight: 280, bottom: 50, clipPad: 40, linePad: 5, lineHeight: 1.5},
    title: {top: 80, fontSize: 64, maxLines: 2, glow: 40, lineHeight: 1.45},
    tagline: {fontSize: 32, marginTop: 8, lineHeight: 1.25, indent: 16, tickWidth: 8},
    image: {width: 980, height: 735, top: 325, borderRadius: 12, marginSide: 0, glow: 40},
    progressBar: {height: 4},
    grid: {size: 60},
    ambience: {rx: 58, ry: 42, cx: 50, cy: 50, alpha: 15, edge: 72, agendaCx: 50, agendaCy: 50},
    agenda: {
      insetX: 72, insetTop: 88, insetBottom: 96,
      titleSize: 84, titleLineHeight: 1.1, titleMarginTop: 18,
      listMarginTop: 16, kickerSize: 24, idxSize: 30, idxMinWidth: 52,
      nameSize: 42, durSize: 26, rowGap: 10, rowPad: 20,
      verseMaxWidth: 936, maxRows: 7, nameTrim: 20, titleGlow: 48,
    },
  },
  landscape: {
    verse: {windowHeight: 260, bottom: 0, clipPad: 40, linePad: 5, lineHeight: 1.45},
    title: {top: 0, fontSize: 62, maxLines: 0, glow: 40, lineHeight: 1.15},
    tagline: {fontSize: 28, marginTop: 16, lineHeight: 1.3, indent: 22, tickWidth: 8},
    image: {width: 1067, height: 800, top: 0, borderRadius: 12, marginSide: 0, glow: 40},
    progressBar: {height: 5},
    grid: {size: 60},
    ambience: {rx: 46, ry: 52, cx: 68, cy: 50, alpha: 13, edge: 70, agendaCx: 50, agendaCy: 45},
    agenda: {
      insetX: 96, insetTop: 80, insetBottom: 72,
      titleSize: 96, titleLineHeight: 1.12, titleMarginTop: 12,
      listMarginTop: 16, kickerSize: 26, idxSize: 34, idxMinWidth: 60,
      nameSize: 40, durSize: 24, rowGap: 6, rowPad: 8,
      verseMaxWidth: 900, maxRows: 7, nameTrim: 26, titleGlow: 48,
    },
  },
};

export const LANDSCAPE_EXTRA = {
  textCol: {width: 613, gap: 56},
  margin: 96,
  titleGuards: [
    {chars: 16, size: 54},
    {chars: 22, size: 48},
  ],
  subtitleFont: 33,
};

/** 动画参数：与 _template.py 的 animation 块一一对应（line 档是默认 wipe 档，
 * propLine.ease 同时是 clip-path 与引导线的揭幕曲线，见 _wipe_ease 注释）。 */
export const ANIM = {
  titleEntrance: {from: 0.5, duration: 0.5, ease: "back.out(1.7)" as const},
  entranceBudget: {minFactor: 0.45, normSeconds: 4.0},
  imageEntrance: {duration: 0.8, ease: "power2.out" as const, startDelay: 0.2, vertY: 40},
  wipe: {style: "line" as const, duration: 0.28},
  propLine: {
    thickness: 6, duration: 0.4, ease: "power2.inOut" as const,
    peelFrac: 0.3, peelRotation: -4, peelTilt: 12, peelPerspective: 1000,
    peelShadeFrac: 0.08, peelEase: "power2.in" as const,
  },
  canvasAmbience: {rx: 135, ry: 85, cx: 50, cy: 42, alpha: 13, edge: 82},
  apple: {
    title: {blur: 16, scale: 1.06, duration: 1.6, ease: "power3.out" as const},
    kicker: {blur: 8, delay: 0.4, duration: 1.2, ease: "power2.out" as const},
    rows: {delay: 0.7, duration: 0.7, stagger: 0.12, y: 24, ease: "power2.out" as const},
    halo: {in: 1.6, opacity: 0.55, breatheTo: 0.35, breatheDur: 1.2},
  },
};

export type SegmentLayout = "slot" | "canvas" | "agenda";
export type OpeningAnimation = "apple" | null;

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

/** --seg-accent-text 的 TS 版：accent 在主题背景上对比度不足 4.5 时朝黑/白
 * 推移到位（移植 _theme.ensure_text_contrast，背景取主题渐变最坏一档）。 */
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

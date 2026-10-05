import type {SegmentLayout, OpeningAnimation, Aspect} from "./theme";
import {ANIM} from "./theme";
import {DATA} from "./generated";

export type {Aspect};

export interface GenSentence {
  index: number;
  text: string;
  startTime: number;
  duration: number;
}

export interface GenRow {
  idx: string;
  name: string;
  dur: string | null;
}

/** director.steps 的求值数据（生成器按帧采样/展开，组件只按绝对帧求值）。 */
export interface DirectorStep {
  target: string;
  kind: "to" | "from" | "fromTo" | "set" | "draw" | "count" | "type" | "morph";
  pos: number;
  dur: number;
  ease: string;
  repeat: number;          // -1 = 无限（周期求值，无收尾态，与 GSAP repeat:-1 同确定性）
  yoyo: boolean;
  stagger: number | {each?: number; amount?: number; from?: string | number; grid?: [number, number]} | null;
  // 补间变量（契约层已滤成 JSON 标量；值可为嵌套 attr:{}）
  fromVars?: Record<string, unknown>;
  toVars?: Record<string, unknown>;
  vars?: Record<string, unknown>;   // set
  count?: {from: number; to: number; decimals: number; prefix: string; suffix: string};
  morphKeys?: {t: number; d: string}[];
}

export interface DirectorData {
  segStart: number;
  camOrigin: [number, number] | null;
  steps: DirectorStep[];
}

export interface GenMedia {
  src: string;
  poster?: string | null;
  loop?: boolean;
  muted?: boolean;
}

export interface GenSegment {
  id: string;
  title: string;
  tagline: string | null;
  accent: string;
  layout: SegmentLayout;
  openingAnimation: OpeningAnimation;
  sentences: GenSentence[];
  start: number;
  duration: number;
  wipe: number;
  winStart: number;
  vis: number;
  peel: {sid: string; wipe: number} | null;
  /** 媒体渲染方式：img（静态图/gif <img>）、video（Remotion <Video>）、
   * svgInline（净化内联 SVG：director / stage:"keep"）、null（纯文字）。 */
  imageMode: "img" | "video" | "svgInline" | null;
  media: GenMedia | null;
  /** 净化内联的 SVG（keep 页为上一页演完画面的烘焙副本）。 */
  svg: string | null;
  director: DirectorData | null;
  keep: boolean;
  rows: GenRow[];
  tail: GenRow | null;
}

export const FPS: number = DATA.fps;
export const WIDTH: number = DATA.width;
export const HEIGHT: number = DATA.height;
export const TOTAL_DURATION: number = DATA.totalDuration;
export const AUDIO_SRC: string | null = DATA.audioSrc;
export const SEGMENTS: GenSegment[] = DATA.segments as unknown as GenSegment[];

export const ASPECT: Aspect = WIDTH > HEIGHT ? "landscape" : "vertical";

/** 该段是否正在可见窗口内（窗口 = [winStart, winStart + vis)）。 */
export const visibleAt = (seg: GenSegment, t: number): boolean =>
  t >= seg.winStart && t < seg.winStart + seg.vis;

/** 段落入场动效预算随段长归一化：段越长给入场动画的时间预算越足，
 * 但不低于 ANIM.entranceBudget.minFactor（短段也不至于瞬间落定）。 */
export const entranceK = (seg: GenSegment): number => {
  const eb = ANIM.entranceBudget;
  return Math.min(1, Math.max(eb.minFactor, seg.duration / eb.normSeconds));
};

/** 该段 verse 的当前句（全局句子起点锚定；句间静音/段尾保持最后一句，
 * 与 runtime.js 的 curCueIdx 语义一致）。返回 -1 = 首句之前（无高亮）。 */
export const activeSentenceIndex = (seg: GenSegment, t: number): number => {
  let active = -1;
  for (let j = 0; j < seg.sentences.length; j++) {
    if (t >= seg.sentences[j].startTime) active = j;
  }
  return active;
};

/** 被下一页揭幕窗口推走的旧卡（line 档 peel）：cards[i+1].peel.sid 指向本卡。 */
export const peelFor = (
  segments: GenSegment[],
  i: number,
): {wipe: number; winStart: number} | null => {
  const next = segments[i + 1];
  if (next && next.peel && next.peel.sid === segments[i].id) {
    return {wipe: next.peel.wipe, winStart: next.winStart};
  }
  return null;
};

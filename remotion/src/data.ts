import type {SegmentLayout, OpeningAnimation, Aspect} from "./theme";
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
  image: string | null;
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

/** 段落入场动效预算随段长归一化（与 html_renderer 同一条公式）。 */
export const entranceK = (seg: GenSegment): number => {
  const eb = {minFactor: 0.45, normSeconds: 4.0};
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

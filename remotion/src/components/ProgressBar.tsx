import React from "react";
import {LAYOUTS, rgba} from "../theme";
import type {Aspect} from "../theme";
import type {GenSegment} from "../data";
import {clamp01} from "../easing";

interface ProgressBarProps {
  seg: GenSegment;
  t: number;
  aspect: Aspect;
  w: number;
  h: number;
}

/** 底部进度条：段起点 0% → 段终点 100%，线性，与段落时间轴同步
 * （html_renderer 的 `tl.to("#prog-{sid}",{width:"100%"},s)` 同口径）。
 * 轨道底轨（10% 文字色透明）垫在其下；辉光按模板高度 ×3 派生。 */
export const ProgressBar: React.FC<ProgressBarProps> = ({seg, t, aspect}) => {
  const ph = LAYOUTS[aspect].progressBar.height;
  const p = clamp01((t - seg.start) / Math.max(0.001, seg.duration));
  return (
    <div style={{position: "absolute", left: 0, right: 0, bottom: 0, height: ph}}>
      {/* 轨道 */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(238,242,238,0.10)",
        }}
      />
      {/* 填充 */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: `${p * 100}%`,
          background: seg.accent,
          boxShadow: `0 0 ${ph * 3}px ${rgba(seg.accent, 0.6)}`,
        }}
      />
    </div>
  );
};

import React from "react";
import {AbsoluteFill} from "remotion";
import type {Aspect} from "../theme";
import type {GenSegment} from "../data";
import {ProgressBar} from "./ProgressBar";
import {MediaBox} from "./MediaBox";

interface CanvasCardProps {
  seg: GenSegment;
  t: number;
  aspect: Aspect;
  w: number;
  h: number;
}

/** 整页画布：媒体（<img>/<video>/净化内联 SVG）拉满全屏，标题层/句子流不渲染
 * （文字由画布自己画），wipe 已被生成器归零 = 在自己的音频起点硬切出现，
 * 运动全由 director 驱动；进度条抬到画面之上。媒体缺失是数据问题（契约层
 * needs_image/canvas_layout_errors 拦截），这里仍兜底渲染空背景，不静默
 * 出裂图。 */
export const CanvasCard: React.FC<CanvasCardProps> = ({seg, t, aspect, w, h}) => {
  return (
    <AbsoluteFill>
      <MediaBox seg={seg} t={t} />
      <ProgressBar seg={seg} t={t} aspect={aspect} w={w} h={h} />
    </AbsoluteFill>
  );
};

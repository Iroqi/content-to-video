import React from "react";
import {AbsoluteFill, staticFile} from "remotion";
import type {Aspect} from "../theme";
import type {GenSegment} from "../data";
import {ProgressBar} from "./ProgressBar";

interface CanvasCardProps {
  seg: GenSegment;
  t: number;
  aspect: Aspect;
  w: number;
  h: number;
}

/** 整页画布：配图拉满全屏，标题层/句子流不渲染（文字由画布自己画），
 * wipe 已被生成器归零 = 在自己的音频起点硬切出现；进度条抬到画面之上。
 * 配图缺失是数据问题（契约层 needs_image/canvas_layout_errors 拦截），
 * 这里仍兜底渲染空背景，不静默出裂图。 */
export const CanvasCard: React.FC<CanvasCardProps> = ({seg, t, aspect, w, h}) => {
  return (
    <AbsoluteFill>
      {seg.image ? (
        <img
          src={staticFile(seg.image)}
          alt=""
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
            display: "block",
          }}
        />
      ) : null}
      <ProgressBar seg={seg} t={t} aspect={aspect} w={w} h={h} />
    </AbsoluteFill>
  );
};

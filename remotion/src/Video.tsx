import React from "react";
import {AbsoluteFill, Audio, staticFile, useCurrentFrame} from "remotion";
import {LAYOUTS, THEME} from "./theme";
import {AUDIO_SRC, ASPECT, FPS, HEIGHT, SEGMENTS, peelFor} from "./data";
import {Card} from "./components/Card";
import {AgendaCard} from "./components/AgendaCard";
import {SlotCard} from "./components/SlotCard";
import {CanvasCard} from "./components/CanvasCard";

/** 主合成：根背景（渐变 + 网格）+ 按 manifest 序堆叠的段落卡（后卡盖前卡）
 * + 全片音频。组件按绝对帧推导画面 = 单条时间线逐帧 seek 的等价实现
 *（references/rendering.md「动画」）。画幅宽高由 Remotion 根组件给定，
 * 组件内部一律按百分比/auto 外边距定位，不接 w/h —— 唯一按像素算的
 * peel/引导线位移用 HEIGHT（竖直方向），横向由 left/right 0 拉满。 */
export const Video: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const aspect = ASPECT;
  const g = LAYOUTS[aspect].grid.size;

  return (
    <AbsoluteFill style={{backgroundColor: "#060709"}}>
      <div style={{position: "absolute", inset: 0, background: THEME.bgGradient}} />
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage: `linear-gradient(${THEME.gridColor} 1px,transparent 1px),linear-gradient(90deg,${THEME.gridColor} 1px,transparent 1px)`,
          backgroundSize: `${g}px ${g}px`,
          WebkitMaskImage: "radial-gradient(120% 95% at 50% 45%,transparent 28%,#000 82%)",
          maskImage: "radial-gradient(120% 95% at 50% 45%,transparent 28%,#000 82%)",
        }}
      />

      {SEGMENTS.map((seg, i) => (
        <Card key={seg.id} seg={seg} t={t} aspect={aspect} h={HEIGHT} peel={peelFor(SEGMENTS, i)}>
          {seg.layout === "agenda" ? (
            <AgendaCard seg={seg} t={t} aspect={aspect} />
          ) : seg.layout === "canvas" ? (
            <CanvasCard seg={seg} t={t} aspect={aspect} />
          ) : (
            <SlotCard seg={seg} t={t} aspect={aspect} />
          )}
        </Card>
      ))}

      {AUDIO_SRC ? <Audio src={staticFile(AUDIO_SRC)} /> : null}
    </AbsoluteFill>
  );
};

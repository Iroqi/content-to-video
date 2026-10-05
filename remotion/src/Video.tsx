import React from "react";
import {AbsoluteFill, Audio, staticFile, useCurrentFrame} from "remotion";
import {LAYOUTS, THEME} from "./theme";
import {AUDIO_SRC, ASPECT, FPS, HEIGHT, SEGMENTS, WIDTH, peelFor} from "./data";
import {Card} from "./components/Card";
import {AgendaCard} from "./components/AgendaCard";
import {SlotCard} from "./components/SlotCard";
import {CanvasCard} from "./components/CanvasCard";

/** 主合成：根背景（渐变 + 网格）+ 按 manifest 序堆叠的段落卡（后卡盖前卡，
 * 与 HTML 的 DOM 序同语义）+ 全片音频。组件按绝对帧推导画面 = GSAP 单条
 * 时间线逐帧 seek 的等价实现（references/rendering.md「动画」）。 */
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
        <Card
          key={seg.id}
          seg={seg}
          t={t}
          aspect={aspect}
          w={WIDTH}
          h={HEIGHT}
          peel={peelFor(SEGMENTS, i)}
        >
          {seg.layout === "agenda" ? (
            <AgendaCard seg={seg} t={t} aspect={aspect} w={WIDTH} h={HEIGHT} />
          ) : seg.layout === "canvas" ? (
            <CanvasCard seg={seg} t={t} aspect={aspect} w={WIDTH} h={HEIGHT} />
          ) : (
            <SlotCard seg={seg} t={t} aspect={aspect} w={WIDTH} h={HEIGHT} />
          )}
        </Card>
      ))}

      {AUDIO_SRC ? <Audio src={staticFile(AUDIO_SRC)} /> : null}
    </AbsoluteFill>
  );
};

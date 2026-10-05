import React from "react";
import {AbsoluteFill} from "remotion";
import {ANIM, LAYOUTS, THEME, mixColors, rgba} from "../theme";
import type {Aspect} from "../theme";
import type {GenSegment} from "../data";
import {visibleAt} from "../data";
import {clamp01, power2In, power2InOut} from "../easing";

interface CardProps {
  seg: GenSegment;
  t: number;
  aspect: Aspect;
  w: number;
  h: number;
  peel: {wipe: number; winStart: number} | null;
  children: React.ReactNode;
}

const gridStyle = (aspect: Aspect): React.CSSProperties => {
  const g = LAYOUTS[aspect].grid.size;
  return {
    position: "absolute",
    inset: 0,
    backgroundImage: `linear-gradient(${THEME.gridColor} 1px,transparent 1px),linear-gradient(90deg,${THEME.gridColor} 1px,transparent 1px)`,
    backgroundSize: `${g}px ${g}px`,
    WebkitMaskImage: "radial-gradient(120% 95% at 50% 45%,transparent 28%,#000 82%)",
    maskImage: "radial-gradient(120% 95% at 50% 45%,transparent 28%,#000 82%)",
  };
};

/** 段落卡的不透明外壳：卡片底色 + 网格 + 氛围光（按声明序压底），
 * clip-path 揭幕 + line 档引导线 + 被下一页推走时的剥离（peel）。
 * 等价于 html_renderer 的卡 DOM + wipe/line/peel 三条 GSAP 补间：
 * 揭幕几何与引导线共用同一条曲线（ANIM.propLine.ease），peel 是旧卡
 * 在下张卡揭幕窗口里的加速上移 + 底缘暗边（peel 由下一张卡的 clip 数据
 * 给出，见 data.peelFor）。
 */
export const Card: React.FC<CardProps> = ({seg, t, aspect, w, h, peel, children}) => {
  if (!visibleAt(seg, t)) return null;
  const lay = LAYOUTS[aspect];
  const wipe = seg.wipe;
  const eased = wipe > 0 ? power2InOut(clamp01((t - seg.winStart) / wipe)) : 1;
  const clipTop = (1 - eased) * 100;
  const clipPath = `inset(${clipTop}% 0 0 0)`;

  // 引导线（line 档）：骑揭示边从页底扫到页顶，终位 -thickness 缩没进边沿
  const pl = ANIM.propLine;
  const lineTop = wipe > 0 ? (1 - eased) * h - pl.thickness : null;
  const line = lineTop !== null ? (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: 0,
        height: pl.thickness,
        transform: `translateY(${lineTop}px)`,
        background: `linear-gradient(90deg,transparent 0%,${seg.accent} 16%,${mixColors(seg.accent, "#ffffff", 0.45)} 50%,${seg.accent} 84%,transparent 100%)`,
        boxShadow: `0 12px 30px 5px ${rgba(seg.accent, 0.55)}`,
      }}
    />
  ) : null;

  // 被下一张卡揭幕推走：加速上移 + 平面逆旋 + 绕底边的透视后倒 + 底缘暗边
  let peelTransform: React.CSSProperties = {};
  let shade: React.ReactNode = null;
  if (peel && peel.wipe > 0) {
    const p = power2In(clamp01((t - peel.winStart) / peel.wipe));
    peelTransform = {
      transform:
        `perspective(${pl.peelPerspective}px) translateY(${-pl.peelFrac * h * p}px) ` +
        `rotate(${pl.peelRotation * p}deg) rotateX(${pl.peelTilt * p}deg)`,
      transformOrigin: "50% 100%",
    };
    shade = (
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: pl.peelShadeFrac * h,
          background: "linear-gradient(0deg,rgba(0,0,0,0.5),transparent)",
          opacity: p,
        }}
      />
    );
  }

  // 氛围光（accent 稀释成的径向光垫在内容之下；agenda 拨到文字重心
  // agendaCx/Cy，canvas 换近全屏宽带柔光——口径与 composition.css 同）
  const amb =
    seg.layout === "canvas"
      ? ANIM.canvasAmbience
      : seg.layout === "agenda"
        ? {
            rx: lay.ambience.rx,
            ry: lay.ambience.ry,
            cx: lay.ambience.agendaCx,
            cy: lay.ambience.agendaCy,
            alpha: lay.ambience.alpha,
            edge: lay.ambience.edge,
          }
        : {
            rx: lay.ambience.rx,
            ry: lay.ambience.ry,
            cx: lay.ambience.cx,
            cy: lay.ambience.cy,
            alpha: lay.ambience.alpha,
            edge: lay.ambience.edge,
          };
  const ambStyle: React.CSSProperties = {
    position: "absolute",
    inset: 0,
    background: `radial-gradient(${amb.rx}% ${amb.ry}% at ${amb.cx}% ${amb.cy}%,${rgba(seg.accent, amb.alpha / 100)},transparent ${amb.edge}%)`,
  };

  return (
    <AbsoluteFill style={{...peelTransform, clipPath}}>
      <div style={{position: "absolute", inset: 0, background: THEME.bgGradient}} />
      <div style={gridStyle(aspect)} />
      <div style={ambStyle} />
      {children}
      {line}
      {shade}
    </AbsoluteFill>
  );
};

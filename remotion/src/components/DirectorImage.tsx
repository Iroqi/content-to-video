import React, {useLayoutEffect, useRef} from "react";
import {applyDirectorFrame} from "./director";
import type {DirectorData} from "../data";

interface Props {
  svg: string;
  director: DirectorData | null;
  t: number;
  style?: React.CSSProperties;
}

/** 净化内联 SVG + 导演编排逐帧求值。
 *
 * svg 由生成器净化/烘焙（script/事件/外链/动画 CSS 已被 _svg_sanitize 摘掉），
 * 渲染端只做两件事：dangerouslySetInnerHTML 一次性注入（容器在整个可见窗口
 * 内保持同一个 DOM，状态可跨帧延续），每帧 useLayoutEffect 按绝对帧把
 * director 数据落到 DOM（等价于 GSAP 单条时间线的逐帧 seek）。svg 为空时
 * 渲染透明占位（纯 director 页可能没有可见图）。
 */
export const DirectorImage: React.FC<Props> = ({svg, director, t, style}) => {
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!ref.current || !director) return;
    applyDirectorFrame(ref.current, director, t);
  }, [t, director]);

  if (!svg) return null;
  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        inset: 0,
        overflow: "hidden",
        ...style,
      }}
      dangerouslySetInnerHTML={{__html: svg}}
    />
  );
};

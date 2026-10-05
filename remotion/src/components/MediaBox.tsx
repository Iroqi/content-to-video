import React from "react";
import {Img, staticFile, Video} from "remotion";
import {DirectorImage} from "./DirectorImage";
import type {GenSegment} from "../data";

interface MediaBoxProps {
  seg: GenSegment;
  t: number;
  /** 槽位卡的内嵌媒体框（slot）或整页媒体区（canvas）都复用这一份。 */
  style?: React.CSSProperties;
}

/** 按 seg.imageMode 渲染媒体（img / video / svgInline 导演内联）。
 *
 * 与 html_renderer._media_html 对应：静态图/gif 走 <img>（objectFit cover）、
 * 视频走 <video>（loop/muted/poster 读 items 选项）、director / stage:"keep"
 * 走净化内联 SVG。Remotion 下 autoplay/playsinline 是浏览器语义、逐帧渲染
 * 无意义，这里只保留 loop/muted/poster（README 已声明）。
 */
export const MediaBox: React.FC<MediaBoxProps> = ({seg, t, style}) => {
  const base: React.CSSProperties = {
    position: "absolute",
    inset: 0,
    width: "100%",
    height: "100%",
    ...style,
  };

  if (seg.imageMode === "svgInline" && seg.svg) {
    return <DirectorImage svg={seg.svg} director={seg.director} t={t} style={base} />;
  }

  if (seg.imageMode === "video" && seg.media) {
    const m = seg.media;
    return (
      <Video
        src={staticFile(m.src)}
        loop={m.loop !== false}
        muted={m.muted !== false}
        style={{...base, objectFit: "cover"}}
      />
    );
  }

  if (seg.imageMode === "img" && seg.media) {
    return (
      <Img
        src={staticFile(seg.media.src)}
        style={{...base, objectFit: "cover"}}
      />
    );
  }

  return null;
};

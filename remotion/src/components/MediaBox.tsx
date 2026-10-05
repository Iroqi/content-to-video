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
 * 静态图/gif 走 <Img>（objectFit cover）、视频走 <Video>、director 与
 * stage:"keep" 走净化内联 SVG。images.json 的 autoplay/playsinline 是浏览器
 * 播放语义，逐帧渲染无意义，契约层收下但渲染端不读（README 已声明）；poster
 * 则照原生 <video> 属性透传——视频首帧尚未解出时它就是画面，不接会露出空帧。
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
        poster={m.poster ? staticFile(m.poster) : undefined}
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

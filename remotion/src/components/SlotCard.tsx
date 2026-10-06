import React from "react";
import {AbsoluteFill} from "remotion";
import {
  ANIM, FONT_STACK, LANDSCAPE_ONLY, LAYOUTS, MONO_STACK, THEME, TYPO,
  ensureTextContrast, rgba,
} from "../theme";
import type {Aspect} from "../theme";
import type {GenSegment} from "../data";
import {entranceK} from "../data";
import {backOut, clamp01, lerp, power2Out} from "../easing";
import {ProgressBar} from "./ProgressBar";
import {Verse} from "./Verse";
import {MediaBox} from "./MediaBox";

interface SlotCardProps {
  seg: GenSegment;
  t: number;
  aspect: Aspect;
}

/** 槽位版式（默认内容段）：标题区 + 配图槽 + verse 句子流。
 * 竖屏三区各自绝对定位（标题区固定高度、画布定高居中、verse 钉底）；
 * 横屏是左文字栏（标题+tagline+verse 垂直居中成块）+ 右侧媒体卡。
 * 配图缺失时纯文字兜底（槽位版式缺图只是少一块画面）。
 */
export const SlotCard: React.FC<SlotCardProps> = ({seg, t, aspect}) => {
  const lay = LAYOUTS[aspect];
  const k = entranceK(seg);
  const titleP = backOut(clamp01((t - seg.start) / (ANIM.titleEntrance.duration * k)));
  const titleScale = lerp(ANIM.titleEntrance.from, 1, titleP);

  const aImg = ANIM.imageEntrance;
  // 接续页（stage:"keep"）：画面是上一页演完的烘焙副本，入场补间退场
  // （keep/canvas 同闸），配图从第一帧就位。
  const imgStart = seg.start + aImg.startDelay * k;
  const imgP = seg.keep ? 1 : power2Out(clamp01((t - imgStart) / (aImg.duration * k)));
  const imgY = seg.keep ? 0 : aImg.vertY * (1 - imgP);
  const imgOpacity = seg.keep ? 1 : t >= imgStart ? imgP : 0;

  // 横屏左文字栏：标题 + tagline + verse 成列垂直居中
  if (aspect === "landscape") {
    const lt = lay.title;
    const ls = LANDSCAPE_ONLY;
    let titleSize = lt.fontSize;
    for (const g of ls.titleGuards) {
      if (seg.title.length > g.chars) titleSize = g.size;
    }
    const margin = ls.margin;
    return (
      <AbsoluteFill>
        <div
          style={{
            position: "absolute",
            left: margin,
            top: margin,
            bottom: margin,
            width: ls.textCol.width,
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            gap: ls.textCol.gap,
          }}
        >
          <div style={{fontFamily: FONT_STACK}}>
            <div
              style={{
                fontSize: titleSize,
                fontWeight: TYPO.titleWeight,
                lineHeight: lt.lineHeight,
                letterSpacing: TYPO.titleTracking,
                // 与竖屏标题同一个显式取值：Card.tsx 从不设 color（氛围光只是
                // 一层 radial-gradient 背景，不参与继承），所以不写这一行会落到
                // 浏览器默认黑色——黑字压在近黑渐变上，横屏标题整行看不见。
                color: THEME.textColor,
                textShadow: `0 0 ${lt.glow}px ${rgba(seg.accent, 0.25)}`,
                transform: titleScale !== 1 ? `scale(${titleScale})` : undefined,
                textWrap: "balance",
              }}
            >
              {seg.title}
            </div>
            {seg.tagline ? <Tagline seg={seg} aspect={aspect} /> : null}
          </div>
          <Verse seg={seg} t={t} aspect={aspect} placement="column-slot" />
        </div>
        <div
          style={{
            position: "absolute",
            right: margin,
            top: margin,
            bottom: margin,
            width: lay.image.width,
            height: lay.image.height,
            marginTop: "auto",
            marginBottom: "auto",
            borderRadius: lay.image.borderRadius,
            overflow: "hidden",
            boxShadow: `0 0 ${lay.image.glow}px ${rgba(seg.accent, 0.25)}`,
            opacity: imgOpacity,
            transform: imgY !== 0 ? `translateY(${imgY}px)` : undefined,
          }}
        >
          <MediaBox seg={seg} t={t} style={{borderRadius: lay.image.borderRadius}} />
        </div>
        <ProgressBar seg={seg} t={t} aspect={aspect} />
      </AbsoluteFill>
    );
  }

  // ── 竖屏 ──
  const titleArea =
    lay.title.maxLines * lay.title.fontSize * TYPO.titleLineHeight +
    lay.tagline.marginTop +
    lay.tagline.fontSize * lay.tagline.lineHeight;
  return (
    <AbsoluteFill>
      {/* 标题区：定高 + 两行钳制，画布/verse 不随标题折行移动 */}
      <div
        style={{
          position: "absolute",
          left: 50,
          right: 50,
          top: lay.title.top,
          height: titleArea,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            WebkitLineClamp: lay.title.maxLines,
            overflow: "hidden",
            fontSize: lay.title.fontSize,
            fontWeight: TYPO.titleWeight,
            lineHeight: TYPO.titleLineHeight,
            letterSpacing: TYPO.titleTracking,
            color: THEME.textColor,
            fontFamily: FONT_STACK,
            textShadow: `0 0 ${lay.title.glow}px ${rgba(seg.accent, 0.25)}`,
            transform: titleScale !== 1 ? `scale(${titleScale})` : undefined,
          }}
        >
          {seg.title}
        </div>
        {seg.tagline ? <Tagline seg={seg} aspect={aspect} /> : null}
      </div>

      {/* 4:3 画布居中（auto 外边距，transform 留给入场补间） */}
      <div
        style={{
          position: "absolute",
          top: lay.image.top,
          left: 50,
          right: 50,
          margin: "0 auto",
          width: lay.image.width,
          height: lay.image.height,
          borderRadius: lay.image.borderRadius,
          overflow: "hidden",
          boxShadow: `0 0 ${lay.image.glow}px ${rgba(seg.accent, 0.25)}`,
          opacity: imgOpacity,
          transform: imgY !== 0 ? `translateY(${imgY}px)` : undefined,
        }}
      >
          <MediaBox seg={seg} t={t} style={{borderRadius: lay.image.borderRadius}} />
        </div>

      <Verse seg={seg} t={t} aspect={aspect} placement="vertical-slot" />
      <ProgressBar seg={seg} t={t} aspect={aspect} />
    </AbsoluteFill>
  );
};

/** tagline：mono 小字 + 左侧 accent 刻度条（不推挤文字）。 */
const Tagline: React.FC<{seg: GenSegment; aspect: Aspect}> = ({seg, aspect}) => {
  const tg = LAYOUTS[aspect].tagline;
  return (
    <div
      style={{
        position: "relative",
        marginTop: tg.marginTop,
        paddingLeft: tg.indent,
        fontFamily: MONO_STACK,
        fontSize: tg.fontSize,
        fontWeight: TYPO.taglineWeight,
        lineHeight: tg.lineHeight,
        letterSpacing: "0.05em",
        color: ensureTextContrast(seg.accent),
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
      }}
    >
      <span
        style={{
          position: "absolute",
          left: 0,
          top: "50%",
          width: tg.tickWidth,
          height: "1.15em",
          transform: "translateY(-50%)",
          borderRadius: tg.tickWidth / 2,
          background: `linear-gradient(180deg,${seg.accent},${rgba(seg.accent, 0.45)})`,
        }}
      />
      {seg.tagline}
    </div>
  );
};

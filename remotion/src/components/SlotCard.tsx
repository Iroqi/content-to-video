import React from "react";
import {AbsoluteFill, staticFile} from "remotion";
import {FONT_STACK, LAYOUTS, MONO_STACK, THEME, TYPO, rgba} from "../theme";
import type {Aspect} from "../theme";
import type {GenSegment} from "../data";
import {entranceK} from "../data";
import {backOut, clamp01, lerp, power2Out} from "../easing";
import {ProgressBar} from "./ProgressBar";
import {Verse} from "./Verse";
import {ensureTextContrast as ensureTextContrastColor} from "../theme";

interface SlotCardProps {
  seg: GenSegment;
  t: number;
  aspect: Aspect;
  w: number;
  h: number;
}

/** 槽位版式（默认内容段）：标题区 + 配图槽 + verse 句子流。
 * 竖屏三区各自绝对定位（标题区固定高度、画布定高居中、verse 钉底）；
 * 横屏是左文字栏（标题+tagline+verse 垂直居中成块）+ 右侧媒体卡。
 * 配图缺失时纯文字兜底（槽位版式缺图只是少一块画面）。
 */
export const SlotCard: React.FC<SlotCardProps> = ({seg, t, aspect, w, h}) => {
  const lay = LAYOUTS[aspect];
  const k = entranceK(seg);
  const aTitle = ANIM_TITLE;
  const titleDur = aTitle.duration * k;
  const titleP = backOut(clamp01((t - seg.start) / titleDur));
  const titleScale = lerp(aTitle.from, 1, titleP);

  const aImg = ANIM_IMG;
  const imgStart = seg.start + aImg.startDelay * k;
  const imgP = power2Out(clamp01((t - imgStart) / (aImg.duration * k)));
  const imgY = aImg.vertY * (1 - imgP);
  const imgOpacity = t >= imgStart ? imgP : 0;

  // 横屏左文字栏：标题 + tagline + verse 成列垂直居中
  if (aspect === "landscape") {
    const lt = lay.title;
    let titleSize = lt.fontSize;
    for (const g of [{chars: 16, size: 54}, {chars: 22, size: 48}]) {
      if (seg.title.length > g.chars) titleSize = g.size;
    }
    const margin = 96;
    const textColW = 613;
    return (
      <AbsoluteFill>
        <div
          style={{
            position: "absolute",
            left: margin,
            top: margin,
            bottom: margin,
            width: textColW,
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            gap: 56,
          }}
        >
          <div style={{fontFamily: FONT_STACK}}>
            <div
              style={{
                fontSize: titleSize,
                fontWeight: TYPO.titleWeight,
                lineHeight: lt.lineHeight,
                letterSpacing: TYPO.titleTracking,
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
          {seg.image ? (
            <img
              src={staticFile(seg.image)}
              alt=""
              style={{width: "100%", height: "100%", objectFit: "cover", display: "block"}}
            />
          ) : null}
        </div>
        <ProgressBar seg={seg} t={t} aspect={aspect} w={w} h={h} />
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
        {seg.image ? (
          <img
            src={staticFile(seg.image)}
            alt=""
            style={{width: "100%", height: "100%", objectFit: "cover", display: "block"}}
          />
        ) : null}
      </div>

      <Verse seg={seg} t={t} aspect={aspect} placement="vertical-slot" />
      <ProgressBar seg={seg} t={t} aspect={aspect} w={w} h={h} />
    </AbsoluteFill>
  );
};

const ANIM_TITLE = {from: 0.5, duration: 0.5};
const ANIM_IMG = {duration: 0.8, startDelay: 0.2, vertY: 40};

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
        color: ensureTaglineColor(seg),
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

/** tagline 颜色：accent 在文字底色上压暗/提亮到达标档（HTML 走
 * --seg-accent-text 派生；这里用同一组对比度保底）。 */
function ensureTaglineColor(seg: GenSegment): string {
  // 直接复用 ensureTextContrast 的暗/亮两档推断：这里与 agenda idx 同口径
  return ensureTextContrastColor(seg.accent);
}

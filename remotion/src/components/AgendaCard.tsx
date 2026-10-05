import React from "react";
import {AbsoluteFill} from "remotion";
import {ANIM, FONT_STACK, LAYOUTS, MONO_STACK, THEME, TYPO, ensureTextContrast, rgba} from "../theme";
import type {Aspect} from "../theme";
import type {GenSegment} from "../data";
import {entranceK} from "../data";
import {backOut, clamp01, lerp, power2Out, power3Out, sineOut} from "../easing";
import {ProgressBar} from "./ProgressBar";
import {Verse} from "./Verse";

interface AgendaCardProps {
  seg: GenSegment;
  t: number;
  aspect: Aspect;
}

/** 开屏/结尾纯文字 agenda 卡：kicker + 大标题 + 章节/要点行 + verse。
 * opening_animation:"apple" 时整页换苹果風編排（光晕呼吸、模糊→锐利标题、
 * 逐行浮現），全部锚在擦除起点 win_start；静态模式标题走通用 back.out 入场。
 */
export const AgendaCard: React.FC<AgendaCardProps> = ({seg, t, aspect}) => {
  const lay = LAYOUTS[aspect];
  const ag = lay.agenda;
  const apple = seg.openingAnimation === "apple";
  const ap = ANIM.apple;
  const accentText = ensureTextContrast(seg.accent);

  // ── 苹果编排（锚 winStart，时长是编排的一部分、不随段长归一化）──────
  let haloOpacity = 0;
  if (apple) {
    if (t < seg.winStart + ap.halo.in) {
      haloOpacity = sineOut((t - seg.winStart) / ap.halo.in) * ap.halo.opacity;
    } else {
      const u = (t - (seg.winStart + ap.halo.in)) / ap.halo.breatheDur;
      haloOpacity =
        (ap.halo.opacity + ap.halo.breatheTo) / 2 +
        ((ap.halo.opacity - ap.halo.breatheTo) / 2) * Math.cos(Math.PI * u);
    }
  }
  const titleP = apple
    ? power3Out(clamp01((t - seg.winStart) / ap.title.duration))
    : backOut(clamp01((t - seg.start) / (ANIM.titleEntrance.duration * entranceK(seg))));
  const titleScale = apple
    ? lerp(ap.title.scale, 1, titleP)
    : lerp(ANIM.titleEntrance.from, 1, titleP);
  const titleBlur = apple ? ap.title.blur * (1 - titleP) : 0;
  const titleOpacity = apple ? titleP : 1;

  const kickerP = apple && seg.tagline
    ? power2Out(clamp01((t - (seg.winStart + ap.kicker.delay)) / ap.kicker.duration))
    : 1;
  const kickerBlur = apple && seg.tagline ? ap.kicker.blur * (1 - kickerP) : 0;
  const kickerOpacity = apple && seg.tagline ? kickerP : 1;

  const rowStyle = (i: number): React.CSSProperties => {
    if (!apple) return {opacity: 1, transform: "none"};
    const p = power2Out(
      clamp01((t - (seg.winStart + ap.rows.delay + i * ap.rows.stagger)) / ap.rows.duration),
    );
    return {opacity: p, transform: `translateY(${ap.rows.y * (1 - p)}px)`};
  };

  const rowBorder = `1px solid ${rgba(THEME.textColor, 0.14)}`;

  return (
    <AbsoluteFill>
      {/* 苹果题头光晕：衬在标题身后（声明序在列之前 → 列盖在上面） */}
      {apple ? (
        <div
          style={{
            position: "absolute",
            inset: "0 0 42% 0",
            opacity: haloOpacity,
            background: `radial-gradient(58% 92% at 50% 34%,${rgba(seg.accent, 0.34)},transparent 68%)`,
          }}
        />
      ) : null}

      <div
        style={{
          position: "absolute",
          left: ag.insetX,
          right: ag.insetX,
          top: ag.insetTop,
          bottom: ag.insetBottom,
          display: "flex",
          flexDirection: "column",
          color: THEME.textColor,
          fontFamily: FONT_STACK,
        }}
      >
        {seg.tagline ? (
          <div
            style={{
              fontFamily: MONO_STACK,
              fontSize: ag.kickerSize,
              fontWeight: 600,
              letterSpacing: "0.14em",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              opacity: kickerOpacity,
              filter: kickerBlur > 0 ? `blur(${kickerBlur}px)` : undefined,
            }}
          >
            {seg.tagline}
          </div>
        ) : null}

        <div
          style={{
            marginTop: ag.titleMarginTop,
            fontSize: ag.titleSize,
            fontWeight: TYPO.titleWeight,
            lineHeight: ag.titleLineHeight,
            letterSpacing: TYPO.titleTracking,
            textShadow: `0 0 ${ag.titleGlow}px ${rgba(seg.accent, 0.25)}`,
            opacity: titleOpacity,
            transform: titleScale !== 1 ? `scale(${titleScale})` : undefined,
            filter: titleBlur > 0 ? `blur(${titleBlur}px)` : undefined,
            textWrap: "balance",
          }}
        >
          {seg.title}
        </div>

        <div
          style={{
            marginTop: ag.listMarginTop,
            flex: "1 1 auto",
            minHeight: 0,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-evenly",
          }}
        >
          {seg.rows.map((r, i) => (
            <div
              key={r.idx + i}
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: ag.rowGap,
                padding: `${ag.rowPad}px 4px`,
                borderTop: rowBorder,
                ...rowStyle(i),
              }}
            >
              <span
                style={{
                  fontFamily: MONO_STACK,
                  fontSize: ag.idxSize,
                  fontWeight: 700,
                  minWidth: ag.idxMinWidth,
                  color: accentText,
                }}
              >
                {r.idx}
              </span>
              <span
                style={{
                  fontSize: ag.nameSize,
                  fontWeight: 700,
                  lineHeight: 1.4,
                  minWidth: 0,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {r.name}
              </span>
              {r.dur ? (
                <span
                  style={{
                    marginLeft: "auto",
                    fontFamily: MONO_STACK,
                    fontSize: ag.durSize,
                    color: rgba(THEME.textColor, 0.45),
                  }}
                >
                  {r.dur}
                </span>
              ) : null}
            </div>
          ))}
        </div>

        {seg.tail ? (
          <div style={{flexShrink: 0}}>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: ag.rowGap,
                padding: `${ag.rowPad}px 4px`,
                borderTop: 0,
              }}
            >
              <span
                style={{
                  fontFamily: MONO_STACK,
                  fontSize: ag.idxSize,
                  fontWeight: 700,
                  minWidth: ag.idxMinWidth,
                  color: accentText,
                }}
              >
                {seg.tail.idx}
              </span>
              <span
                style={{
                  fontSize: ag.nameSize,
                  fontWeight: 700,
                  lineHeight: 1.4,
                  minWidth: 0,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {seg.tail.name}
              </span>
            </div>
          </div>
        ) : null}

        <Verse seg={seg} t={t} aspect={aspect} placement="agenda" />
      </div>

      <ProgressBar seg={seg} t={t} aspect={aspect} />
    </AbsoluteFill>
  );
};

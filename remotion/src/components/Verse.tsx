import React from "react";
import {LAYOUTS, THEME, rgba} from "../theme";
import type {Aspect} from "../theme";
import type {GenSegment} from "../data";
import {activeSentenceIndex} from "../data";

export type VersePlacement = "agenda" | "vertical-slot" | "column-slot";

interface VerseProps {
  seg: GenSegment;
  t: number;
  aspect: Aspect;
  placement: VersePlacement;
}

/** 歌词式句子流：定高窗口 + 上下渐隐 mask + 活动句滚动锚定 + accent 高亮。
 *
 * 行高按版式数值直接算（linePad × 2 + 字号 × 行高），不依赖 DOM 测量——
 * Remotion 逐帧渲染，算术推导与 runtime.js 的懒缓存测量是同一组数的两个
 * 实现，口径见 references/rendering.md「画面结构」。
 *
 * placement：
 * - agenda：开屏/结尾卡内，随 flex 列文档流锚底（maxWidth + margin:auto 0 0）
 * - vertical-slot：竖屏内容段，绝对定位钉底（left/right 50、bottom 50）
 * - column-slot：横屏内容段，落在左文字栏 flex 列内（随列垂直居中）
 */
export const Verse: React.FC<VerseProps> = ({seg, t, aspect, placement}) => {
  const lay = LAYOUTS[aspect];
  const v = lay.verse;
  const subFont = lay.subtitle.fontSize;
  const lineBox = v.linePad * 2 + subFont * v.lineHeight;
  const clipH = seg.sentences.length * lineBox + v.clipPad * 2;
  const winH = v.windowHeight;
  const active = activeSentenceIndex(seg, t);
  const actTop = active >= 0 ? v.clipPad + active * lineBox : 0;
  const y =
    clipH <= winH
      ? 0
      : Math.max(winH - clipH, Math.min(0, v.clipPad - actTop));

  const base: React.CSSProperties =
    placement === "agenda"
      ? {
          position: "relative",
          width: "100%",
          maxWidth: lay.agenda.verseMaxWidth,
          height: winH,
          flexShrink: 0,
          margin: "auto 0 0",
        }
      : placement === "vertical-slot"
        ? {
            position: "absolute",
            left: 50,
            right: 50,
            bottom: v.bottom,
            width: "auto",
            height: winH,
          }
        : {
            position: "relative",
            width: "100%",
            height: winH,
            flexShrink: 0,
          };

  return (
    <div
      style={{
        ...base,
        overflow: "hidden",
        WebkitMaskImage: "linear-gradient(transparent,#000 14%,#000 86%,transparent)",
        maskImage: "linear-gradient(transparent,#000 14%,#000 86%,transparent)",
      }}
    >
      <div
        style={{
          position: "relative",
          padding: `${v.clipPad}px 0`,
          transform: `translateY(${y}px)`,
        }}
      >
        {seg.sentences.map((s, j) => {
          const isActive = j === active;
          const isPast = active >= 0 && j < active;
          return (
            <div
              key={s.index}
              style={{
                fontSize: subFont,
                lineHeight: v.lineHeight,
                fontWeight: 600,
                color: isActive ? seg.accent : THEME.textColor,
                opacity: isActive ? 1 : isPast ? 0.45 : 0.62,
                padding: `${v.linePad}px 0`,
                textAlign: "left",
                position: "relative",
              }}
            >
              {s.text}
              {isActive ? (
                <div
                  style={{
                    position: "absolute",
                    left: 0,
                    right: 0,
                    bottom: v.linePad * 0.4,
                    // 当前句下方那道高亮规则：厚度读 template 的 verse.activeRule
                    // （单位 em，随字号缩放）。此前这里硬编码 "0.12em"——两画幅
                    // 同值所以看不出错，但改模板不动这里就是一处静默分家。
                    height: `${v.activeRule}em`,
                    borderRadius: 999,
                    background: `linear-gradient(90deg,${rgba(seg.accent, 0.65)},transparent 62%)`,
                  }}
                />
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
};

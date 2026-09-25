/* ── verse 模式句子流驱动（挂在 GSAP timeline 的 onUpdate）────────────────
   依赖 runtime.js 已定义的 cues、tl 与 verseUpdate。 */
let curCueIdx = -1; // 当前已处理的 cue 下标，避免每帧重复触发
tl.eventCallback("onUpdate", function() {
  const t = tl.time();
  let found = -1;
  for (let i = 0; i < cues.length; i++) {
    if (t >= cues[i].t && t < cues[i].t + Math.max(cues[i].d, 0.01)) { found = i; break; }
  }
  if (found >= 0) {
    // 用命中的 cue 下标做键，而非 t|d|speaker：相邻句的 start 各自 round(,2)
    // 可能坍缩成同一 t，若 d 也相同则键值碰撞，第二条 cue 的高亮永不刷新。
    // 下标天然唯一，且 si 相同的相邻 cue 重复调用 verseUpdate 幂等。
    if (found !== curCueIdx) {
      curCueIdx = found;
      const c = cues[found];
      if (c.si !== undefined && c.si >= 0) verseUpdate(c.si);
    }
  } else {
    // 句间 gap：保持最后一行的状态（高亮停在末句，窗口不回滚）
    curCueIdx = -1;
  }
  if (window.__pvUpdate) window.__pvUpdate(); // 预览刷新钩子（无预览时为空）
});

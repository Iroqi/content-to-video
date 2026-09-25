/* ── Composition 运行时：GSAP 时间线 + cue 数据 + verse 滚动 ──────────────
   动态片段由 html_renderer.py 填进本文件的占位符：图表引导脚本（无图表时为
   空串）、逐段补间（数据驱动，由 Python 生成）、逐句字幕 cue 数组（唯一
   时间轴来源）、verse 滚动锚点（= 当前画幅的 verse.clipPad）。
   注意：注释里**不得写出占位符名字**——装配用的是全文替换，注释里的同名
   字符串也会被替换（会把整段代码塞进注释，甚至因早闭注释破坏语法）。
   时钟只有一个：GSAP 时间线。渲染器逐帧 seek 触发 onUpdate。 */
__CTV_CHART_BOOT__
window.__timelines = window.__timelines || {};
const tl = gsap.timeline({paused:true});
__CTV_GSAP__

// Sentence-flow sync via onUpdate
const cues = [
__CTV_CUES__
];

// verse 句子流：静态行集合（两画幅内容段统一渲染 .verse，滚动/高亮
// 机制两画幅共用同一份 cue 驱动；横屏 clipPad 与窗口高取各自模板值）。
// 渲染器逐帧 seek 触发 onUpdate（非线性顺序），测量做懒缓存——布局值
// （offsetTop/clientHeight/scrollHeight）不受 transform 与 seek 顺序影响、
// 字体就绪后恒定，首帧测量一次即可；transform 赋绝对值幂等，乱序 seek
// 重复执行结果一致（seek-safe）。
const verses = Array.from(document.querySelectorAll(".verse"));
const verseCache = new Map();
// 字体异步加载会改变行高：就绪后清缓存，下次触发时用最终字形重测
// （期间已设的 transform 按旧测量算，偏差在下一次 cue 切换即修正）
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(() => verseCache.clear());
}
const verseUpdate = (si) => {
  for (const v of verses) {
    let c = verseCache.get(v);
    if (!c) {
      const clip = v.querySelector(".verse-clip");
      c = {clip: clip, accent: clip.getAttribute("data-accent") || "", winH: v.clientHeight, clipH: clip.scrollHeight, lines: []};
      for (const ln of v.querySelectorAll(".verse-line")) {
        c.lines.push({el: ln, top: ln.offsetTop});
      }
      verseCache.set(v, c);
    }
    let act = null;
    for (const L of c.lines) {
      if (parseInt(L.el.getAttribute("data-i"), 10) === si) { act = L; break; }
    }
    for (const L of c.lines) {
      const di = parseInt(L.el.getAttribute("data-i"), 10);
      L.el.classList.toggle("active", L === act);
      // 当前句用段落 accent 着色（字重恒定，避免 500/700 切换时字形宽度
      // 跳变）；非活动行恢复主题色。换色/换透明度在这里是**瞬时**的：
      // 淡入淡出过渡只在人工预览里由 preview.js 注入，成片逐帧 seek 时
      // 每一帧都必须等于时间线时刻（理由见 subtitle-verse.css 的注释）
      L.el.style.color = (L === act && c.accent) ? c.accent : "";
      // past 只在同一 verse（同段落）内比较，跨段句序无先后语义
      L.el.classList.toggle("past", !!act && di < si);
    }
    if (act) {
      // 歌词式滚动：active 行滚到窗口顶部往下 clipPad px（= clip 顶部内边距，
      // 与 .verse-clip 的 padding 保持一致——首句/尾句完整落在 mask 渐隐区
      // 之外，不被边缘虚化）；首行不滚出顶、末行不露底白（clip 比窗口矮时
      // 整体不滚）
      const y = c.clipH <= c.winH ? 0
        : Math.max(c.winH - c.clipH, Math.min(0, __CTV_VERSE_CLIP__ - act.top));
      c.clip.style.transform = "translateY(" + y + "px)";
    }
  }
};
__CTV_SUBTITLE_JS__
// 注册放脚本末尾（同步执行流内，行为与紧跟构建后注册等价）：hyperframes 的
// lint 规则按源码位置判定"注册是否早于 document.fonts.ready 异步构建"
// （window.__timelines[ 出现点必须在 fonts.ready 之后）；字幕 JS 里
// Array.from 的 ".from(" 会被规则的 GSAP 补间正则误判，注册线在 fonts.ready
// 之前就会触发 gsap_timeline_registered_before_async_build 误报。
window.__timelines["main"] = tl;

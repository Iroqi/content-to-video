/* ── Composition 运行时：GSAP 时间线 + cue 数据 + verse 滚动 ──────────────
   动态片段由 html_renderer.py 填进本文件的占位符：逐段补间（数据驱动，由
   Python 生成）、逐句字幕 cue 数组（唯一时间轴来源）、verse 滚动锚点（= 当前
   画幅的 verse.clipPad）。
   注意：注释里**不得写出占位符名字**——装配用的是全文替换，注释里的同名
   字符串也会被替换（会把整段代码塞进注释，甚至因早闭注释破坏语法）。
   时钟只有一个：GSAP 时间线。渲染器逐帧 seek 触发 onUpdate。 */
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
const verseEmCache = new Map();
// 覆盖层的"每个字该在哪儿"缓存：按行元素记，内容是逐字命中 span 的链表。
// 字体就绪会改变折行，命中测试必须重来，所以和上面两份缓存一起清。
const vedBg = new Map();
// 字体异步加载会改变行高：就绪后清缓存，下次触发时用最终字形重测
// （期间已设的 transform 按旧测量算，偏差在下一次 cue 切换即修正）
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(() => { verseCache.clear(); verseEmCache.clear(); vedBg.clear(); });
}
// 懒测量：布局值（offsetTop/clientHeight/scrollHeight）不受 transform 与 seek
// 顺序影响、字体就绪后恒定，首帧测一次即可。
const verseCacheOf = (v) => {
  let c = verseCache.get(v);
  if (c) return c;
  const clip = v.querySelector(".verse-clip");
  c = {clip: clip, accent: clip.getAttribute("data-accent") || "", winH: v.clientHeight, clipH: clip.scrollHeight, lines: []};
  for (const ln of v.querySelectorAll(".verse-line")) {
    c.lines.push({el: ln, top: ln.offsetTop,
      t0: parseFloat(ln.getAttribute("data-t0")),
      t1: parseFloat(ln.getAttribute("data-t1")),
      // 发声长度（manifest 的 voiced，生成期写成 data-voiced）：词级强调的进度
      // 分母。音频块开头/结尾各有一段静默，拿 t1−t0 当分母会让最后一个词先亮完
      // 再干等。老 cue 没有这个属性 → NaN，退回整块时长（verseEmEnd 里判）。
      voiced: parseFloat(ln.getAttribute("data-voiced")),
      // 有没有可强调的正文（生成期在行尾放的挂点，见 html_renderer._em_marker）
      em: ln.querySelector(".verse-em") !== null,
      // 覆盖层元素（生成期就位的空盒子，见 html_renderer._ved_layer）
      ved: ln.querySelector(".verse-ved"),
      lastK: -1});
  }
  verseCache.set(v, c);
  return c;
};
// ── 词级强调（karaoke）：当前句按句内进度把文字切成"已念 / 未念"两段 ────
// 已念段染该段 accent、未念段压暗（见 composition.css 的 .verse-ved）。
//
// 三条纪律，缺一条这条链路就不可复现或不可用：
//   1. **纯函数**：第 k 个字只由「t、本句 t0/t1、实测字宽」决定——不看上一帧。
//      渲染器逐帧 seek 时可能乱序跳（见上方 verseCache 的注释），所以这里不能
//      有任何"当前亮到第几个"的状态累积。
//   2. **按渲染宽度切，不按字符数**：中文一字一宽、中英混排时按字数切会让高亮
//      领先或落后半句。字宽只在字号/容器宽变化时重测（字体就绪后清缓存）。
//   3. **句内匀速是唯一假设**：TTS 只回整段音频、没有词级时间戳（与导演档
//      `at` 的小数偏移同一条实测口径：平均偏 0.2 字、长句能到 1s 量级）。它只
//      影响"哪几个字先亮"，不影响字幕切换时刻——切换仍由 cue 的 t/d 硬切。
// 可强调的正文节点 = 行内**最后一个**文本节点（行首说话人标签是元素，正文在它
// 之后；生成期保证正文是一个裸文本节点，见 html_renderer._em_marker）。
const verseEmNode = (el) => {
  for (let n = el.lastChild; n; n = n.previousSibling) {
    if (n.nodeType === 3) return n;
  }
  return null;
};
const verseEmClip = (el) => {
  const node = verseEmNode(el);
  const len = node ? node.nodeValue.length : 0;
  const key = el.clientWidth + "|" + len;
  const hit = verseEmCache.get(el);
  if (hit && hit.key === key) return hit;
  const cut = {key: key, n: len, xs: null, total: len, text: node ? node.nodeValue : ""};
  if (len > 0) {
    const r = document.createRange();
    const xs = [0];
    for (let k = 1; k <= len; k++) {
      r.setStart(node, k - 1);
      r.setEnd(node, k);
      xs.push(r.getBoundingClientRect().right);
    }
    // 折行会让右缘非单调（第二行回到左边）：逐字取累计最大值，折行处的字仍按
    // 顺序点亮，进度退化成按字宽加权的均匀分布——仍是时间与宽度的纯函数。
    const base = xs[0];
    for (let k = 0; k <= len; k++) {
      xs[k] -= base;
      if (k > 0 && xs[k] < xs[k - 1]) xs[k] = xs[k - 1];
    }
    cut.xs = xs;
    cut.total = Math.max(1, xs[len]);
  }
  cut.text = node ? node.nodeValue : "";
  verseEmCache.set(el, cut);
  return cut;
};
// 覆盖层的横向原点：它的盒子铺满**行盒**（负边距抵消行内边距，见 composition.css），
// 所以文字原点在行的左缘，而正文首字可能因为说话人标签 / 首行缩进往右让开一段。
// 只在覆盖层**首次上屏前**量一次，写成 px padding（--ctv-ved-dx），此后每帧零成本；
// 字体就绪会清 verseEmCache，那时才允许重量（L.vedDx === undefined 即未量）。
const verseVedOrigin = (L) => {
  if (L.vedDx !== undefined) return L;
  const node = verseEmNode(L.el);
  L.vedDx = 0;
  if (node && node.nodeValue.length) {
    const rg = document.createRange();
    rg.setStart(node, 0);
    rg.setEnd(node, 1);
    const dx = rg.getBoundingClientRect().left - L.el.getBoundingClientRect().left;
    if (isFinite(dx) && dx > 0.5) L.vedDx = Math.round(dx * 10) / 10;
  }
  if (L.ved) L.ved.style.setProperty("--ctv-ved-dx", L.vedDx + "px");
  return L;
};
// ── 覆盖层写什么：**整句原文，逐字上 alpha** ───────────────────────────────
// 覆盖层必须与正文**逐字重合**，否则就是重影。能保证重合的只有一件事：让它装的
// 字和正文一模一样、走的行盒一模一样——于是折行位置、缩进、字距全部由浏览器算出
// 同一份答案，一个像素都不用换算。
//
// 反面教材（别再走回头路）：写 slice(k)「还没念到的那截」，让它自己从头起排。
// 单行时不缩进的句子看起来没问题，一旦跨行就塌：前 k 个字被拿掉，折行点全部前移，
// 覆盖层第 j 个字与正文第 k+j 个字只对齐第一个，之后按"缺掉的前缀宽度"逐渐错开
// （实测 13 字句的第 8 字起差出半个字，同一句里两个字叠着画）。
// "裁掉 k 个 em"同理：它假设被裁掉的部分恰好占 k 个等宽格，中英混排、标点、字母
// 都不成立，误差随 k 累积。
//
// 所以：覆盖层整句写满，已经念过的字给 alpha 0（彻底不画，不会与正文的 accent 色
// 抢同一格），还没念到的字给 dim（压暗前缀）。无 clip-path、无 em 书签、无原点换算
// ——这三样全是重影的来源，一并删掉。
//
// 逐字用 <span> 包起来只在覆盖层里发生：它是 aria-hidden、不参与布局的附加物，
// Layout 门禁只看正文的裸文本节点，不受影响（正文必须保持裸文本的原因见
// composition.css）。缓存 span 列表，每帧只改 style.opacity。
//
// 覆盖层是**实色**（文本色按 0.62 混到底色上，见 composition.css），不是半透明：
// 活动行的正文整句都是 accent，半透明浅色压不住它。所以这里的 alpha 只有两档：
// 已念 0（彻底不画，露出正文的 accent）、未念 1（实色盖住 accent，观感等于 base 档）。
const verseVedLit = "0";
const verseVedDim = "1";
const verseVedGlyphs = (L) => {
  const text = L.ved ? (L.ved.getAttribute("data-text") || "") : "";
  if (L.vedSpans && L.vedText === text) return L.vedSpans;
  L.vedText = text;
  L.vedSpans = [];
  if (L.ved) {
    L.ved.textContent = "";
    const frag = document.createDocumentFragment();
    for (const ch of text) {
      const sp = document.createElement("span");
      sp.textContent = ch;
      frag.appendChild(sp);
      L.vedSpans.push(sp);
    }
    L.ved.appendChild(frag);
  }
  return L.vedSpans;
};
// 只改"刚刚跨过"的那几个字：k 每帧只前进零点几个字，全量刷会让样式改写量随句长
// 线性增长。L.vedDone 记已经处理到第几个字（每句 reset，仍是纯函数派生量）。
const verseVedPaint = (L, k) => {
  const spans = verseVedGlyphs(L);
  const from = Math.min(L.vedDone, k);
  const to = Math.max(L.vedDone, k);
  for (let i = from; i < to && i < spans.length; i++) {
    spans[i].style.opacity = i < k ? "0" : verseVedDim;
  }
  L.vedDone = k;
};
// 覆盖层压暗的是"还没念到的字"，而**字在屏幕上的位置**由正文决定，不由覆盖层的
// 字符序号决定——一条字幕句可能被拆成多行 .verse-line 渲染（生成期的分行），
// 覆盖层里的第 j 个字与正文第 j 个字不一定在同一行上。所以映射按**像素**建：
// 正文里每个字的中心点若落在覆盖层某个字的盒子里，这两个字就是同一个字。
//
// 映射只测一次（字体就绪后随 vedBg 清空），存成"正文序号 → 覆盖层序号"的数组。
// 逐字测一次 range 是 O(n)，只在上屏首帧付；之后每帧只做 O(n) 的查表。
const verseVedMap = (L) => {
  const cached = vedBg.get(L.el);
  if (cached) return cached;
  const node = verseEmNode(L.el);
  const spans = verseVedGlyphs(L);
  const map = [];
  if (node && node.nodeValue.length && spans.length) {
    const boxes = spans.map((sp) => sp.getBoundingClientRect());
    const rg = document.createRange();
    const n = node.nodeValue.length;
    for (let i = 0; i < n; i++) {
      rg.setStart(node, i);
      rg.setEnd(node, i + 1);
      const r = rg.getBoundingClientRect();
      map.push(-1);
      if (!r.width && !r.height) continue;
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      for (let j = 0; j < boxes.length; j++) {
        const b = boxes[j];
        if (cx >= b.left && cx <= b.right && cy >= b.top && cy <= b.bottom) {
          map[i] = j;
          break;
        }
      }
    }
  }
  vedBg.set(L.el, map);
  return map;
};
// 把"已念到正文第 k 个字"翻译成"覆盖层哪些字该灭"。未命中的正文序号跳过——
// 它的可见像素由相邻那格承担，不影响观感。
// 先 verseVedGlyphs 再 verseVedMap：前者负责把 data-text 拆成 span（首次上屏
// 时建好），后者才有盒子可量。顺序颠倒会让 spans 还是空的、整层永远不亮。
const verseVedApply = (L, k) => {
  const spans = verseVedGlyphs(L);
  if (!spans.length) return;
  const map = verseVedMap(L);
  const off = [];
  for (let i = 0; i < map.length; i++) {
    const j = map[i];
    if (j >= 0) off[j] = i < k;
  }
  for (let j = 0; j < spans.length; j++) {
    spans[j].style.opacity = off[j] ? verseVedLit : verseVedDim;
  }
};
const verseEmAt = (el, p) => {
  const c = verseEmClip(el);
  if (!c.n) return 0;
  if (!c.xs) return Math.round(p * c.n);
  const x = p * c.total;
  let k = 0;
  while (k < c.n && c.xs[k + 1] <= x) k++;
  return k;
};
// ── 已念段的画法：**正文不动 + 绝对定位的一层覆盖物** ─────────────────────
// 拆成「已念 span + 未念 span」也能染色，但会把一句拆成两个含文字的叶子，而
// Layout 门禁按叶子逐个量重叠：滚出窗口的行 rect 仍在原位，静态内容段会成批
// 报 text_occluded（实测一出现就报）。所以正文保持一个裸文本节点，另起一层
// .verse-ved 复制整句原文、把未念到的字压暗——露出来的已念段就是行的 accent 色。
// 覆盖物绝对定位、不占布局，不影响行高、折行与滚动锚点。
const verseEmClear = (L) => {
  if (L.lastK === -1) return;
  L.lastK = -1;
  L.el.classList.remove("verse-ved-on", "verse-ved-done");
  if (L.vedSpans) for (const sp of L.vedSpans) sp.style.opacity = "";
};
// 念完这句的时刻：**发声**长度定，不是"到下一句开口"那一刻。
// 没有 data-voiced（老 cue / 手写 manifest）时退回 t1——旧行为逐字节不变。
// 上限夹在 t1：发声长度是量出来的，理论上不会越过下一句，但手改过的 manifest
// 可能把 voiced 写大，那时宁可让进度提前走满，也别把高亮拖到下一句里。
const verseEmEnd = (L) => (L.voiced > 0 ? Math.min(L.t1, L.t0 + L.voiced) : L.t1);
const verseEmUpdate = (c, si, t) => {
  for (const L of c.lines) {
    if (!L.em) continue;
    const el = L.el;
    const on = parseInt(el.getAttribute("data-i"), 10) === si
      && isFinite(L.t0) && isFinite(L.t1) && L.t1 > L.t0;
    if (!on) { verseEmClear(L); continue; }
    const p = Math.max(0, Math.min(1, (t - L.t0) / (verseEmEnd(L) - L.t0)));
    const k = verseEmAt(el, p);
    if (k === L.lastK) continue;
    L.lastK = k;
    // 先上屏（display:block 之后才有盒子可测），再对原点、再逐字上 alpha。
    el.classList.add("verse-ved-on");
    verseVedOrigin(L);
    verseVedApply(L, k);
    el.classList.toggle("verse-ved-done", k >= verseEmClip(el).n);
  }
};
// ── 时间轴"这一刻"从哪来 ─────────────────────────────────────────────────
// 逐帧渲染（截图档）**只**把 tl.time() 拨到帧时刻、不回调 onUpdate，回调只在该
// 帧时间线内部真正跨过某个 tween 边界时才触发。于是任何"只在 onUpdate 里改
// 字幕状态"的写法在成片里都是散的：跳到一句中间时，活跃行是上一句留下的、书签
// 停在上一句的位置（实测 t=4.4s 整句仍是 accent 色）。
//
// 所以字幕链路分两半：
//   1. **cue 切换**（哪一句 active）——挂一段零长 tween 组成的时钟轨（见下
//      buildCueTrack）。时钟轨在渲染开关打开时把每一句的起点钉在帧网格上，且每
//      **帧**都必定被跨过一次，回调因此每帧必到；两条时间线互为"保险丝"（开关
//      被误关、边界被并帧），谁报的时刻新就用谁。
//   2. **句内书签**——每次收到时刻都重算（见 verseApply），不靠"又在同一句里"
//      这个判断。句内仍是纯函数：只有 (t, t0, t1, 实测字宽) 四个输入。
// 时钟锚：回调触发时 tl.time() 可能已被更晚的 seek 拨走（帧异步执行），所以先
// 试 gsap.globalTimeline.time()——逐帧渲染下它是**渲染器当前这一帧**的时刻；
// 它早于刚收到的时刻、或拿不到时退回收到的时刻。
const cueClock = {t: -1};
let cueSi = -1;
// 时钟轨的时间位：按这个上限铺满全片（句末 gap 里也要有回调，否则跨句时上一句
// 的书签会冻在句尾——gap 里该维持"整句已念完"）
const cueSpan = () => (cues.length ? cues[cues.length - 1].t + Math.max(cues[cues.length - 1].d, 0.01) : 0);
const verseApply = (si) => {
  cueSi = si;
  for (const v of verses) {
    const c = verseCacheOf(v);
    let act = null;
    for (const L of c.lines) {
      if (parseInt(L.el.getAttribute("data-i"), 10) === si) { act = L; break; }
    }
    for (const L of c.lines) {
      const di = parseInt(L.el.getAttribute("data-i"), 10);
      L.el.classList.toggle("active", L === act);
      L.el.classList.toggle("past", !!act && di < si);
      // 当前句吃段落 accent（字重恒定，避免 500/700 切换时字形宽度跳变）；
      // 非活动行恢复主题色。换色/换透明度必须是**瞬时**的：过渡只在人工预览里
      // 由 preview.js 注入，成片每一帧都得等于时间线时刻（见 composition.css）。
      L.el.style.color = (L === act && c.accent) ? c.accent : "";
    }
    if (!act) continue;
    // 两画幅里不滚动的那一侧 clip 比窗口矮，整体不滚
    c.clip.style.transform = "translateY(" + (c.clipH <= c.winH ? 0
      : Math.max(c.winH - c.clipH, Math.min(0, __CTV_VERSE_CLIP__ - act.top))) + "px)";
    // 换句接力的起点：这一句第一次成为当前句的时刻（= 它的 cue 起点）。
    c.relayT = _cueStart(si);
  }
};
// 换句接力：新句落位的那一小段行程里淡入，而不是"啪"地到位。
//
// 滚动是父级 .verse-clip 的 translateY（整行平移），新句在平移开始前就在窗口
// 外、平移结束时正好落位。给它补一次"从略暗到位"的 alpha，读起来是上一句被
// 推走、下一句接着上来，而不是硬切。
//
// 四条约束（逐帧渲染逼出来的）：
//   1. **时间 t 的纯函数**：进度只由 (t − 本句 cue 起点) 算，不记"上一帧到哪"，
//      乱序 seek 也幂等。
//   2. **只动 opacity，不碰 transform**：滚动由 .verse-clip 独占；行上再写
//      transform 会与滚动叠加成错位。
//   3. **只给"当前行"与"未来行"**：past 行有自己的 0.45 档，掺进接力会让
//      "念过"和"刚到"两种暗法撞在一起，读不出区别。
//   4. **结束后清掉内联值**：让 CSS 的 active/past 档位重新接管，否则内联
//      opacity 会永久压住 active 的 1.0。
const RELAY_SECONDS = 0.32;
const _cueStart = (si) => {
  for (let i = 0; i < cues.length; i++) if (cues[i].si === si) return cues[i].t;
  return NaN;
};
const verseRelay = (c, t) => {
  const t0 = c.relayT;
  if (!isFinite(t0)) return;
  const p = Math.max(0, Math.min(1, (t - t0) / RELAY_SECONDS));
  for (const L of c.lines) {
    const past = L.el.classList.contains("past");
    const on = (p < 1) && !past;
    if (!on) {
      if (L.relaySet) { L.el.style.removeProperty("--ctv-relay"); L.relaySet = false; }
      continue;
    }
    // 起点 0.35 ≈ past 档（0.45）之下一点，落位瞬间"几乎还没亮"，然后追上 1
    L.el.style.setProperty("--ctv-relay", (0.35 + 0.65 * p).toFixed(3));
    L.relaySet = true;
  }
};
const verseTick = (t) => {
  // 只看当前 cue：句间 gap 里 si 保持最后一句（整句已念完、窗口不回滚）
  let found = -1;
  for (let i = 0; i < cues.length; i++) {
    if (t >= cues[i].t && t < cues[i].t + Math.max(cues[i].d, 0.01)) { found = i; break; }
  }
  if (found < 0) {
    // 片头/片尾的 lead-in 与 gap：沿用最后一句，但把它的 data-t1 时间之上的状态
    // 钉死在句尾——p 会随 t 继续涨，k 早已到 n，结果与句尾一致。
    if (cueSi < 0) return;
    for (const v of verses) {
      const c = verseCacheOf(v);
      verseEmUpdate(c, cueSi, 1e9);
      verseRelay(c, 1e9);
    }
    return;
  }
  if (cues[found].si !== cueSi) verseApply(cues[found].si);
  for (const v of verses) {
    const c = verseCacheOf(v);
    verseEmUpdate(c, cueSi, t);
    verseRelay(c, t);
  }
};
const verseSync = (t, t2) => {
  // 时钟锚：优先全局时间线（= 当前渲染帧），它不比自己收到的时刻早就用它
  let tt = t;
  try {
    const g = gsap.globalTimeline.time();
    if (isFinite(g) && g + 1e-4 >= t) tt = g;
  } catch (e) {}
  if (t2 !== undefined && isFinite(t2) && t2 + 1e-4 >= t) tt = Math.max(tt, t2);
  // 首帧（cueSi 还没定）必须放行：渲染器从 0 之外任意时刻开抓时，第一个回调
  // 就落在某句中间，这里若因"si 未定"直接返回，整片字幕都不会出现
  if (!isFinite(tt) || Math.abs(tt - cueClock.t) < 1e-4) return;
  cueClock.t = tt;
  verseTick(tt);
};
// ── 时钟轨：让"每帧都会有一次回调"成为结构保证，而不是靠渲染器的实现细节 ──
// 一段只有零长 tween、没有可见属性的空轨，层级放在所有视觉补间**之上**（层级
// 只影响渲染次序，空轨什么也不画）。
//
// 打点的密度决定字幕能不能逐帧推进：onUpdate 只在 seek **跨过某个 tween 边界**
// 时才被调用。只在 cue 起止处打点 = 一句之内只有两帧会更新，实测成片里
// 3.6s→5.9s 六帧全是整句亮蓝、句内书签一动不动。所以在**每个帧网格**上打点：
// 渲染器 seek 的时刻本就落在网格上，于是每一帧都正好跨过一个时钟位，onUpdate
// 每帧必到。
//
// 为什么不再拿渲染开关（window.__HF_EXPORT_RENDER_SEEK_CONFIG）来决定铺不铺：
// 开关的可见时机取决于渲染器在哪一步注入页面脚本，靠不住——关掉时静默退化成
// "只有 cue 边界"，而那种退化在预览里看不出来、只在成片里暴露（书签冻住）。
// 一条 12fps / 20s 的片子也就 240 个零长 tween，开销可以忽略；两档行为一致，
// 预览看到的推进节奏与成片逐帧一致，这才是更值钱的。
const CUES_FPS = parseFloat("__CTV_FPS__") || 30;
const buildCueTrack = () => {
  const span = cueSpan();
  if (!(span > 0)) return;
  const step = 1 / CUES_FPS;
  const ts = [];
  const end = span + step;
  for (let i = 0; i * step <= end + 1e-6; i++) ts.push(Math.round(i * step * 1e4) / 1e4);
  for (const c of cues) {
    // cue 边界补一遍：cue 的 t 未必正好落在帧网格上，而"换句"必须准时
    ts.push(Math.round(c.t * 1e4) / 1e4);
    ts.push(Math.round((c.t + Math.max(c.d, 0.01)) * 1e4) / 1e4);
  }
  const uniq = ts.filter((x, i, a) => isFinite(x) && x >= 0 && a.indexOf(x) === i).sort((a, b) => a - b);
  const clock = gsap.timeline({paused: true});
  for (const t of uniq) clock.to({}, {duration: 0}, t);
  tl.add(clock, 0);
};
buildCueTrack();
tl.eventCallback("onUpdate", function() {
  verseSync(tl.time());
  if (window.__pvUpdate) window.__pvUpdate(); // 预览刷新钩子（无预览时为空）
});
gsap.ticker.add(() => { verseSync(tl.time(), tl.time()); });
// 注册放脚本末尾（同步执行流内，行为与紧跟构建后注册等价）：hyperframes 的
// lint 规则按源码位置判定"注册是否早于 document.fonts.ready 异步构建"
// （window.__timelines[ 出现点必须在 fonts.ready 之后）；字幕 JS 里
// Array.from 的 ".from(" 会被规则的 GSAP 补间正则误判，注册线在 fonts.ready
// 之前就会触发 gsap_timeline_registered_before_async_build 误报。
window.__timelines["__CTV_COMPOSITION_ID__"] = tl;

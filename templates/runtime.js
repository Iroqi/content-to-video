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
// 行的全局句序（= data-i）：档位距离 d 与"上一句"都由它推，定义在最早处是为了
// 让下面所有消费者都在它**之后**——const 有 TDZ，定义晚于调用点会静默炸掉。
const _idx = (L) => parseInt(L.el.getAttribute("data-i"), 10);
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
      // 有没有可强调的正文（生成期在行尾放的挂点，见 html_renderer._em_marker）。
      // 收束也挂在它上面：没有挂点就没有逐字层，也就没有可退的 accent。
      em: ln.querySelector(".verse-em") !== null,
      // 覆盖层元素（生成期就位的空盒子，见 html_renderer._ved_layer）
      ved: ln.querySelector(".verse-ved"),
      lastK: -1,
      // 收束是否已经演过（一次性）：清场时一起复位，往回 seek 到句尾会重演，
      // 与"cue 切回来重播词级强调"同一口径。
      settled: false});
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
    const xs = [];
    for (let k = 1; k <= len; k++) {
      r.setStart(node, k - 1);
      r.setEnd(node, k);
      const box = r.getBoundingClientRect();
      if (k === 1) xs.push(box.left);
      xs.push(box.right);
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
    const rEl = L.el.getBoundingClientRect();
    const dx = rg.getBoundingClientRect().left - rEl.left;
    // 行此刻多半正被焦点/接力的 scale 缩着，而 getBoundingClientRect 给的是
    // **变换后**的几何。要的是布局值：覆盖层是行的子元素、与行同一个 scale，
    // 两边同比例缩放，按布局值写才对得上。变换后宽 ÷ 布局宽 = 当前 scale，
    // 除回去即可——纯派生量，不新增状态（缓存被字体就绪清掉时照样算得出）。
    // 不除的后果：对话段首句的说话人标签段（~60px）被少算 6%，覆盖层整体左移
    // 3~4px，在 40px 字号上是看得见的横向重影。
    const w = L.el.offsetWidth;
    const s = (w && rEl.width) ? rEl.width / w : 1;
    const d0 = isFinite(dx) ? dx / s : 0;
    if (d0 > 0.5) L.vedDx = Math.round(d0 * 10) / 10;
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
// 收束态：已念的字从 accent 交还覆盖层实色。数值与 CSS 的
// `.verse-ved-settled ... {opacity:.62}` 同值（内联会盖住 CSS，两边必须一致）。
const verseVedSettled = "0.62";
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
// ── 读取头：**亚字精度** ────────────────────────────────────────────────
// 句内进度 p 是**连续量**，但此前只用它的整数部分：k 取到整字之后，一个字要等这一格
// 走完才整字数——portrait 40px 字号下一个 CJK 字约 40px 宽，句内匀速约 0.15~0.2s 一
// 格，于是 30fps 下**四到六帧一动不动、第七帧平移 39.4px**（实测 portrait golden 逐
// 帧读覆盖层的逐字 alpha）。更致命的是正在念的那个字 alpha 恒为 1：边缘只有"还没翻"
// 和"翻过去了"两种状态，从来没有"正在翻开"。
//
// 前沿歌词 UI（AMLL 的逐字模式、Apple Music 的词级同步）的高亮边缘都是**连续的**：
// 前者按词级时间戳、后者按词的发声区间推进，两者都没有"取整到整字"这一步。本仓库的
// TTS 只有句级时间戳，"句内匀速"确实是唯一可用的假设——但**匀速推不出取整**：把 p
// floor 成整数是自己加的一层量化器，不是时间戳缺失的推论。留着它，等于把句内这条
// 连续量里最容易拿到的那一段信息白白扔掉。
//
// 所以把 floor 这一层拿掉：整数部分 k 照旧决定"哪些字已经念完"（O(n) 的全量刷仍只在
// 跨格那一帧），多出来的零头 f 全部落在**正在念的那一个字**上——覆盖层的 alpha 从 1
// 收到 1−f。于是每帧边缘都在走，读取头不再有驻点。插值在**实测字宽**（xs）上进行，
// 不假设等宽：中英混排、标点、折行都仍由 xs 说了算。
const verseHeadAt = (el, p) => {
  const c = verseEmClip(el);
  if (!c.n) return {k: 0, f: 1};
  // 没有实测字宽就没有可插值的度量：零头按 0 处理（这一格仍整格压着，与改动前逐
  // 字节一致）。不敢猜"已经念了一半"——猜过了就是把还没念的字先亮出来。
  if (!c.xs) return {k: Math.round(p * c.n), f: 0};
  const x = p * c.total;
  let k = 0;
  while (k < c.n && c.xs[k + 1] <= x) k++;
  if (k >= c.n) return {k: c.n, f: 1};
  // 折行处那一格被累计最大值压成零宽（见 verseEmClip）：它没有零头可分，整格亮。
  const w = c.xs[k + 1] - c.xs[k];
  if (!(w > 0)) return {k: k, f: 1};
  return {k: k, f: Math.max(0, Math.min(1, (x - c.xs[k]) / w))};
};
// 亚字那一位：**只重写正在念的那一个字**——它是全句唯一每帧都变的格子，其余字在
// 跨格那一帧已被 verseVedApply 写成终值、到这句念完前都不再动。
// alpha = 1 − f：f=0 时这一格仍整格压着（= verseVedDim），f=1 时整格让出（=
// verseVedLit），中间的位置就是"笔锋正跋在这一格上"。三位小数精度足够了——一个
// 40px 的字分成 1000 档是 0.04px，远低于栅格能分辨的量。
const verseEdgeSet = (L, k, f) => {
  const spans = L.vedSpans || [];
  if (!spans.length) return;
  const map = verseVedMap(L);
  if (k >= map.length) return;                   // 整句已念完：没有"正在念"的字
  const j = map[k];
  // 正文第 k 个字没在覆盖层里落到格子（换行吞掉的空白、测不着的盒）：它的可见像素
  // 由相邻那格承担（与 verseVedApply 同一条口径）。
  if (!(j >= 0) || j >= spans.length) return;
  const sp = spans[j];
  // 值不变就不写，与 runtime 里其它逐帧变量同一条守卫写法：整数部分的那些字在稳态
  // 时若跟着每帧重写一遍，白触发一次样式重算——这一位是唯一每帧必写的，别扩散到全句。
  const v = f >= 1 ? verseVedLit : (1 - f).toFixed(3);
  if (sp.style.opacity !== v) sp.style.opacity = v;
};
// ── 已念段的画法：**正文不动 + 绝对定位的一层覆盖物** ─────────────────────
// 拆成「已念 span + 未念 span」也能染色，但会把一句拆成两个含文字的叶子，而
// Layout 门禁按叶子逐个量重叠：滚出窗口的行 rect 仍在原位，静态内容段会成批
// 报 text_occluded（实测一出现就报）。所以正文保持一个裸文本节点，另起一层
// .verse-ved 复制整句原文、把未念到的字压暗——露出来的已念段就是行的 accent 色。
// 覆盖物绝对定位、不占布局，不影响行高、折行与滚动锚点。
const verseEmClear = (L) => {
  // 不设"lastK === -1 就早退"的守卫（曾经有，删了）：字体就绪清缓存会把 lastK
  // 归 −1，而行上可能还挂着上一份缓存时代写下的 verse-ved-on / settled 类——
  // 早退会让这些类永远没人摘（症状：已过的行留着覆盖层，颜色对不齐）。全量重置
  // 是幂等空操作，每帧多花的只是几个属性赋值。
  L.lastK = -1;
  L.settled = false;
  L.el.classList.remove("verse-ved-on", "verse-ved-settled");
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
    // 念到句尾那一刻做一次收束（见 verseEmSettle）。判据是**时间**、且必须写在
    // 下面那道"跨格才刷"的门**之前**：k 是取整后的字序号，最后不足一个字的行程里
    // k 已经停在 n 不动，门一关这条判据就被整个跳过——收束永远不触发，且没有任何
    // 报错（踩过）。settle 自身幂等，每帧判一次代价为零。
    if (t >= verseEmEnd(L) - 1e-6) verseEmSettle(L);
    const p = Math.max(0, Math.min(1, (t - L.t0) / (verseEmEnd(L) - L.t0)));
    const h = verseHeadAt(el, p);
    const k = h.k;
    // 整数部分照旧"跨格才刷"：O(n) 的全量覆盖只发生在换格的那一帧。
    // 亚字那一位**不进这道门**——它本来就是每帧都变的那一位，关进门里就退回按字跳格。
    if (k !== L.lastK) {
      L.lastK = k;
      // 先上屏（display:block 之后才有盒子可测），再对原点、再逐字上 alpha。
      el.classList.add("verse-ved-on");
      verseVedOrigin(L);
      verseVedApply(L, k);
    }
    verseEdgeSet(L, k, h.f);
  }
};
// ── 念完之后的收束（settle）：把整句的 accent 交还给"下一句" ──────────────
// 词级强调的目标是"念到哪儿亮到哪儿"，但念完那一刻整句仍是全亮 accent，而它
// 与下一句刚亮起的第一个字**同色同重**——画面上同时有两个"现在的焦点"。前沿的
// 歌词/字幕收束（Apple Music 的逐行歌词、Spotify 同步歌词、卡拉OK 的 bar 收尾）
// 都在一句唱完时做一次回落：句内进度走满的同时整句退到"已过去"那一档，把焦点
// 让给下一句。
//
// 做法不是"整句一起压暗"（一整句在 0.3s 里同时变灰，读成闪烁），而是**逐字错峰
// 回落**：每个字按自己的序号从**句首**起依次交还覆盖层实色——于是笔画像被一支笔
// 从前往后盖过去，收在句尾。观感是"这句话被念过去了"，不是"这句话坏掉了"。
//
// 时间口径只有一处真源：整段回落恰好铺满"最后一句已念完 → 下一次换句"那段留白
// （生成期把 `--ctv-ved-settle` 按 cue 算出，见 html_renderer._settle_span 与
// composition.css）。留白不够时**整段退化为瞬间**（不演半截），
// 留白富余时上限封顶、不把回落拉成慢动作——两种退化都在生成期一次算完，运行时
// 只读这两个数，不做第二次判断。没有留白的最后一句因此保持纯 accent 到页尾，
// 与改动前逐字节一致。
const verseSettleMs = () => {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--ctv-ved-settle");
  return parseFloat(raw) || 0;
};
// 口径：整段回落铺满留白（见 _ved_settle_span 的推导），每字间隔 = 时长 ÷ 字数，
// 于是第一个字在留白开头、最后一个字在留白收尾退回。
const verseEmSettle = (L) => {
  if (L.settled) return;
  L.settled = true;
  // 收束标记挂上（逐字延迟也要算），时长是否为 0 只决定"会不会真演"：CSS 那边
  // 读到 0s 就是不演。这样"要不要演"只有一个判断点（生成期算出的那个数），
  // 运行时不再重复决定一次。
  if (verseSettleMs() <= 0) return;
  const spans = L.vedSpans || [];
  if (!spans.length) return;
  // 逐字错峰：第 i 个字在「句首 + i×(时长÷字数)」那一刻开始退。整段因此恰好铺满
  // 留白——第一个字在留白开头动，最后一个字在下一句开口前退回。
  const step = verseSettleMs() / spans.length;
  for (let i = 0; i < spans.length; i++) {
    const sp = spans[i];
    // 秒，三位小数：0.6s / 13 字 = 0.046s，`.toFixed(3)` 留得住；用两位小数会把
    // 短句的间隔全部压成 0，整句同时退，读成闪烁（踩过）。
    sp.style.transitionDelay = (i * step).toFixed(3) + "s";
    // 已念的字（alpha 0）才需要退；还没念到的字本来就压暗，保持不动
    if (sp.style.opacity === verseVedLit) sp.style.opacity = verseVedSettled;
  }
  L.el.classList.add("verse-ved-settled");
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
// 时钟锚：**只信主时间线的时刻**，两条通道（onUpdate 的 tl.time() 与 ticker 传
// 来的 tl.time()）同源，取较大者只为处理"回调晚于 ticker"的次序。
// 以前这里会先试 gsap.globalTimeline.time()（当时以为逐帧渲染下它就是"当前这一
// 帧"），但它是**墙钟**：页面打开后一直在涨，只要碰巧 ≥ 当前 seek 到的 t 就会被
// 当成帧时刻，于是短时间线的中后段字幕完全按墙钟跑（seek 到 1.40 却按 1.55 画，
// 收束在句尾之前触发）。
const cueClock = {t: -1};
let cueSi = -1;
// 时钟轨的时间位：按这个上限铺满全片（句末 gap 里也要有回调，否则跨句时上一句
// 的书签会冻在句尾——gap 里该维持"整句已念完"）
const cueSpan = () => (cues.length ? cues[cues.length - 1].t + Math.max(cues[cues.length - 1].d, 0.01) : 0);
// ── 焦点衰减（focus falloff）：句子流按"离当前句多远"决定亮度 ────────────
// 三档阶跃（1.0 / 0.45 / 0.62）的问题在"同档内一视同仁"：0.62 的未念墙里，观众
// 唯一找焦点的线索是最亮那行。前沿的歌词 UI（Apple Music 逐行、Spotify 同步）
// 都按**离焦距离**衰减——渐变本身就是路标。
//
// 曲线不是新发明：0.62 × 0.726 ≈ 0.45，"已念"本来就是"未念"沿同一条几何衰减
// 多走的一步，只是曲线一直被砍断在第二级。这里把它延长（真源在模板
// subtitle.focus，经 :root 的 --ctv-focus-* 注入；下面三个默认值是**同一组数**
// 的兜底抄写，两边必须一致——与 verseVedSettled 的内外两份同口径）：
//   未来侧 d=1→0.62、d=2→0.45、d≥3→0.327 封住；
//   过去侧整条提前一档：d=−1→0.45、d=−2→0.327、d≤−3→0.237。
// 两侧在 0.45 / 0.327 处无缝拼接——"念过的比没念的同距更暗一档"是曲线错位一档
// 的自然结果，不用特判。封在第三档：再远的行基本已滚出窗口。
//
// ── 第二个维度：尺寸 ────────────────────────────────────────────────────
// 前沿歌词 UI（Apple Music 逐行、Apple Music Like Lyrics 的 spring 动画）让层级
// 同时落在**三个**属性上：透明度、缩放、位置。这里只有一维，于是"远"只被表达成
// "暗"——同一档里第 2 行和第 4 行完全一样远。补上尺寸：远处的行不只更暗，也更小。
//
// 为什么必须是 transform 而不是 font-size：改字号会**重排**——折行点变、offsetTop
// 变（滚动锚点跟着漂，首句/尾句会抖）、覆盖层那份复制文本折到别的位置去，重影
// 随之而来（那正是覆盖层走过的弯路）。transform 不进布局：offsetTop、
// clientWidth、scrollHeight 全是原值，滚动公式与逐字命中测试一个字节都不用改。
//
// 两个维度用**同一条几何曲线**（每远一档乘一次 ratio），但各自的 ratio 与封顶不
// 同，因为失效边界不同：
//   · 亮度可以一直暗下去——暗到看不见就是"退场"本来的意思，所以封在第三档
//     （0.237 仍可见，只是不再有信息）；
//   · 尺寸不能一直缩下去——缩到读不出来就不是"退场"，是把字幕毁了。横屏字幕
//     33px 是最小的字号，最深那一档是过去侧的 0.94³=0.8306 → 27.4px，仍在可读
//     区（技能的可读下限是 26px）；再多封一档（0.94⁴）到 25.8px 就破线了。
//     所以**尺寸封在第二档**。
// 这条不对称是设计，不是遗漏：曲线的形状同源，深度由各自的物理下限决定。
let focusRows = null;
const focusTable = () => {
  if (focusRows) return focusRows;
  const cs = getComputedStyle(document.documentElement);
  const fRaw = parseFloat(cs.getPropertyValue("--ctv-focus-future"));
  const pRaw = parseFloat(cs.getPropertyValue("--ctv-focus-past"));
  const rRaw = parseFloat(cs.getPropertyValue("--ctv-focus-ratio"));
  const sRaw = parseFloat(cs.getPropertyValue("--ctv-focus-scale"));
  const bRaw = parseFloat(cs.getPropertyValue("--ctv-focus-blur"));
  const fut = isFinite(fRaw) && fRaw > 0 && fRaw <= 1 ? fRaw : 0.62;
  const past = isFinite(pRaw) && pRaw > 0 && pRaw <= fut ? pRaw : 0.45;
  const r = isFinite(rRaw) && rRaw > 0 && rRaw < 1 ? rRaw : past / fut;
  const s = isFinite(sRaw) && sRaw > 0 && sRaw <= 1 ? sRaw : 0.94;
  // 景深是**加法**不是乘法：每远一档多 blur 个 em（高斯半径按离焦距离线性增长，
  // 这才是景深的样子；照抄 ratio 那一档的乘法会得到"越远越不虚"，方向是反的）。
  // 0 = 关掉这一维（老产物没有这个变量时回退 0.02，与模板同值，由测试钉住）。
  const b = isFinite(bRaw) && bRaw >= 0 ? bRaw : 0.02;
  // 一条曲线的展开：[1, a, a·q, …]，长度 = 封顶档 + 1。尺寸那条错位一档的起点是
  // s·s（= 未来侧的第二档），与亮度用 past/fut 的错位同一种写法。
  const row = (a, q, n) => {
    const out = [1];
    for (let i = 0; i < n; i++) out.push(a * Math.pow(q, i));
    return out;
  };
  focusRows = {fut: row(fut, r, 3), past: row(past, r, 3),
               sfut: row(s, s, 2), spast: row(s * s, s, 2), blur: b};
  return focusRows;
};
// 档位有**两种取法**，缺一个就会把"换档"演成"跳档"：
//   · `focusOp(d)` —— 数值（d=0 → 1）。给换句窗口**插值**用：一行要从"上一句
//     那档"走到"这一句那档"，两端都得是数。
//   · `verseFocusOp(d)` —— 行内联写法（d=0 → ""＝撤内联，交还 CSS 的 active 档）。
//     给窗口结束后的**稳态**用。
// 以前只有后者，于是"上一句退场"只能把 0.45 一次性写死（实测一帧 1 → .45）。
const focusOp = (d) => {
  if (d === 0) return 1;
  const T = focusTable();
  const row = d < 0 ? T.past : T.fut;
  return row[Math.min(Math.abs(d), 3)];
};
const verseFocusOp = (d) => (d === 0 ? "" : focusOp(d).toFixed(3));
const focusScale = (d) => {
  if (d === 0) return 1;
  const T = focusTable();
  const row = d < 0 ? T.spast : T.sfut;
  return row[Math.min(Math.abs(d), 2)];
};
// 尺寸那一档，写法与上面同源；四位小数是留给 0.8836 这种两档相乘的值。
const verseFocusScale = (d) => (d === 0 ? "" : focusScale(d).toFixed(4));
// ── 第四个维度：景深（模糊）───────────────────────────────────────────────
// 前沿歌词 UI（AMLL 的 setEnableBlur、Jellyfin 那支“梯度模糊”）让离焦的行不只是
// 更暗更小，还**更虚**。这是"远"这个字在光学里的本义：前三维合起来表达的是强弱
// （暗 / 小 / 被推走），只有模糊表达的是"不在焦平面上"——观众判断"哪一行是现在"
// 靠的是锐度，锐度差比亮度差更早被眼睛抓到。
//
// 两个与前面几维不同的地方，都是有理由的：
//   · **加法而不是乘法**：每远一档多 blur 个 em。照抄 ratio 那种"每档乘一次"会
//     得到"越远越不虚"（0.02 → 0.0145 → 0.0105），方向是反的；景深随离焦距离
//     线性增长。
//   · **单位是 em**：相对该行自己的字号，于是竖屏 40px 与横屏 33px 各自按自己的
//     字号缩放，模板里只存一个数。
// 封顶**两档**（与尺寸同档数、比亮度少一档）：档数不是拍的，是生成期那条糊掉线
// 推出来的（floor(FOCUS_BLUR_MAX_EM ÷ blur)，见 _template.FOCUS_BLUR_MAX_EM），
// 所以这里写成常量、由测试钉住——改模板的 blur 不会被这里的档数悄悄放行到越线。
// 过去侧与亮度/尺寸同一种错位（提前一档），于是 d=−1 就已经是封顶值：念过的行
// 一律最虚。
const FOCUS_BLUR_RANK_CAP = 2;
const focusBlur = (d) => {
  if (d === 0) return 0;
  const b = focusTable().blur;
  if (!(b > 0)) return 0;              // 关掉景深：撤滤镜
  return b * Math.min(Math.abs(d) + (d < 0 ? 1 : 0), FOCUS_BLUR_RANK_CAP);
};
// 滤镜的开关与取值都在这一处合成，**不交给 CSS 的 max()**：那句只能写成
// `blur(max(var(--ctv-line-blur,0em),var(--ctv-relay-blur,0em)))`，而 blur(0em)
// 也是 filter——实测它把字形换成另一套栅格化（同一句 40px 白字：边缘拉普拉斯和
// 925410 → 994512、单像素最大差 80；对照 transform:scale(1) 与 none 逐像素相同）。
// 离焦行本来就要虚，无所谓；当前行是画面上最该锐利的那一句，为一个 0 半径的滤镜
// 把它的字形换一遍是纯视觉漂移，成片里看不出是谁干的。
//
// 地板（0.01em ≈ 40px 字幕的 0.4px）管的是另一件事：接力的末段模糊量一路收到 0，
// 最后一帧会同时发生"模糊归零 + 撤滤镜"——那次栅格化切换不再被任何模糊盖住。收
// 到地板就撤，切换被残存的 0.4px 模糊盖住。
const FOCUS_BLUR_MIN_EM = 0.01;
const verseBlurSet = (L, b) => {
  if (!(b > FOCUS_BLUR_MIN_EM)) {
    if (L.el.style.getPropertyValue("--ctv-line-filter") !== "")
      L.el.style.removeProperty("--ctv-line-filter");
    return;
  }
  // 值不变就不写：这一位每帧都会被 verseRelay 重新算一遍，稳态时（接力结束后）
  // 不该每帧都触发一次样式重算。与上面几个 relay 变量同一条守卫写法。
  const v = "blur(" + b.toFixed(4) + "em)";
  if (L.el.style.getPropertyValue("--ctv-line-filter") !== v)
    L.el.style.setProperty("--ctv-line-filter", v);
};
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
      // 焦点衰减：内联 --ctv-line-op 盖过 CSS 的 active/past 档；当前行与"本页
      // 没有活跃句"的 verse（别的卡被盖着，不需要层级）都撤内联、交还 CSS。
      const op = act ? verseFocusOp(di - si) : "";
      if (op === "") L.el.style.removeProperty("--ctv-line-op");
      else L.el.style.setProperty("--ctv-line-op", op);
      // 尺寸维度同一口径：换句时才变，所以同样只在 verseApply 里写。
      const sc = act ? verseFocusScale(di - si) : "";
      if (sc === "") L.el.style.removeProperty("--ctv-line-scale");
      else L.el.style.setProperty("--ctv-line-scale", sc);
      // 景深维度同一口径：换句时才变，所以同样只在 verseApply 里写（接力的那一
      // 份由 verseRelay 每帧补，两边取大者——合成在 verseBlurSet 里）。
      verseBlurSet(L, act ? focusBlur(di - si) : 0);
    }
    // 位移**不在这里写**：换句那一刻只写一次的话，滚动是硬跳（实测一帧从 0
    // 跳到 −70px），而同一次换句的亮度与尺寸都在 0.32s 里柔和到位——三个维度
    // 里只有位置这一维是硬的。滚动改由 verseScroll 每帧插值（见下）。
    // 这里写的三档是**稳态目标值**：窗口内由 verseRelay 逐帧覆盖成插值中途的值，
    // 窗口结束（p≥1）relay 写回同一个数，别页（无活跃句）撤内联交还 CSS 档位。
    // 三者同一个数来源（focusTable），只有一处真源。
  }
};
// 换句接力：新句落位的那一小段行程里淡入，而不是"啪"地到位。
//
// 滚动是父级 .verse-clip 的 translateY（整行平移），新句在平移开始前就在窗口
// 外、平移结束时正好落位。给它补一次"从略暗到位"的 alpha，读起来是上一句被
// 推走、下一句接着上来，而不是硬切。
//
// 五条约束（逐帧渲染逼出来的）：
//   1. **时间 t 的纯函数**：进度只由 (t − 本句 cue 起点) 算，不记"上一帧到哪"，
//      乱序 seek 也幂等。起点由 verseTick 传进来（就是当前 cue 的 t）——曾经把它
//      缓存在 verse 缓存对象上，但字体就绪会整表清空那份缓存，relayT 丢了、行上
//      已写出的内联值成了没人管的孤儿：开场句整句卡在接力起点 0.35，比未念档还
//      暗（实测）。**缓存可以清，状态必须能从 t 重推**。
//   2. **"这行上的内联值是不是我写的"看行本身**（getPropertyValue 非空），不另记
//      布尔——那个布尔躺同一张被清的表里，丢了同样留下孤儿内联值。
//   3. **位移仍归 .verse-clip 独占**：它的 translateY 按 offsetTop 算落位，行上
//      再叠 translate 会把行推离锚点。**scale 不受这条限制**——它不进布局，
//      offsetTop 还是原值，锚点读到的仍是未缩放的位置（尺寸维度能加进来正是
//      因为这个，见 focusTable 旁那条注释）。
//   4. **入场浮出只给当前行**：它读的是"焦点换了地方"这个事件，只有新成为焦点的
//      那一行配得上；其余行走的是「档位插值」（见下第五维），从上一句那档柔和走到
//      这一句那档。以前这枚淡入是**所有非 past 行共用**一枚的，于是离焦距离根本
//      没变的行也要跟着从 RELAY_FROM 浮起——每次换句整墙字幕闪一下（实测
//      .45 → .35 → .45）。别的 verse（被盖住的页）整表不参与，显式判 mine。
//   5. **结束后清掉浮出那两枚内联值**：让焦点档位重新接管，否则内联
//      opacity 会永久压住 active 的 1.0。
// 换句接力的入场时长（秒）：真源是模板 layout.<画幅>.subtitle.relay，生成期写成
// :root 的 --ctv-relay-s。这里不抄数字——抄了就得靠测试盯两份相等，而生成期的
// 收束留白也是从同一个变量算的（两边不同步＝收束和接力叠着演）。
const RELAY_SECONDS = parseFloat(
  getComputedStyle(document.documentElement).getPropertyValue("--ctv-relay-s")) || 0.32;
// 入场浮出的起点（**只叠给当前行**，理由见 verseRelay 的长注释）：新成为焦点的
// 那一行从这一档长到 1。真源是模板 `subtitle.relay.from`，生成期经
// --ctv-relay-from 注入（与 --ctv-relay-s / --ctv-scroll-ease 同一口径），
// 下面的兜底值必须与模板同值——两边不同步＝改了一处、成片里演的是另一条曲线。
const _relayRaw = parseFloat(getComputedStyle(document.documentElement)
  .getPropertyValue("--ctv-relay-from"));
const RELAY_FROM = isFinite(_relayRaw) ? Math.min(Math.max(_relayRaw, 0), 0.99) : 0.35;
const _cueStart = (si) => {
  for (let i = 0; i < cues.length; i++) if (cues[i].si === si) return cues[i].t;
  return NaN;
};
// ── 焦点层级的第三个维度：位置（滚动）─────────────────────────────────
//
// 前沿歌词 UI（Apple Music 逐行歌词、AMLL 的 LyricPlayer）的 spring 同时管
// **垂直位置、缩放、透明度**三个属性——行不是"啪地换掉"的，是被推着上去的。
// 此前本仓库只做了两维（亮度、尺寸），滚动那一维是硬跳：换句那一帧 clip 的
// translateY 直接写死到新落位（实测 0 → −70px 一帧到位），而同一次换句的亮度
// 与尺寸都在一个接力窗口里柔和到位——三个维度里只有位置这一维是硬的。
//
// 三条纪律（与 verseRelay 同源，逐帧渲染逼出来的）：
//   1. **时间 t 的纯函数**：插值进度只由 (t − 本句 cue 起点) 算，不记"上一帧
//      滚到哪"，乱序 seek 幂等。起点由 verseTick 传进来（当前 cue 的 t）。
//   2. **不新增任何缓存字段**：起点与终点都由**静态布局值**重推（行的 offsetTop、
//      窗口高、clip 高）——缓存被字体就绪清掉后照样算得出。
//   3. **位移仍归 .verse-clip 独占**：行上只允许 scale，行上再叠位移会把行推离
//      锚点（同 focusTable 旁那条注释）。
//
// 曲线取钉固 GSAP 自己的档（默认 sine.out：减速停稳、不过冲，落点精确回到布局
// 值），不在运行时另抄一份公式：抄了就得靠测试盯两份相等，而 GSAP 若哪天不认
// 这个档名会**静默**回落到缺省缓动——那正是"演的不是你写的"那条无声退化。
//
// 档名本身也**不在这里写死**：真源是模板 subtitle.scroll.ease，生成期校验后经
// --ctv-scroll-ease 注入（与 --ctv-relay-s 同一口径）。下面的兜底值必须与模板
// 同值——两边不同步＝改了一处、成片里演的是另一条曲线，且没有任何报错。
const SCROLL_EASE_NAME = (getComputedStyle(document.documentElement)
  .getPropertyValue("--ctv-scroll-ease") || "").trim() || "sine.out";
const SCROLL_EASE = gsap.parseEase(SCROLL_EASE_NAME);
if (typeof SCROLL_EASE !== "function")
  throw new Error("GSAP 不认 " + SCROLL_EASE_NAME + "：滚动曲线会静默退化成缺省缓动");
// 同段里"上一句"的行（段内句子 index 连续，取小于当前句的最大者）。
// 没有上一句时（段首第一句）返回 null：那时没有"推"的出发点，落位就是终点，
// 不制造一段无来由的位移——滚动与档位插值共用这一条口径。
const versePrev = (c, si) => {
  let best = null, bestDi = -Infinity;
  for (const L of c.lines) {
    const di = _idx(L);
    if (di < si && di > bestDi) { best = L; bestDi = di; }
  }
  return best;
};
// 某一行落在窗口里时，clip 该有的 translateY。装得下（clip 比窗口矮）就整体不滚。
const verseClipY = (c, L) => (c.clipH <= c.winH ? 0
  : Math.max(c.winH - c.clipH, Math.min(0, __CTV_VERSE_CLIP__ - L.top)));
// 滚动插值：from = 同段上一句的落位，to = 当前句的落位，p 与亮度/尺寸共用同一个
// 接力窗口——三个维度同一时刻到位，"焦点"不能在途中分裂成"先滚、再亮"两个动作。
const verseScroll = (c, t, t0, si) => {
  if (!isFinite(t0)) return;
  let act = null;
  for (const L of c.lines) if (_idx(L) === si) { act = L; break; }
  if (!act) return;              // 这一段没有当前句：它的落位保持不动
  const to = verseClipY(c, act);
  // 本段第一句：没有"上一句"可推，落位就是终点（不制造一段无来由的位移）。
  const prev = versePrev(c, si);
  const from = prev ? verseClipY(c, prev) : to;
  const p = Math.max(0, Math.min(1, (t - t0) / RELAY_SECONDS));
  const y = from + (to - from) * SCROLL_EASE(p);
  c.clip.style.transform = "translateY(" + y.toFixed(2) + "px)";
};
// ── 焦点层级的第五维：时间（换档过程）─────────────────────────────────
// 前四维（亮度 / 尺寸 / 位置 / 景深）都只是**档位表**：离焦距离 d → 一个静态值。
// 但"档位何时生效"此前只做了一半：换句窗口内**所有非 past 行共用一枚**从
// RELAY_FROM 起的全局淡入系数，而变成 past 的行由 verseApply 一次性写死。于是
// 每次换句真正的运动只有"新当前行浮出"这一个，其余行是**不动**或**闪一下**：
//   · 刚变成 past 的那行：一帧内 opacity 1 → .45、scale 1 → .8836、filter
//     none → blur(1.6px)（实测 t=4.78 → 4.80）——退场是硬的；
//   · 只是往前挪一档的行（d: 2→1）：.45 → **.35** → .452 → .62，中间比两端
//     都暗——被全局淡入的起点压下去再涨回来；
//   · 离焦距离**根本没变**的行：.45 → **.35** → .45——纯闪烁，它本来该一动不动。
//
// 前沿歌词 UI（Apple Music 逐行歌词、AMLL 的 LyricPlayer、Spotify 同步歌词）里所有
// 行共享同一套 spring 物理：换句是整块歌词各行**同时**柔和挪位——新行上浮放大变亮、
// 旧行继续上浮缩小褪色、更远的行同步递降，没有一行跳档，也没有一行会先暗下去再
// 亮回来。d 不是"档位号"，而是**连续场里的一个坐标**。
//
// 所以这里把档位表升级成连续场：换句窗口内每一行都从「上一句时的档」插值到
// 「这一句时的档」，用与滚动同一条曲线、同一个 p、同一个窗口——三个维度同一时刻
// 到位，"焦点"不能在途中分裂成"先挪档、再入场"两个动作。
// 内联变量的写与撤走同一对，不留"该撤没撤"的孤儿值——字体就绪清缓存那次就翻过
// 车：行上留着上一份缓存时代写下的内联值，再也没人摘。
const verseVarSet = (L, key, s) => {
  if (L.el.style.getPropertyValue(key) !== s) L.el.style.setProperty(key, s);
};
const verseVarClear = (L, key) => {
  if (L.el.style.getPropertyValue(key) !== "") L.el.style.removeProperty(key);
};
// 精度与 verseApply 写的稳态档位保持一致（op 三位、scale 四位）：窗口最后一帧与
// 稳态值若差 0.0001，就会白触发一次样式重算。
const verseOpSet = (L, v) => verseVarSet(L, "--ctv-line-op", v.toFixed(3));
const verseScaleSet = (L, v) => verseVarSet(L, "--ctv-line-scale", v.toFixed(4));
// 入场浮出的两枚是**逐帧插值**、窗口结束必须撤的；档位那两枚窗口结束后仍要留在
// 行上（= 稳态值），所以两组分开清。
const verseRelayClear = (L) => {
  verseVarClear(L, "--ctv-relay");
  verseVarClear(L, "--ctv-relay-scale");
};
const verseInlineClear = (L) => {
  verseVarClear(L, "--ctv-line-op");
  verseVarClear(L, "--ctv-line-scale");
  verseRelayClear(L);
  verseBlurSet(L, 0);
};
const verseRelay = (c, t, t0, si) => {
  if (!isFinite(t0)) return;
  const mine = c.lines.some((L) => _idx(L) === si);
  const p = Math.max(0, Math.min(1, (t - t0) / RELAY_SECONDS));
  const e = p >= 1 ? 1 : SCROLL_EASE(p);
  // 本段第一句没有 prev：除当前行的入场浮出外，所有行的档位都不动。
  const prev = mine ? versePrev(c, si) : null;
  const s0 = focusScale(1);
  const b0 = focusTable().blur;
  for (const L of c.lines) {
    const di = _idx(L);
    if (!mine) { verseInlineClear(L); continue; }
    const dNew = di - si;
    const dOld = prev ? di - _idx(prev) : dNew;
    // 这一行**这一帧**的三档：窗口内从 dOld 走到 dNew，窗口外就等于 dNew
    // （= 稳态档位，与 verseApply 写死的同一个数）。
    const at = (f) => f(dOld) + (f(dNew) - f(dOld)) * e;
    verseOpSet(L, at(focusOp));
    verseScaleSet(L, at(focusScale));
    // 景深取大者：档位插值（离焦行一路变虚）与当前行的入场（从虚到实收回 0）
    // 合成在**这一处**一起算——另一分支若跟着把滤镜整条撤掉，会把离焦行本来的
    // 模糊一起擦掉（入场结束不等于"这一行不需要模糊了"）。
    const float = di === si && p < 1;
    verseBlurSet(L, Math.max(at(focusBlur), float ? b0 * (1 - e) : 0));
    // 入场浮出**只叠给当前行**：它读的是"焦点换了地方"这个事件；其余行只是挪
    // 一档，跟着一起从 RELAY_FROM 浮起会让整墙字幕每次换句都闪一下（实测
    // .45 → .35 → .45）。
    if (float) {
      L.el.style.setProperty("--ctv-relay",
        (RELAY_FROM + (1 - RELAY_FROM) * e).toFixed(3));
      L.el.style.setProperty("--ctv-relay-scale", (s0 + (1 - s0) * e).toFixed(4));
    } else {
      verseRelayClear(L);
    }
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
    const tailT0 = _cueStart(cueSi);
    for (const v of verses) {
      const c = verseCacheOf(v);
      verseEmUpdate(c, cueSi, 1e9);
      verseRelay(c, 1e9, tailT0, cueSi);
      verseScroll(c, 1e9, tailT0, cueSi);
    }
    return;
  }
  const curT0 = cues[found].t;
  const curSi = cues[found].si;
  if (curSi !== cueSi) verseApply(curSi);
  for (const v of verses) {
    const c = verseCacheOf(v);
    verseEmUpdate(c, cueSi, t);
    verseRelay(c, t, curT0, curSi);
    verseScroll(c, t, curT0, curSi);
  }
};
const verseSync = (t, t2) => {
  // 时钟锚：只信"主时间线的时刻"。**不要**再拿 gsap.globalTimeline.time() 当锚：
  // 那是墙钟（页面打开后一直在涨），只要它碰巧 ≥ 当前 seek 到的 t 就会被当成
  // "这一帧的时刻"——于是短时间线的中后段（墙钟已经超过片长）字幕完全按墙钟
  // 跑：seek 到 1.40 却按 1.55 画，settle 在句尾之前就触发、笔画像被快进。成片
  // 里渲染器 seek 的时刻通常远大于墙钟，所以症状只在预览/短片上暴露，很难归因
  // （踩过：settle 在 t=1.30 就挂上了，而句尾在 1.55）。
  // t2 是 ticker 通道传来的 tl.time()，与 t 同源，取较大者只为处理"回调晚于
  // ticker"的次序，不会跳到别的时钟上。
  let tt = t;
  if (t2 !== undefined && isFinite(t2) && isFinite(tt)) tt = Math.max(tt, t2);
  else if (!isFinite(tt) && t2 !== undefined) tt = t2;
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
  // 去重走 Set：filter 里 a.indexOf(x) 是 O(n²)——12fps × 20s 就有 240 个
  // 时间点，长片（几分钟级）上是几万² 次比较，主线程上听得见卡顿。
  const seen = new Set();
  const uniq = [];
  for (const x of ts) {
    if (isFinite(x) && x >= 0 && !seen.has(x)) {
      seen.add(x);
      uniq.push(x);
    }
  }
  uniq.sort((a, b) => a - b);
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

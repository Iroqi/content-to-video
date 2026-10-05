/** 导演编排的逐帧求值器：把生成器展开的 DirectorStep 数据按绝对帧应用到
 * 净化内联的 SVG DOM 上（相当于把一条 GSAP 时间线 seek 到这一帧）。
 *
 * 确定性契约：
 * - 节拍 pos/dur/ease/repeat/yoyo/stagger 全部由生成器算好，这里只做代数；
 * - repeat:-1 是"按时间解析的无限循环"——每一帧的值都是时间的确定函数，
 *   不存在"最后停在哪儿"；yoyo 在奇数次遍序上倒放；
 * - morph 用生成器采样的关键帧（fps×2），seek 取"最近一个已到的关键帧"；
 * - count/type 写 textContent：count 在节拍前显示 from 值（HTML 的 build
 *   f() 行为）、type 在节拍前为空串——两个都按帧重算，天然可复现；
 * - 补间变量的"自然态"（元素未被打到时的样子）来自 SVG 自身：首次触碰时
 *   捕获（computed style / transform 属性 / attr 现值），to 补间从自然态
 *   插到目标、from 补间从目标插回自然态（与 GSAP 的 current-value 语义
 *   一致）；本求值器只增删自己写进去的内联样式/属性，WeakMap 记账。
 *
 * 已知取舍（README 已写明）：GSAP 的 `random` stagger 源在逐帧渲染下本身
 * 不确定，这里按 "start" 处理；grid/from 的几何精确复刻需要布局引擎，这里
 * 按索引的近似网格计算。
 */

import type {DirectorData, DirectorStep} from "../data";
import type {EaseFn} from "../easing";
import {clamp01, easeByName, lerp} from "../easing";

/** 模板 animation.director.ease 的缺省手性：契约层已保证数据胶里的 ease 都在
 * 目录内，这里兜的是"跨版本数据胶"那一类并不该发生的情况——兜而不默，
 * 一帧清点一次。 */
const DEFAULT_EASE = "power2.out";
const warned = new Set<string>();

// ── 缓动：唯一实现在 ../easing.ts（口径与 scripts/_ease.py 对拍）──────────

/** 缓动解析：解析不出就用缺省手性，并**出声一次**（认不出来就按线性演，是最难
 * 排查的一类静默退化——成片看着"不太对"，说不清哪儿不对）。 */
const easeFor = (name: string): EaseFn => {
  const f = easeByName(name);
  if (f) return f;
  if (!warned.has(name)) {
    warned.add(name);
    // eslint-disable-next-line no-console
    console.warn(`[director] ease ${name} 不在 scripts/_ease.py 的档名目录里，` +
      `本帧按 ${DEFAULT_EASE} 演——请改住 images.json 的这条 ease`);
  }
  return easeByName(DEFAULT_EASE)!;
};

const hexToRgb = (hex: string): [number, number, number] | null => {
  let h = hex.trim().replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  if (h.length !== 6 || !/^[0-9a-fA-F]{6}$/.test(h)) return null;
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
};

const toHex = (c: [number, number, number]): string =>
  `#${c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("")}`;

const isColor = (v: unknown): v is string => typeof v === "string" && hexToRgb(v) !== null;

/** "rgb(r, g, b)" → "#rrggbb"（computed style 的返回值形态）。 */
const rgbToHex = (s: string): string => {
  const m = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/.exec(s);
  if (!m) return s;
  return toHex([+m[1], +m[2], +m[3]]);
};

const lerpColor = (a: string, b: string, t: number): string => {
  const ra = hexToRgb(a);
  const rb = hexToRgb(b);
  if (!ra || !rb) return t >= 1 ? b : a;
  return toHex(ra.map((v, i) => lerp(v, rb[i], t)) as [number, number, number]);
};

const parseNum = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/** 插值一个变量：数字/颜色线性插，串在 u≥1 落到终值（GSAP 对非插值串
 * 也只在补间结束时落到目标）。 */
const interpValue = (from: unknown, to: unknown, u: number): unknown => {
  const nf = parseNum(from);
  const nt = parseNum(to);
  if (nf !== null && nt !== null) return lerp(nf, nt, u);
  if (isColor(from) && isColor(to)) return lerpColor(from, to, u);
  return u >= 1 ? to : from;
};

const camelToKebab = (k: string): string => k.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

/** 相对量（GSAP 的 `+=100` / `-=0.5`）→ 以自然态为基值的绝对值。
 * 不认它就是静默变 0（`parseFloat("+=100")` 是 NaN，一路 `?? 0` 兜成 0），
 * 作者写的是"再往右挪一点"，成片是"整体弹回原点"。 */
const absRel = (el: Element, key: string, v: unknown): unknown => {
  if (typeof v !== "string") return v;
  const m = /^([+-])=\s*([-+eE\d.]+)$/.exec(v.trim());
  if (!m) return v;
  const base = parseNum(naturalFor(el, key)) ?? 0;
  return m[1] === "+" ? base + Number.parseFloat(m[2]) : base - Number.parseFloat(m[2]);
};

// ── 自然态捕获（GSAP 的 current-value 语义）─────────────────────────────
const naturalMemo = new WeakMap<Element, Map<string, unknown>>();

/** transform 属性 → 位移/缩放/旋转的自然值。
 * 按 SVG 语义合成，而不是取串里第一个 transform 函数：`translate(a) translate(b)`
 * 是**累加**（等于 translate(a+b)）、`scale(a) scale(b)` 是**相乘**、多个 rotate
 * 累加角度。单参形态也算：`translate(dx)` ≡ `translate(dx,0)`、`scale(s)` ≡
 * `scale(s,s)`。接错一处，keep 页与运镜的起手式就整体偏出去一截。 */
const parseTransformAttr = (el: Element): {x: number; y: number; sx: number; sy: number; r: number} => {
  const t = el.getAttribute("transform") ?? "";
  let x = 0, y = 0, sx = 1, sy = 1, r = 0;
  const n = "[-+eE\\d.]";
  const tr = new RegExp(`translate\\(\\s*(${n}+)(?:[,\\s]+(${n}+))?\\s*\\)`, "g");
  for (let m = tr.exec(t); m !== null; m = tr.exec(t)) {
    x += parseFloat(m[1]);
    if (m[2] !== undefined) y += parseFloat(m[2]);
  }
  const sc = new RegExp(`scale\\(\\s*(${n}+)(?:[,\\s]+(${n}+))?\\s*\\)`, "g");
  for (let m = sc.exec(t); m !== null; m = sc.exec(t)) {
    const ax = parseFloat(m[1]);
    const ay = m[2] !== undefined ? parseFloat(m[2]) : ax;
    sx *= ax;
    sy *= ay;
  }
  const ro = /rotate\(\s*([-+eE\d.]+)/g;
  for (let m = ro.exec(t); m !== null; m = ro.exec(t)) r += parseFloat(m[1]);
  return {x, y, sx, sy, r};
};

const naturalFor = (el: Element, key: string): unknown => {
  let m = naturalMemo.get(el);
  if (!m) { m = new Map<string, unknown>(); naturalMemo.set(el, m); }
  if (m.has(key)) return m.get(key);
  let val: unknown;
  if (key === "x" || key === "y" || key === "scale" || key === "scaleX" ||
      key === "scaleY" || key === "rotation") {
    const t = parseTransformAttr(el);
    val = key === "x" ? t.x : key === "y" ? t.y : key === "scale" ? t.sx
      : key === "scaleX" ? t.sx : key === "scaleY" ? t.sy : t.r;
  } else if (key === "opacity") {
    // 读不到（NaN）才回落 1；读到 0 就是自然态 0 —— `|| 1` 会把"作者写了
    // opacity=0 准备淡入"读成 1，于是 from 0→to 1 的淡入在每一帧都算出 1，
    // 动画在第一帧就跳到终值，整段淡入静默失效。
    const o = Number.parseFloat(getComputedStyle(el).opacity);
    val = Number.isFinite(o) ? o : 1;
  } else if (key === "fill" || key === "stroke") {
    val = rgbToHex(getComputedStyle(el)[key] ?? "none");
  } else if (key.startsWith("attr.")) {
    const k = key.slice(5);
    const av = el.getAttribute(k);
    val = av === null ? 0 : (Number.isNaN(parseFloat(av)) ? av : parseFloat(av));
  } else {
    const n = Number.parseFloat(getComputedStyle(el).getPropertyValue(camelToKebab(key)));
    val = Number.isFinite(n) ? n : 0;
  }
  m.set(key, val);
  return val;
};

// ── 单元素状态记账：只撤自己写过的内联样式/属性，不碰 SVG 作者原有样式 ──
interface NodeState {
  styles: Set<string>;
  attrs: Set<string>;
  origText: string | null;
}
const states = new WeakMap<Element, NodeState>();
const stateFor = (el: Element): NodeState => {
  let s = states.get(el);
  if (!s) {
    s = {styles: new Set<string>(), attrs: new Set<string>(), origText: null};
    states.set(el, s);
  }
  return s;
};

const clearOverrides = (el: Element): void => {
  const s = stateFor(el);
  const style = (el as HTMLElement).style;
  for (const k of s.styles) style.removeProperty(k);
  for (const k of s.attrs) el.removeAttribute(k);
  if (s.origText !== null) {
    el.textContent = s.origText;
    s.origText = null;
  }
  s.styles.clear();
  s.attrs.clear();
};

const setStyle = (el: Element, key: string, value: string): void => {
  (el as HTMLElement).style.setProperty(key, value);
  stateFor(el).styles.add(key);
};

const setAttr = (el: Element, key: string, value: string): void => {
  el.setAttribute(key, value);
  stateFor(el).attrs.add(key);
};

const CSS_PROPS: Record<string, string> = {
  opacity: "opacity",
  fill: "fill",
  stroke: "stroke",
  strokeDashoffset: "stroke-dashoffset",
  strokeDasharray: "stroke-dasharray",
  strokeWidth: "stroke-width",
};
const TRANSFORM_KEYS = new Set(["x", "y", "scale", "scaleX", "scaleY", "rotation"]);

/** 解析数字：number 直接取；字符串走 parseFloat（数据胶里的 "%g %g" 串）。 */
const toNum = (v: unknown): number | null => {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number.parseFloat(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
};

/** svgOrigin（"50 55.325" 或 [50,55.325] 或 "20%,30%"）→ 数值对 | null。 */
const parseOriginPair = (origin: unknown): [number, number] | null => {
  if (Array.isArray(origin) && origin.length === 2) {
    const a = toNum(origin[0]);
    const b = toNum(origin[1]);
    if (a !== null && b !== null) return [a, b];
  }
  if (typeof origin === "string") {
    const parts = origin.trim().split(/[,\s]+/).filter(Boolean);
    if (parts.length === 2) {
      const a = toNum(parts[0]);
      const b = toNum(parts[1]);
      if (a !== null && b !== null) return [a, b];
    }
  }
  return null;
};

const applyVars = (
  el: Element,
  vars: Record<string, unknown>,
  camOrigin: [number, number] | null,
  targetIsCam: boolean,
): void => {
  const tx = vars["x"], ty = vars["y"], sc = vars["scale"],
        scX = vars["scaleX"], scY = vars["scaleY"], rot = vars["rotation"];
  if (tx !== undefined || ty !== undefined || sc !== undefined ||
      scX !== undefined || scY !== undefined || rot !== undefined) {
    const x = tx === undefined ? 0 : (parseNum(tx) ?? 0);
    const y = ty === undefined ? 0 : (parseNum(ty) ?? 0);
    const sx = sc !== undefined ? (parseNum(sc) ?? 1) : scX !== undefined ? (parseNum(scX) ?? 1) : 1;
    const sy = sc !== undefined ? sx : scY !== undefined ? (parseNum(scY) ?? 1) : 1;
    const r = rot === undefined ? 0 : ((parseNum(rot) ?? 0) * Math.PI) / 180;
    // 原点：svgOrigin / 数据胶 camOrigin（"50 55.325" 或数值对）→ 数值对；否则 (0,0)。
    // 不用 CSS transform-origin/transform-box：实测该渲染器里这两者（尤其配 view-box）
    // 的 JS 赋值不可靠，而直接 style.transform 赋值可靠。为保证与
    // _stage_carry._fold_camera 烘焙矩阵（translate(e,f) scale(s)，e=(1-s)*cx+x）按构造
    // 一致，这里显式把「绕点缩放平移」展开成 matrix()：
    //   a = sx·cosθ  b = sy·sinθ  c = -sx·sinθ  d = sy·cosθ
    //   e = x + ox − a·ox − c·oy   f = y + oy − b·ox − d·oy
    // 无旋转时退化为 e = x + ox(1−sx)、f = y + oy(1−sy) —— 与烘焙逐位相同。
    const origin = parseOriginPair(vars["svgOrigin"] ?? vars["transformOrigin"])
      ?? (targetIsCam && camOrigin ? camOrigin : null);
    const [ox, oy] = origin ?? [0, 0];
    const cos = Math.cos(r), sin = Math.sin(r);
    const a = sx * cos, b = sy * sin, c = -sx * sin, d = sy * cos;
    const e = x + ox - a * ox - c * oy;
    const f = y + oy - b * ox - d * oy;
    const fmt = (n: number): string => {
      const v = Math.round(n * 1e4) / 1e4;
      return String(v);
    };
    setStyle(el, "transform", `matrix(${fmt(a)},${fmt(b)},${fmt(c)},${fmt(d)},${fmt(e)},${fmt(f)})`);
  }
  for (const [key, css] of Object.entries(CSS_PROPS)) {
    const v = vars[key];
    if (v === undefined) continue;
    setStyle(el, css, String(v));
  }
  const attr = vars["attr"];
  if (attr && typeof attr === "object") {
    for (const [k, v] of Object.entries(attr as Record<string, unknown>)) {
      if (typeof v === "number" || typeof v === "string") setAttr(el, k, String(v));
    }
  }
  for (const [k, v] of Object.entries(vars)) {
    if (k === "attr" || k === "svgOrigin" || k === "transformOrigin" ||
        TRANSFORM_KEYS.has(k) || k in CSS_PROPS) continue;
    const n = parseNum(v);
    if (n !== null) setStyle(el, camelToKebab(k), String(n));
    else if (typeof v === "string") setAttr(el, k, v);
  }
};

/** 把 fromVars→toVars（可缺边）按进度 u 插成一份可应用变量表。
 * to 缺 from：自然态起步；from 缺 to：插回自然态。attr:{} 递归同规则。 */
const tweenVars = (
  el: Element,
  fromVars: Record<string, unknown> | null,
  toVars: Record<string, unknown> | null,
  u: number,
): Record<string, unknown> => {
  const out: Record<string, unknown> = {};
  const keys = new Set<string>([
    ...Object.keys(fromVars ?? {}),
    ...Object.keys(toVars ?? {}),
    "x", "y", "scale", "scaleX", "scaleY", "rotation",
  ]);
  const attrFrom = (fromVars?.attr ?? {}) as Record<string, unknown>;
  const attrTo = (toVars?.attr ?? {}) as Record<string, unknown>;
  const attrKeys = new Set<string>([...Object.keys(attrFrom), ...Object.keys(attrTo)]);
  if (attrKeys.size > 0) {
    const nested: Record<string, unknown> = {};
    for (const k of attrKeys) {
      const from = absRel(el, `attr.${k}`, attrFrom[k] ?? naturalFor(el, `attr.${k}`));
      const to = absRel(el, `attr.${k}`, attrTo[k]);
      nested[k] = interpValue(from, to, u);
    }
    out["attr"] = nested;
  }
  for (const key of keys) {
    if (key === "attr") continue;
    const rawTo = toVars?.[key];
    if (rawTo === undefined) continue;      // 该键只在 from 侧（from 补间插回自然态时 to=自然）
    const from = absRel(el, key, fromVars?.[key] ?? naturalFor(el, key));
    out[key] = interpValue(from, absRel(el, key, rawTo), u);
  }
  // from 补间：to 侧为空，但键在 fromVars 里——插回自然态
  if (fromVars && !toVars) {
    for (const key of Object.keys(fromVars)) {
      if (key === "attr" || out[key] !== undefined) continue;
      const from = absRel(el, key, fromVars[key]);
      if (typeof from === "number" || isColor(from)) {
        out[key] = interpValue(from, naturalFor(el, key), u);
      } else if (typeof from === "string") {
        out[key] = u >= 1 ? naturalFor(el, key) : from;
      }
    }
  }
  // transform 是**整条 matrix 覆写**：给了其中一个轴，其余轴必须沿用元素的自然值，
  // 否则 applyVars 会把它们补成 0/1——表现是"我只动了 x，元素却整个跳回原点"。
  // keep 页的 translate 前缀与 #cam 的取景矩阵都挂在这条上。放在最后：不能抢在
  // from 补间之前，否则 `out[key] !== undefined` 会让那些轴停止插值。
  const has = (k: string): boolean => out[k] !== undefined;
  if (has("x") || has("y") || has("scale") || has("scaleX") || has("scaleY") || has("rotation")) {
    if (!has("x")) out["x"] = naturalFor(el, "x");
    if (!has("y")) out["y"] = naturalFor(el, "y");
    if (!has("rotation")) out["rotation"] = naturalFor(el, "rotation");
    // scale 与 scaleX/scaleY 互斥（applyVars 里前者优先），缺哪个补哪个
    if (!has("scale") && !has("scaleX") && !has("scaleY")) {
      out["scaleX"] = naturalFor(el, "scaleX");
      out["scaleY"] = naturalFor(el, "scaleY");
    }
  }
  return out;
};

/** 单个 step 在 t 时刻对单个元素的进度状态。 */
const stepProgress = (
  step: DirectorStep,
  t: number,
  delayOffset: number,
): {u: number; before: boolean; ended: boolean; endU: number} => {
  const start = step.pos + delayOffset;
  const cycles = step.repeat < 0 ? Infinity : step.repeat + 1;
  // duration:0 是"瞬时赋值"（契约层允许 ≥0）：不要闪一帧起始值，直接给终态。
  if (step.dur <= 0) {
    return {u: 1, before: t < start, ended: t >= start, endU: 1};
  }
  const dur = step.dur;
  const spanEnd = cycles === Infinity ? Infinity : start + dur * cycles;
  // yoyo 的偶数遍收尾停在**起点**（末遍是倒放），奇数遍停在 to —— 与
  // scripts/_timeline.ends_at_start 同一口径；无限循环没有"收尾"，恒为 1。
  const endU = step.yoyo && cycles !== Infinity && cycles % 2 === 0 ? 0 : 1;
  if (t < start) return {u: 0, before: true, ended: false, endU};
  const phase = (t - start) / dur;
  const c = Math.floor(phase);
  let p = phase - c;
  if (step.yoyo && c % 2 === 1) p = 1 - p;
  return {
    u: easeFor(step.ease)(p),
    before: false,
    ended: spanEnd !== Infinity && t >= spanEnd,
    endU,
  };
};

const staggerOffset = (step: DirectorStep, n: number, i: number): number => {
  const st = step.stagger;
  if (st == null) return 0;
  if (typeof st === "number") return st * i;
  const each = st.amount != null ? st.amount / Math.max(1, n - 1) : (st.each ?? 0);
  if (typeof st.from === "number") return (st.from + i) * each;
  if (st.from === "end") return (n - 1 - i) * each;
  if (st.from === "center") return Math.abs(i - (n - 1) / 2) * each;
  return i * each;   // start / random（README：random 按 start 处理）
};

/** 对整棵容器应用一帧的 director 状态（直接改 DOM；Remotion 每帧 effect 执行）。 */
export const applyDirectorFrame = (
  container: HTMLElement,
  director: DirectorData,
  t: number,
): void => {
  // 数据胶的 camOrigin 来自 cam_default_origin 的 "%g %g" 串：归一成数值对
  // 再进 applyVars（组件里不解析字符串语义）。
  const camOrigin = parseOriginPair(director.camOrigin);

  // 逐帧可复现的前提：**每一帧都从时间线的起点重算**，不留上一帧的残留。
  // 但要先把它们全部清回自然态，再按 steps 顺序重放 —— 不能边遍历边清：
  // 那样"数组里靠后、此刻尚未开始"的 step 会清掉前面已经演完的赋值
  // （表现为"该隐藏的图元忽然全亮""打字机一个字都没打"）。
  const touched = new Set<Element>();
  for (const step of director.steps) {
    try {
      container.querySelectorAll(step.target).forEach((el) => touched.add(el));
    } catch {
      continue;
    }
  }
  touched.forEach(clearOverrides);

  for (const step of director.steps) {
    let nodes: NodeListOf<Element>;
    try {
      nodes = container.querySelectorAll(step.target);
    } catch {
      continue;
    }
    if (nodes.length === 0) continue;
    const n = nodes.length;
    const targetIsCam = step.target === "#cam";
    nodes.forEach((el, i) => {
      const off = staggerOffset(step, n, i);
      const {u, before, ended, endU} = stepProgress(step, t, off);
      // 未开始的这一拍不做事：清场已经在上面统一做过了，这里再清就会
      // 抹掉同元素上更早那几拍的成果。
      if (before) return;

      if (step.kind === "morph") {
        const keys = step.morphKeys ?? [];
        if (ended) {
          // yoyo 的偶数遍收尾要落在**首帧**（末遍倒放回起点），不是末帧。
          const last = endU === 0 ? keys[0] : keys[keys.length - 1];
          if (last) setAttr(el, "d", last.d);
        } else {
          let d = "";
          for (const k of keys) {
            if (k.t <= t) d = k.d;
            else break;
          }
          if (d) setAttr(el, "d", d);
        }
        return;
      }

      if (step.kind === "count") {
        const c = step.count!;
        const v = lerp(c.from, c.to, ended ? endU : u);
        el.textContent = `${c.prefix}${v.toFixed(c.decimals)}${c.suffix}`;
        return;
      }

      if (step.kind === "type") {
        const s = stateFor(el);
        if (s.origText === null) s.origText = el.textContent ?? "";
        const k = Math.round(s.origText.length * (ended ? endU : u));
        el.textContent = s.origText.slice(0, k);
        return;
      }

      if (step.kind === "draw") {
        setAttr(el, "pathLength", "1");
        setStyle(el, "stroke-dasharray", "1");
        setStyle(el, "stroke-dashoffset", String(1 - (ended ? endU : u)));
        return;
      }

      // set：瞬时赋值，节拍后一直持值
      if (step.kind === "set") {
        applyVars(el, tweenVars(el, null, step.vars ?? null, 1), camOrigin, targetIsCam);
        return;
      }

      if (ended) {
        // from 补间的终态：奇数遍收尾回到自然态、偶数遍收尾停在起点值
        if (step.kind === "from") {
          if (endU === 1) clearOverrides(el);
          else applyVars(el, tweenVars(el, step.fromVars ?? null, null, 0), camOrigin, targetIsCam);
        } else {
          // fromTo 也要带上起点：endU=0 时收尾落在 fromVars 上，而不是自然态
          const fromVars = step.kind === "fromTo" ? (step.fromVars ?? null) : null;
          applyVars(el, tweenVars(el, fromVars, step.toVars ?? null, endU), camOrigin, targetIsCam);
        }
        return;
      }
      const fromVars = step.kind === "from" || step.kind === "fromTo" ? (step.fromVars ?? null) : null;
      const toVars = step.kind === "to" || step.kind === "fromTo" ? (step.toVars ?? null) : null;
      applyVars(el, tweenVars(el, fromVars, toVars, u), camOrigin, targetIsCam);
    });
  }
};

/** 导演编排的逐帧求值器：把生成器展开的 DirectorStep 数据按绝对帧应用到
 * 净化内联的 SVG DOM 上（等价于 html_renderer._director_timeline_lines 的
 * GSAP 补间在逐帧 seek 下的效果）。
 *
 * 确定性契约（与 GSAP 同一语义）：
 * - 节拍 pos/dur/ease/repeat/yoyo/stagger 全部由生成器算好，这里只做代数；
 * - repeat:-1 是"按时间解析的无限循环"——每一帧的值都是时间的确定函数，
 *   不存在"最后停在哪儿"；yoyo 在奇数次遍序上倒放（与 GSAP 一致）；
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

type EaseFn = (x: number) => number;
const clamp01 = (x: number): number => Math.min(1, Math.max(0, x));
const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

const powEase = (p: number, exp: number, mode: "in" | "out" | "inOut"): number => {
  if (mode === "in") return Math.pow(clamp01(p), exp);
  if (mode === "out") return 1 - Math.pow(1 - clamp01(p), exp);
  const t = clamp01(p);
  return t < 0.5
    ? Math.pow(2, exp - 1) * Math.pow(t, exp)
    : 1 - Math.pow(-2 * t + 2, exp) / 2;
};

const backEase = (p: number, s: number, mode: "in" | "out" | "inOut"): number => {
  const c1 = s;
  const c3 = c1 + 1;
  const x = clamp01(p);
  if (mode === "out") return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
  if (mode === "in") return c3 * Math.pow(x, 3) - c1 * Math.pow(x, 2);
  const t = clamp01(p);
  if (t < 0.5) return 0.5 * (c3 * Math.pow(2 * t - 1, 3) - c1 * Math.pow(2 * t - 1, 2) + 1);
  return 0.5 * (c3 * Math.pow(2 * t - 2, 3) + c1 * Math.pow(2 * t - 2, 2) + 1);
};

const EASE_REGISTRY: Record<string, EaseFn> = {};
type EaseMode = "in" | "out" | "inOut";
const registerFamily = (names: string[], make: (fam: string, mode: EaseMode) => EaseFn): void => {
  for (const mode of ["in", "out", "inOut"] as EaseMode[]) {
    for (const fam of names) {
      EASE_REGISTRY[`${fam}.${mode}`] = make(fam, mode);
    }
  }
};
registerFamily(["power1", "power2", "power3", "power4", "quad", "cubic", "quart", "quint"], (fam, mode) => {
  const exp = {power1: 1, power2: 2, power3: 3, power4: 4, quad: 2, cubic: 3, quart: 4, quint: 5}[fam] ?? 2;
  return (p: number) => powEase(p, exp, mode);
});
registerFamily(["expo"], (_, mode) => {
  const f = (p: number): number => {
    const t = clamp01(p);
    if (mode === "in") return Math.pow(2, 10 * (t - 1));
    if (mode === "out") return 1 - Math.pow(2, -10 * t);
    return t < 0.5 ? Math.pow(2, 20 * t - 10) / 2 : (2 - Math.pow(2, -20 * t + 10)) / 2;
  };
  return f;
});
registerFamily(["sine"], (_, mode) => (p: number) => {
  const t = clamp01(p);
  if (mode === "in") return 1 - Math.cos((t * Math.PI) / 2);
  if (mode === "out") return Math.sin((t * Math.PI) / 2);
  return -(Math.cos(Math.PI * t) - 1) / 2;
});
registerFamily(["circ"], (_, mode) => (p: number) => {
  const t = clamp01(p);
  if (mode === "in") return 1 - Math.sqrt(1 - t * t);
  if (mode === "out") return Math.sqrt(1 - Math.pow(t - 1, 2));
  return t < 0.5 ? (1 - Math.sqrt(1 - 4 * t * t)) / 2 : (Math.sqrt(1 - Math.pow(-2 * t + 2, 2)) + 1) / 2;
});
registerFamily(["back"], (_, mode) => (p: number) => backEase(p, 1.70158, mode));
["none", "linear", "power0.inOut"].forEach((k) => {
  EASE_REGISTRY[k] = (p: number) => clamp01(p);
});

/** ease 名 → 曲线；未收录的族回退线性（契约层校验过的族名都在上面）。 */
export const easeByName = (name: string): EaseFn =>
  EASE_REGISTRY[name] ?? ((p: number) => clamp01(p));

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

// ── 自然态捕获（GSAP 的 current-value 语义）─────────────────────────────
const naturalMemo = new WeakMap<Element, Map<string, unknown>>();

const parseTransformAttr = (el: Element): {x: number; y: number; sx: number; sy: number; r: number} => {
  const t = el.getAttribute("transform") ?? "";
  let x = 0, y = 0, sx = 1, sy = 1, r = 0;
  const tr = /translate\(\s*([-\d.]+)[,\s]+([-\d.]+)\s*\)/.exec(t);
  if (tr) { x = parseFloat(tr[1]); y = parseFloat(tr[2]); }
  const sc = /scale\(\s*([-\d.]+)(?:[,\s]+([-\d.]+))?\s*\)/.exec(t);
  if (sc) { sx = parseFloat(sc[1]); sy = sc[2] !== undefined ? parseFloat(sc[2]) : sx; }
  const ro = /rotate\(\s*([-\d.]+)\s*\)/.exec(t);
  if (ro) r = parseFloat(ro[1]);
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
    val = parseFloat(getComputedStyle(el).opacity) || 1;
  } else if (key === "fill" || key === "stroke") {
    val = rgbToHex(getComputedStyle(el)[key] ?? "none");
  } else if (key.startsWith("attr.")) {
    const k = key.slice(5);
    const av = el.getAttribute(k);
    val = av === null ? 0 : (Number.isNaN(parseFloat(av)) ? av : parseFloat(av));
  } else {
    const cs = getComputedStyle(el).getPropertyValue(camelToKebab(key));
    val = parseFloat(cs) || 0;
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
      const from = attrFrom[k] ?? naturalFor(el, `attr.${k}`);
      const to = attrTo[k];
      nested[k] = interpValue(from, to, u);
    }
    out["attr"] = nested;
  }
  for (const key of keys) {
    if (key === "attr") continue;
    const from = fromVars?.[key] ?? naturalFor(el, key);
    const to = toVars?.[key];
    if (to === undefined) continue;      // 该键只在 from 侧（from 补间插回自然态时 to=自然）
    out[key] = interpValue(from, to, u);
  }
  // from 补间：to 侧为空，但键在 fromVars 里——插回自然态
  if (fromVars && !toVars) {
    for (const key of Object.keys(fromVars)) {
      if (key === "attr" || out[key] !== undefined) continue;
      const from = fromVars[key];
      if (typeof from === "number" || isColor(from)) {
        out[key] = interpValue(from, naturalFor(el, key), u);
      } else if (typeof from === "string") {
        out[key] = u >= 1 ? naturalFor(el, key) : from;
      }
    }
  }
  return out;
};

/** 单个 step 在 t 时刻对单个元素的进度状态。 */
const stepProgress = (
  step: DirectorStep,
  t: number,
  delayOffset: number,
): {u: number; before: boolean; ended: boolean} => {
  const start = step.pos + delayOffset;
  const dur = Math.max(0.001, step.dur);
  const cycles = step.repeat < 0 ? Infinity : step.repeat + 1;
  const spanEnd = cycles === Infinity ? Infinity : start + dur * cycles;
  if (t < start) return {u: 0, before: true, ended: false};
  const phase = (t - start) / dur;
  const c = Math.floor(phase);
  let p = phase - c;
  if (step.yoyo && c % 2 === 1) p = 1 - p;
  return {
    u: easeByName(step.ease)(p),
    before: false,
    ended: spanEnd !== Infinity && t >= spanEnd,
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
      const {u, before, ended} = stepProgress(step, t, off);
      const inWindow = !before;

      if (step.kind === "morph") {
        const keys = step.morphKeys ?? [];
        if (before) {
          clearOverrides(el);
        } else if (ended) {
          const last = keys[keys.length - 1];
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
        const v = before ? c.from : ended ? c.to : lerp(c.from, c.to, u);
        el.textContent = `${c.prefix}${v.toFixed(c.decimals)}${c.suffix}`;
        return;
      }

      if (step.kind === "type") {
        const s = stateFor(el);
        if (s.origText === null) s.origText = el.textContent ?? "";
        const k = before ? 0 : ended ? s.origText.length : Math.round(lerp(0, s.origText.length, u));
        el.textContent = s.origText.slice(0, k);
        return;
      }

      if (step.kind === "draw") {
        if (t < director.segStart) {
          clearOverrides(el);
          return;
        }
        setAttr(el, "pathLength", "1");
        setStyle(el, "stroke-dasharray", "1");
        setStyle(el, "stroke-dashoffset",
          before ? "1" : ended ? "0" : String(1 - u));
        return;
      }

      // set：瞬时赋值，节拍前自然态、节拍后持值
      if (step.kind === "set") {
        if (before) {
          clearOverrides(el);
        } else {
          applyVars(el, tweenVars(el, null, step.vars ?? null, 1), camOrigin, targetIsCam);
        }
        return;
      }

      // to / from / fromTo
      if (before) {
        clearOverrides(el);
        return;
      }
      if (ended) {
        if (step.kind === "from") {
          clearOverrides(el);
        } else {
          const toVars = step.toVars ?? null;
          applyVars(el, tweenVars(el, null, toVars, 1), camOrigin, targetIsCam);
        }
        return;
      }
      const fromVars = step.kind === "from" || step.kind === "fromTo" ? (step.fromVars ?? null) : null;
      const toVars = step.kind === "to" || step.kind === "fromTo" ? (step.toVars ?? null) : null;
      applyVars(el, tweenVars(el, fromVars, toVars, u), camOrigin, targetIsCam);
    });
  }
};

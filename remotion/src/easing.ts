/** GSAP 缓动曲线：ease 串 → 曲线 f:[0,1]→[0,1]。
 *
 * **这里是渲染端唯一的缓动数学**——`director.ts` 的逐帧求值、组件的入场/转场
 * 全部走它。口径必须与 Python 真源 `scripts/_ease.py` 的 `curve()` 逐点一致：
 * 家族目录与写法由 `_ease.validate_ease` 在契约层 fail-fast，曲线在这里求值，
 * 两边任一漂移都会让"算出来的姿态"与"演出来的姿态"分家。
 * `tests/test_ease_parity.py` 负责盯住这份一致（幂次表 + 逐点采样）。
 *
 * 形状照 `_ease.split_ease`：`族名[(参数)][.方向][(参数)]`，方向缺省按 GSAP 语义
 * 补 `.out`；各 in 变体照 `_ease.curve` 的 g（python 侧即此处的一一对应）。
 */
export type EaseFn = (x: number) => number;
export type EaseMode = "in" | "out" | "inOut";

export const clamp01 = (x: number): number => Math.min(1, Math.max(0, x));
export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;

/** 多项式族次数：power1=平方 … power4=五次；quad/cubic/quart/quint 是同族旧名。
 * 与 scripts/_ease.py 的 _POLY_EXP 同一张表（test_ease_parity 每日比对）。 */
export const POLY_EXP: Record<string, number> = {
  power1: 2, power2: 3, power3: 4, power4: 5,
  quad: 2, cubic: 3, quart: 4, quint: 5,
};

/** 旧名 → 现在的族名（GSAP 两套拼法都解析）。strong 与 power4 同档。 */
const FAMILY_ALIAS: Record<string, string> = {strong: "power4"};

/** 方向后缀 → 内部 mode；空串即 GSAP 的缺省 .out。 */
const MODES: Record<string, EaseMode> = {
  "": "out", in: "in", out: "out", inout: "inOut", both: "inOut",
  easein: "in", easeout: "out", easeinout: "inOut",
};

const EASE_RE = /^([a-zA-Z0-9]+)(?:\(([^)]*)\))?(?:\.([a-zA-Z]+))?(?:\(([^)]*)\))?$/;

export interface ParsedEase {
  family: string;
  mode: EaseMode;
  args: string[];
}

/** ease 串 → (族名, 方向, 参数)；形状不对返回 null。两个位置都能带括号：
 * `steps(4).in`、`back.out(1.7)`、`elastic.inOut(1,0.3)` 都要解得出。 */
export const splitEase = (spec: string): ParsedEase | null => {
  if (typeof spec !== "string") return null;
  const m = EASE_RE.exec(spec.trim());
  if (!m) return null;
  const mode = MODES[(m[3] ?? "").toLowerCase()];
  if (mode === undefined) return null;
  const raw = (m[4] ?? m[2] ?? "").trim();
  const args = raw.split(",").map((a) => a.trim()).filter(Boolean);
  const family = FAMILY_ALIAS[m[1].toLowerCase()] ?? m[1].toLowerCase();
  return {family, mode, args};
};

/** 取第 i 个参数，缺省或不可解析用 fallback（照 _ease._num）。 */
const arg = (args: string[], i: number, fallback: number): number => {
  const n = Number.parseFloat(args[i] ?? "");
  return Number.isFinite(n) ? n : fallback;
};

const _bounceOut = (t: number): number => {
  const n = 7.5625, d = 2.75;
  if (t < 1 / d) return n * t * t;
  if (t < 2 / d) { const u = t - 1.5 / d; return n * u * u + 0.75; }
  if (t < 2.5 / d) { const u = t - 2.25 / d; return n * u * u + 0.9375; }
  const u = t - 2.625 / d;
  return n * u * u + 0.984375;
};

/** 各家族的 **in 变体** g（与 _ease.curve 的 g 逐行对应）。 */
const gOf = (family: string, args: string[], mode: EaseMode): EaseFn | null => {
  switch (family) {
    case "none":
    case "linear":
    case "power0":
      return (t: number) => t;
    case "steps": {
      const n = Math.trunc(arg(args, 0, 4));
      if (n < 1) return (t: number) => t;
      // GSAP 的 SteppedEase 把跳变放在 t=i/(n+1)；带第二参整体提前一格。
      const ahead = args.length > 1 && ["true", "1"].includes((args[1] ?? "").toLowerCase());
      if (ahead) {
        return (t: number) => (t >= 1 ? 1 : Math.min(Math.floor(t * n) + 1, n) / n);
      }
      return (t: number) => (t >= 1 ? 1 : Math.min(Math.floor(t * (n + 1)), n) / n);
    }
    case "elastic": {
      // GSAP _configElastic（照 python 侧实现）：ampl<1 时钳到 1、周期按 1/振幅放大
      const ampl = arg(args, 0, 1.0);
      const defaultPeriod = mode === "inOut" ? 0.45 : 0.3;
      const period = arg(args, 1, defaultPeriod) || defaultPeriod;  // 0/NaN 一律回落
      const a = ampl >= 1 ? ampl : 1.0;
      const per = period / (ampl < 1 ? ampl : 1.0);
      const s = (per * Math.asin(1.0 / a)) / (2 * Math.PI);
      return (t: number) => {
        if (t <= 0) return 0.0;
        if (t >= 1) return 1.0;
        return -a * Math.pow(2, 10 * (t - 1)) *
          Math.sin(((t - 1 - s) * (2 * Math.PI)) / per);
      };
    }
    case "bounce":
      return (t: number) => (t <= 0 ? 0.0 : t >= 1 ? 1.0 : 1 - _bounceOut(1 - t));
    case "sine":
      return (t: number) => 1 - Math.cos((t * Math.PI) / 2);
    case "expo":
      return (t: number) => (t ? Math.pow(2, 10 * (t - 1)) : 0.0);
    case "circ":
      return (t: number) => 1 - Math.sqrt(Math.max(0, 1 - t * t));
    case "back": {
      const c1 = arg(args, 0, 1.70158);
      const c3 = c1 + 1;
      return (t: number) => c3 * t * t * t - c1 * t * t;
    }
    default: {
      // 幂次族：目录内的查表；裸 power（不在放行目录）取首参当次数，缺省 power2。
      const exp = POLY_EXP[family] ??
        (family.startsWith("power") && /^\d+$/.test(family.slice(5))
          ? Number(family.slice(5)) + 1
          : Math.trunc(arg(args, 0, 2)) + 1);
      return (t: number) => Math.pow(t, exp);
    }
  }
};

/** 把 in 变体按方向翻成最终曲线（照 _ease.curve 的 mod 应用）。 */
/** 阶梯族是**终态曲线**，不参与方向翻转（照 _ease.curve：steps 直接 return）。
 *
 * 别拿 withMode 去翻它：`1 - g(1-t)` 对阶梯函数不是恒等——steps(3,true) 在
 * t=0.2 处 g 已跳到 1/3，翻转后成 2/3，与 Python 真源差整整一格。morph 采样
 * 与渲染端逐帧求值会就此分家（test_ease_parity 的逐点对拍就是钉这条）。
 * 阶梯的"方向"体现在跳变落点（i/(n+1)，带第二参时提前一格），不在翻转。 */
const STEPS_FAMILY = "steps";

/** 把 in 变体按方向翻成最终曲线（照 _ease.curve 的 mod 应用）。 */
const withMode = (g: EaseFn, mode: EaseMode): EaseFn => {
  if (mode === "in") return g;
  if (mode === "out") return (t: number) => 1 - g(1 - clamp01(t));
  return (t: number) => {
    const p = clamp01(t);
    return p < 0.5 ? 0.5 * g(2 * p) : 1 - 0.5 * g(2 - 2 * p);
  };
};

/** ease 名 → 曲线；解析不出返回 null（**不做静默兜底**——"认不出来就按线性"正是
 * 那种让人盯着成片说不清哪儿不对的退化，这里把决策权交回调用方去出声）。 */
export const easeByName = (spec: string): EaseFn | null => {
  const parsed = splitEase(spec);
  if (!parsed) return null;
  const g = gOf(parsed.family, parsed.args, parsed.mode);
  if (!g) return null;
  return parsed.family === STEPS_FAMILY ? g : withMode(g, parsed.mode);
};

// ── 具名导出：组件用的那几档，全部从上面的唯一实现派生 ────────────────
/** 取一档；名字写错在开发期就炸在那里，而不是静默退化成线性。 */
const named = (spec: string): EaseFn => {
  const f = easeByName(spec);
  if (!f) throw new Error(`未知缓动：${spec}`);
  return f;
};

/** ease: "power2.out"（图像入场 / apple 的 kicker 与行） */
export const power2Out = named("power2.out");
/** ease: "power2.in"（旧卡剥离） */
export const power2In = named("power2.in");
/** ease: "power2.inOut"（line 档擦除：慢起—快行—慢收） */
export const power2InOut = named("power2.inOut");
/** ease: "power3.out"（apple 标题：浮出落定） */
export const power3Out = named("power3.out");
/** ease: "sine.out"（光晕淡入） */
export const sineOut = named("sine.out");
/** ease: "back.out(1.7)"（通用标题入场） */
export const backOut = named("back.out(1.7)");

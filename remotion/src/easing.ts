/** GSAP 缓动曲线的 TS 移植（本项目用到的几个：back.out / power2 / power3 /
 * sine.out / sine.inOut / linear）。口径与 scripts/_template.py 的动画参数
 * 及 html_renderer 的补间一一对应，见 references/rendering.md「动画」。
 */
export const clamp01 = (x: number): number => Math.min(1, Math.max(0, x));

export const lerp = (a: number, b: number, t: number): number =>
  a + (b - a) * t;

/** ease: "power2.out"（图像入场 / apple 的 kicker 与行） */
export const power2Out = (x: number): number => 1 - Math.pow(1 - clamp01(x), 2);

/** ease: "power2.in"（旧卡剥离） */
export const power2In = (x: number): number => clamp01(x) ** 2;

/** ease: "power2.inOut"（line 档擦除：慢起—快行—慢收） */
export const power2InOut = (x: number): number => {
  const t = clamp01(x);
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
};

/** ease: "power3.out"（apple 标题：浮出落定） */
export const power3Out = (x: number): number => 1 - Math.pow(1 - clamp01(x), 3);

/** ease: "sine.out"（光晕淡入） */
export const sineOut = (x: number): number => Math.sin(clamp01(x) * Math.PI / 2);

/** ease: "sine.inOut"（光晕呼吸的半程） */
export const sineInOut = (x: number): number =>
  -(Math.cos(Math.PI * clamp01(x)) - 1) / 2;

/** ease: "back.out(1.7)"（通用标题入场）。easings.net 约定：c1=overshoot、
 * c3=c1+1，x=0 处恰好 0（overshoot 出现在 x<1 的某处，终值 1）。 */
export const backOut = (x: number, overshoot = 1.7): number => {
  const t = clamp01(x);
  const c1 = overshoot;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
};

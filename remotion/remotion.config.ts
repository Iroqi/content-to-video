import {Config} from "@remotion/cli/config";

// 浏览器解析：显式 REMOTION_BROWSER_EXECUTABLE 优先；否则交回 Remotion 的
// 默认查找链（系统 Chrome/Chromium 常规路径）。headless 沙箱里默认链找不到
// 时用 `npx remotion render --browser-executable <chrome 路径>` 覆盖。
const env = process.env.REMOTION_BROWSER_EXECUTABLE;
if (env) {
  Config.setBrowserExecutable(env);
}

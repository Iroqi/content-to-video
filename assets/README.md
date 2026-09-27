# 内置第三方脚本（离线 vendor）

`gen_hyperframes.py` 生成的 HTML 会真实执行这两个脚本。技能包按钉固版本原样内置，
让无网 / 新机器上的首次生成开箱可用，不必先设置 `CTV_ALLOW_NETWORK_ASSETS=1`。
**打包 / 分发技能时必须连同本目录一起带上**，否则每次生成都要回落到用户缓存或钉固 CDN。

| 文件 | 版本 | 来源 | 完整性 |
|---|---|---|---|
| `gsap-3.14.2.min.js` | 3.14.2 | `https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js` | sha256 `c174bfce53a729418d57a8ad8625e7247c793a22fef8e2851e3cfa3de9cd8280`，72779 字节 |
| `chartjs-4.5.1.umd.min.js` | 4.5.1 | `https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js` | sha384(base64) `jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ`，208522 字节 |

两个文件都是官方 dist 的**逐字节原样副本**，各自的许可声明保留在文件头部注释里：
GSAP 受 GreenSock Standard License（<https://gsap.com/standard-license>）约束，
Chart.js 为 MIT License。本目录是只读**依赖资产**，不是制作产物——管线只从这里读取并
拷进输出项目的 `vendor/`，任何脚本都不往本目录写文件（`guard_not_in_skill_dir` 照旧拦截）。

## 查找链与校验

`gen_hyperframes.py` 按 **输出项目 `vendor/` → 本目录 → 用户缓存
`~/.cache/content-to-video/vendor/` → 钉固 CDN（仅 `CTV_ALLOW_NETWORK_ASSETS=1`）**
的顺序取用，**每一级都重新算一遍哈希**再拷进项目：本目录的文件被改动或传输损坏
时只会回退到下一级并打 `[warn]`，不会把不符钉固字节的脚本装进项目。

因此升级版本不能只替换这里的文件——`gen_hyperframes.py` 顶部的
`GSAP_VERSION` / `GSAP_SHA256` 与
`CHARTJS_VERSION` / `CHARTJS_SHA384` 必须同步改成新版的钉固值，否则内置副本会因
哈希不符而被拒用，每次都得回落到 CDN。

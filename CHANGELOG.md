# CHANGELOG

维护辅助：只记对使用者/维护者有意义的变化，叙述细节以 `git log` 为准。

## 2026-10-01 — 架构 review 收官
- 新增 `tests/`（108 例，标准库 unittest）：golden HTML 字节快照、三份产物契约、
  降级注册表、resume 状态机、渲染后端、run.py 编排各成套件；维护者工具
  `check_docs.py`（文档章节引用防悬空）与 `check_svg.py`（手绘 SVG 静态自查）同批落地
- P1 重构（零行为改动）：`generate_html`（561 行）拆为渲染上下文+卡片函数；
  `_finalize_audio_and_manifest`（300 行）拆为 7 个阶段函数；修复渲染日志句柄泄漏
- run.py 去子进程自调：TTS/HTML 改为进程内直调（`main(argv)`），仅剩
  `npx hyperframes render` 一处外部进程；退出码与制作报告语义由编排测试钉死
- 降级词汇表收口 `scripts/_degraded.py`：新增一档降级只改一处
- `_contracts.py`（890 行）拆为 7 个契约模块：`_segments` / `_timeline` /
  `_voices` / `_validate` / `_source_schema` / `_manifest_schema` / `_images_schema`；
  `read_json_file` 并入 `_script_utils`
- 文档精简：SKILL.md 与 references/* 表格收紧、理由注释从 `_template.py` 的
  `_meta` 归位到 `templates/composition.css`
- 新增维护者工具 `scripts/package_skill.py`：生成剥离开发物
  （tests/、check_*.py、制作残渣）的技能分发 zip

## 2026-09-30 — 结构性页可换整页画布
- 版式一律由段落 `layout` 经 `_segments.seg_layout()` 分派（slot/canvas/agenda 三档），
  渲染器不再按段 id 猜版式
- 开屏/结尾支持顶层 `opening_layout` / `closing_layout: "canvas"` 换成整页海报
- 堵住两个漏洞：canvas 结构性页不再被 agenda 的配图忽略逻辑误弹出；缺图判定
  与覆盖率统计统一走 `needs_image()` 口径
- 移除字幕手动折行，`_text.py` 精简

## 2026-09-29 — 渲染与文本链路瘦身
- 文本渲染逻辑合并，清理无用脚本
- SVG 辉光/描边与渲染器解耦，对比度规则写入文档
- manifest 校验收紧（未知键、时间轴一致性）

## 2026-09-27/28 — 依赖与资产裁剪
- 砍掉随包分发的 assets；GSAP 改为按钉固哈希就地取用（vendor/ → 用户缓存 → CDN）
- 数据图/公式卡路线移除，改走手绘 SVG（`check_svg.py` 补静态门禁）
- 移除废弃的段时长字段

## 更早
直接看 `git log`。

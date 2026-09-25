/* ── Chart.js 引导：spec 数据 + 选项编译 + load 钩子 ──────────────────────
   数据与配色由 html_renderer.py 注入到本文件的两个占位符处：
   chart spec 来自 images.json，配色按主题派生（图表画在 seg-image 槽位里，
   槽位底色跟随主题，所以配色必须由调用方从主题派生后传入；这里写死任何
   一档颜色，都会在另一个主题下让坐标轴刻度几乎不可见）。
   视觉参数（字号/线宽/动画）全部来自 `_template.py` 内联版式数据的 chart 块，
   本文件不藏第二套视觉常量。 */
const ctvCharts = {};
__CTV_CHART_SPECS__
__CTV_CHART_BODY__

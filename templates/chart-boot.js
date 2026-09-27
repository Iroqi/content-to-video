/* ── Chart.js 引导：spec 数据 + 选项编译 + load 钩子 ──────────────────────
   数据与配色由 html_renderer.py 注入到本文件的占位符处：
   chart spec 逐段一行（images.json 的 chart 条目，别的什么都不注入），
   配色与视觉参数按主题/模板派生后传入（图表画在 seg-image 槽位里，
   槽位底色跟随主题，所以配色必须由调用方从主题派生后传入；这里写死任何
   一档颜色，都会在另一个主题下让坐标轴刻度几乎不可见）。
   视觉参数（字号/线宽/动画）全部来自 `_template.py` 内联版式数据的 chart 块，
   本文件不藏第二套视觉常量。
   ctvInitCharts 自带防御：页面没挂 Chart.js（本期无数据图表段）就直接返回，
   所以本骨架不分有无图表、整份内联。 */
const ctvCharts = {};
__CTV_CHART_SPECS__
const ctvInk = __CTV_CHART_INK__;
const CTV_CHART = __CTV_CHART_CFG__;
const F_TITLE = CTV_CHART.titleFontSize, F_LEGEND = CTV_CHART.legendFontSize, F_TICK = CTV_CHART.tickFontSize, F_AXTITLE = CTV_CHART.axisTitleFontSize;
const AXPAD = {x:{title:{padding:{top:F_AXTITLE*CTV_CHART.axisTitlePaddingFactor}}}, y:{title:{padding:{bottom:F_AXTITLE*CTV_CHART.axisTitlePaddingFactor}}}};
const mkScale = (spec, axisExtra={}) => ({x:{...axisExtra,ticks:{color:ctvInk.text,font:{size:F_TICK,weight:'600',family:ctvInk.mono}},grid:{color:ctvInk.gridRgba},title:{display:!!spec.x_label,text:spec.x_label||'',color:ctvInk.text,font:{size:F_AXTITLE,weight:'600'},padding:AXPAD.x.title.padding}},y:{ticks:{color:ctvInk.text,font:{size:F_TICK,weight:'600',family:ctvInk.mono}},grid:{color:ctvInk.gridRgba},title:{display:!!spec.y_label,text:spec.y_label||'',color:ctvInk.text,font:{size:F_AXTITLE,weight:'600'},padding:AXPAD.y.title.padding}}});
function ctvChartConfig(spec, accent) {
  const kind = spec.type === 'curve' ? 'line' : spec.type;
  // 图例只在多数据集时才出现。单序列图（bar/line 的常见形态）里
  // 图例必然与标题同文，只是多一块小字噪音，且在 4:3 槽位里挤压
  // 绘图区高度。例外：pie 的图例是"哪块是哪项"的唯一线索，必须留。
  const seriesCount = (spec.curve_datasets||[]).length || 1;
  const showLegend = seriesCount > 1 || spec.type === 'pie';
  const seriesColor = (seg) => ctvInk.series[seg % ctvInk.series.length];
  const options = {responsive:true, maintainAspectRatio:false, animation:false, layout:{padding:CTV_CHART.layoutPadding}, plugins:{legend:{display:showLegend,labels:{color:ctvInk.text,font:{size:F_LEGEND,weight:'600'}}}, title:{display:!!spec.title,text:spec.title||'',color:ctvInk.text,font:{size:F_TITLE,weight:'700'},padding:{top:6,bottom:18}}, tooltip:{enabled:true,titleFont:{size:F_LEGEND},bodyFont:{size:F_TICK}}}, scales:mkScale(spec)};
  if (kind === 'pie') delete options.scales;
  let data;
  if (spec.type === 'curve') { data={datasets:(spec.curve_datasets||[]).map((d,i)=>({label:d.label,data:d.data,parsing:false,borderColor:seriesColor(i),backgroundColor:'transparent',pointRadius:CTV_CHART.curvePointRadius,borderWidth:CTV_CHART.curveBorderWidth,tension:CTV_CHART.curveTension,spanGaps:true}))}; options.scales=mkScale(spec,{type:'linear'}); }
  else if (spec.type === 'scatter') data={datasets:[{label:spec.title||'',data:(spec.points||spec.values||[]).map((v,i)=>Array.isArray(v)?{x:v[0],y:v[1]}:{x:i,y:v}),backgroundColor:accent,borderColor:accent,pointRadius:CTV_CHART.scatterPointRadius}]};
  else data={labels:spec.labels||[],datasets:[{label:spec.title||'',data:spec.values||[],backgroundColor:spec.type==='pie'?(spec.labels||[]).map((_,i)=>seriesColor(i)):accent,borderColor:spec.type==='pie'?ctvInk.text:accent,borderWidth:CTV_CHART.datasetBorderWidth,fill:false,tension:CTV_CHART.datasetTension}]};
  return {type:kind,data,options};
}
// 段卡 dataset 上带 accent 色（generate_html 写的 data-accent）；缺属性时
// 回落调色板默认 accent——值由 renderer 从 _theme 传入，模板不写死色。
function ctvInitCharts(){ if(!window.Chart) return; for(const [sid,spec] of Object.entries(ctvCharts)){ const canvas=document.getElementById('chart-'+sid); if(!canvas) continue; const host=document.getElementById(sid); const accent=host?.dataset?.accent||__CTV_ACCENT_FALLBACK__; new Chart(canvas.getContext('2d'),ctvChartConfig(spec,accent)); } }
window.addEventListener('load',ctvInitCharts);

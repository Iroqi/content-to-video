/* Content-to-Video — 浏览器预览（仅人工预览用，渲染完全 inert）。
 *
 * 由 gen_hyperframes.py 生成 composition 时自动复制到 HTML 输出目录，
 * index.html 只通过外部脚本引用 preview.js（注意：注释中不要出现字面
 * 量“/script”尖括号序列，否则 hyperframes 单文件打包内联时会截断）。
 * 打开 index.html：真实浏览器直接进入预览（默认停在首帧、点击播放、窗口自适应、
 * 底部控制条、进度条、播完重播）；Hyperframes 渲染走 headless 时第一行退出，
 * 不影响出片。headless 判定用两个独立信号（navigator.webdriver 或 UA 含
 * HeadlessChrome，任一命中即退出），URL 带 ?preview 可强制进入预览做调试。
 */
(function () {
  // 显式 ?preview 参数（精确匹配键名）强制进入预览——headless 调试时用
  var wantPreview = new URLSearchParams(location.search).has('preview');
  // headless 判定用两个独立信号：navigator.webdriver 覆盖标准自动化栈；
  // UA 的 HeadlessChrome 覆盖不置 webdriver 标志的 CDP 直连环境（旧/新
  // headless 构建都带）。两者任一命中且无显式参数都不注入预览 UI，
  // 防止渲染帧里混进控制条。
  var ua = navigator.userAgent || '';
  var isHeadless = navigator.webdriver === true || ua.indexOf('HeadlessChrome') >= 0;
  if (!wantPreview && isHeadless) return;

  var root = document.getElementById('root');
  if (!root) return;
  var W = parseInt(root.dataset.width, 10);
  var H = parseInt(root.dataset.height, 10);
  if (!(W > 0 && H > 0)) throw new Error("preview: composition 未提供有效画布尺寸");
  var dur = parseFloat(root.dataset.duration || '0');
  var tl = (window.__timelines && window.__timelines['main']) || null;
  var srcEl = document.getElementById('main-audio');
  var audio = (srcEl && srcEl.getAttribute('src'))
      ? new Audio(srcEl.getAttribute('src')) : null;

  // Webview 中 composition 的固定画布尺寸可能触发自动缩放，统一改为 device-width
  var vp = document.querySelector('meta[name="viewport"]');
  if (vp) vp.setAttribute('content', 'width=device-width, initial-scale=1');

  // 字幕淡入淡出/滚动补间只属于人工预览：成片是逐帧 seek 出来的，任何按
  // 墙上时钟走的 CSS 过渡都会让"抓取到的那一帧"落后于时间线时刻（详见
  // subtitle-verse.css 的注释）。这里——即只有预览分支会执行的位置——把
  // 过渡补回来，浏览器里看仍然是平滑的。
  var smooth = document.createElement('style');
  smooth.textContent = '.verse-clip{transition:transform .45s cubic-bezier(.4,0,.2,1)}'
      + '.verse-line{transition:opacity .3s,color .3s}';
  document.head.appendChild(smooth);

  var CTRL_H = 52;
  var playing = false;
  var dragging = false;

  // 布局：上方画面 + 底部控制条（控制条在画面下方，绝不遮挡）
  var holder = document.createElement('div');
  holder.style.cssText = 'position:fixed;inset:0;display:flex;flex-direction:column;background:#0d0f16';
  var stage = document.createElement('div');
  stage.style.cssText = 'flex:1;min-height:0;position:relative;overflow:hidden';
  var ctrl = document.createElement('div');
  ctrl.innerHTML =
      '<button id="pv-play" type="button" style="border:0;background:#fff;color:#111;' +
      'border-radius:999px;padding:7px 18px;cursor:pointer;font-weight:600;font-size:13px">' +
      '▶ 播放</button>' +
      '<input id="pv-seek" type="range" min="0" max="100" value="0" step="any" ' +
      'style="width:min(320px,40vw);accent-color:#4fc3f7;cursor:pointer">' +
      '<span id="pv-time" style="min-width:118px;text-align:center;' +
      'font-variant-numeric:tabular-nums">0.0 / 0.0s</span>';
  ctrl.style.cssText = 'flex:none;height:' + CTRL_H + 'px;display:flex;' +
      'align-items:center;justify-content:center;gap:12px;background:#0d0f16;' +
      'border-top:1px solid rgba(255,255,255,0.12);color:#eef2f7;' +
      'font:13px/1.4 system-ui,sans-serif';
  document.body.appendChild(holder);
  holder.appendChild(stage);
  stage.appendChild(root);
  holder.appendChild(ctrl);

  // root 内部全是绝对定位子元素，必须显式给尺寸，否则 transform 后不可见
  root.style.width = W + 'px';
  root.style.height = H + 'px';
  root.style.position = 'absolute';
  root.style.left = '0px';
  root.style.top = '0px';
  root.style.margin = '0';

  document.documentElement.style.width = '100%';
  document.documentElement.style.height = '100%';
  document.body.style.width = '100%';
  document.body.style.height = '100%';
  document.body.style.margin = '0';
  document.body.style.overflow = 'hidden';

  var btn = ctrl.querySelector('#pv-play');
  var seek = ctrl.querySelector('#pv-seek');
  var timeEl = ctrl.querySelector('#pv-time');
  // dur 有效时进度条用秒刻度；缺失（data-duration 没写）时保持 0-100
  // 百分比刻度，拖动值按 tl.duration() 折算成秒——直接把百分比当秒喂给
  // GSAP 会被钳制不崩但语义完全错位
  var secScale = dur > 0;
  if (secScale) seek.max = dur;
  function _tlDuration() { return (tl && tl.duration()) ? tl.duration() : 0; }
  function _seekToSeconds(raw) {
    return secScale ? raw : (raw / 100) * _tlDuration();
  }
  // "播完"的判定时刻取时间线自己跑得到的终点，而不是 data-duration：契约只
  // 要求末句结束 ≤ total_duration（那是音频实测长度，比最后一个补间的终点
  // 多半还晚零点几秒），拿它判停会永远等不到 —— 播完之后按钮仍停在"⏸ 暂停"、
  // 要连点两次才开始重播。两者都测不到时返回 0，调用方按"没有终点"处理。
  function _endTime() {
    var d = _tlDuration();
    return d ? (dur > 0 ? Math.min(dur, d) : d) : dur;
  }

  function fit() {
    var sw = window.innerWidth;
    var sh = window.innerHeight - CTRL_H;
    var s = Math.min(1, sw / W, sh / H);
    root.style.transformOrigin = '0 0';
    root.style.transform = 'scale(' + s + ')';
    root.style.left = Math.max(0, (sw - W * s) / 2) + 'px';
    root.style.top = Math.max(0, (sh - H * s) / 2) + 'px';
  }
  function update() {
    var t = tl ? tl.time() : 0;
    if (!dragging) seek.value = secScale ? t : (_tlDuration() ? (t / _tlDuration()) * 100 : 0);
    timeEl.textContent = t.toFixed(1) + ' / ' + (dur || 0).toFixed(1) + 's';
  }
  function syncAudio() {
    if (!tl) return;
    var t = tl.time();
    if (audio) {
      // 音频未缓冲（readyState<2）时 currentTime 不可靠：恒为 0 会让差值
      // 恒 >0.15、每帧强制 seek，音频永远无法正常播放
      if (audio.readyState >= 2 && Math.abs(audio.currentTime - t) > 0.15) {
        audio.currentTime = t;
      }
    }
    // 收尾判定必须在 audio 判空之外：没有音轨时若跟着 early-return，
    // playing 永远停在 true，播完后按钮卡在"⏸ 暂停"、要点两次才能重播
    var endT = _endTime();
    if (endT > 0 && t >= endT - 0.05 && playing) {
      playing = false;
      btn.textContent = '▶ 播放';
      if (audio) audio.pause();
    }
  }
  function toggle() {
    if (!tl) return;
    if (playing) {
      tl.pause(); if (audio) audio.pause();
      playing = false; btn.textContent = '▶ 播放';
    } else {
      // 播完后再点播放：回到 0s 重新开始（与常见播放器一致）。终点取
      // _endTime() 而不是 data-duration——后者永远追不上，重播判定会失效。
      var endT = _endTime();
      if (endT > 0 && tl.time() >= endT - 0.05) {
        tl.time(0);
        if (audio) audio.currentTime = 0;
      }
      tl.play();
      if (audio) {
        audio.currentTime = tl.time();
        // 播放被拒（自动播放策略/音频文件坏了）时不能只吞掉：按钮会停在
        // "⏸ 暂停"、时间线继续走却全程无声。出声并回滚 UI，让"没声音"可见。
        audio.play().catch(function (err) {
          console.warn('[preview] 音频播放失败，画面将无声推进：', err);
          tl.pause();
          playing = false;
          btn.textContent = '▶ 播放';
        });
      }
      playing = true; btn.textContent = '⏸ 暂停';
    }
  }
  // 直接挂到 composition 自带的字幕 onUpdate（window.__pvUpdate 钩子）
  window.__pvUpdate = function () { update(); syncAudio(); };

  seek.addEventListener('input', function (e) {
    dragging = true;
    var v = _seekToSeconds(parseFloat(seek.value));
    if (tl) tl.time(v);
    if (audio) audio.currentTime = v;
    update();
    // 键盘方向键调整 range 只触发 input、不触发 change（鼠标释放才触发 change）。
    // 单纯靠 change reset 会让键盘调完后 dragging 永远为 true，自动更新被冻结。
    // isTrusted 之外再判一下"非指针类输入"——鼠标拖拽拖完会触发 change，
    // 键盘单步调整后不会。这里对键盘操作即时放行，避免假死。
    if (e.inputType && e.inputType.indexOf('key') === 0) {
      dragging = false;
    }
  });
  seek.addEventListener('change', function () { dragging = false; });
  // 失焦兜底：任何路径（含键盘 Esc、Tab 切走）离开 seek 后都必须解除冻结，
  // 否则 update() 永远不更新 seek.value
  seek.addEventListener('blur', function () { dragging = false; });
  btn.addEventListener('click', toggle);
  document.addEventListener('keydown', function (e) {
    if (e.code === 'Space') { e.preventDefault(); toggle(); }
  });
  window.addEventListener('resize', fit);

  fit(); update();

  // 默认不自动播放：停在首帧，等用户点击“播放”（用户手势下音频不会被浏览器拦截）。
  // 播放/暂停由 btn 点击或空格键触发，见 toggle()。
})();

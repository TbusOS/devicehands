/* DeviceHands site behaviour: language, film scroll, reveals, the round table, charts. */
(function () {
  'use strict';
  var doc = document, root = doc.documentElement;
  var $ = function (s, r) { return (r || doc).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || doc).querySelectorAll(s)); };
  var clamp = function (v, a, b) { return Math.min(b, Math.max(a, v)); };
  var reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- language ---- */
  var langBtn = $('#lang');
  if (langBtn) langBtn.addEventListener('click', function () {
    var l = root.dataset.lang === 'zh' ? 'en' : 'zh';
    root.dataset.lang = l; root.lang = l === 'en' ? 'en' : 'zh-CN';
    try { localStorage.setItem('dh-lang', l); } catch (e) {}
    doc.title = l === 'en' ? 'DeviceHands — hands for your AI' : 'DeviceHands — 给 AI 一双碰得到硬件的手';
    lit && lit.rebuild();
  });

  /* ---- film: scroll position -> progress 0..6 ---- */
  var film = $('#film'), panels = $$('.panel'), rail = $$('.rail li'), cue = $('.cue'), nav = $('#nav');
  function onScroll() {
    var y = window.scrollY, vh = window.innerHeight;
    nav.classList.toggle('is-solid', y > vh * 0.6);
    if (!film) return;
    if (root.classList.contains('is-still')) { window.__dhP = 0; return; }
    var span = film.offsetHeight - vh;
    var p = clamp((y - film.offsetTop) / span, 0, 1) * 6;
    window.__dhP = p;
    panels.forEach(function (el) {
      var at = +el.dataset.at, d = Math.abs(p - at);
      var o = at === 0 ? 1 - clamp(p / 0.45, 0, 1) : at === 6 ? clamp((p - 5.55) / 0.3, 0, 1) : 1 - clamp((d - 0.24) / 0.2, 0, 1);
      el.style.opacity = o.toFixed(3);
      el.classList.toggle('is-on', o > 0.01);
      if (el.classList.contains('panel--ch')) {
        var shift = (p - at) * -46;
        el.style.transform = window.innerWidth > 900 ? 'translateY(calc(-50% + ' + shift.toFixed(1) + 'px))' : 'translateY(' + (shift * 0.4).toFixed(1) + 'px)';
      } else if (at === 0) {
        el.style.transform = window.innerWidth > 900 ? 'translateY(calc(-50% - ' + (p * 60).toFixed(1) + 'px))' : 'translateY(' + (-p * 60).toFixed(1) + 'px)';
      }
    });
    var scrim = film.querySelector('.stage__scrim'); if (scrim) { scrim.style.setProperty('--side', (1 - clamp((p - 5.3) / 0.5, 0, 1)).toFixed(3)); scrim.style.setProperty('--low', (clamp((p - 0.3) / 0.4, 0, 1) * (1 - clamp((p - 5.3) / 0.4, 0, 1))).toFixed(3)); }
    var k = Math.round(p);
    rail.forEach(function (li, i) { li.classList.toggle('is-on', i === k); });
    if (cue) cue.style.opacity = p < 0.15 ? 1 : 0;
  }
  addEventListener('scroll', onScroll, { passive: true });
  addEventListener('resize', onScroll);

  /* ---- statement: words light up as they pass ---- */
  var lit = (function () {
    var host = $('[data-lit]'); if (!host) return null;
    var words = [];
    function split(el) {
      if (el.dataset.done) return; el.dataset.done = 1;
      var txt = el.textContent.trim(), isZh = el.classList.contains('zh');
      var parts = isZh ? Array.from(txt) : txt.split(/(\s+)/);
      el.textContent = '';
      parts.forEach(function (w) {
        if (/^\s+$/.test(w)) { el.appendChild(doc.createTextNode(' ')); return; }
        var s = doc.createElement('span'); s.className = 'w'; s.textContent = w; el.appendChild(s);
      });
      /* the brand name is the gold part */
      var all = $$('.w', el), joined = '', idx = [];
      all.forEach(function (s, i) { joined += s.textContent + (isZh ? '' : ' '); idx.push(joined.length); });
      var a = joined.indexOf('DeviceHands');
      if (a >= 0) all.forEach(function (s, i) { var end = idx[i], start = end - s.textContent.length - (isZh ? 0 : 1); if (start >= a && start < a + 11) s.classList.add('is-gold'); });
    }
    function rebuild() { $$('span.zh, span.en', host).forEach(split); words = $$('.w', host).filter(function (w) { return w.offsetParent !== null; }); update(); }
    function update() {
      if (!words.length) return;
      var r = host.getBoundingClientRect(), vh = window.innerHeight;
      var t = clamp((vh * 0.9 - r.top) / (r.height * 0.9 + vh * 0.25), 0, 1);
      var n = Math.round(t * words.length * 1.08);
      words.forEach(function (w, i) { w.classList.toggle('is-lit', reduced || i < n); });
    }
    addEventListener('scroll', update, { passive: true });
    return { rebuild: rebuild };
  })();

  /* ---- reveal on scroll ---- */
  var io = new IntersectionObserver(function (es) {
    es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('is-in'); io.unobserve(e.target); } });
  }, { threshold: 0.18, rootMargin: '0px 0px -6% 0px' });
  $$('.rv').forEach(function (el) { io.observe(el); });

  /* ---- round table: play the concept conversation ---- */
  (function () {
    var host = $('#table'); if (!host) return;
    var items = $$('.thread__list li', host), seats = $$('.seat', host), beams = $$('.beam', host), timer = 0, started = false;
    function speak(seat) {
      seats.forEach(function (s) { s.classList.toggle('is-on', s.dataset.seat === seat || (seat === 'report' && s.dataset.seat === 'you')); });
      beams.forEach(function (b) { b.classList.toggle('is-on', b.dataset.seat === seat || (seat === 'report' && b.dataset.seat === 'you')); });
    }
    function play() {
      clearTimeout(timer);
      items.forEach(function (li) { li.classList.remove('is-in'); });
      var i = 0;
      (function step() {
        if (i >= items.length) { timer = setTimeout(function () { speak(''); }, 2600); return; }
        var li = items[i++]; li.classList.add('is-in'); speak(li.dataset.seat);
        timer = setTimeout(step, reduced ? 0 : 1900 + li.textContent.length * 9);
      })();
    }
    new IntersectionObserver(function (es) { if (es[0].isIntersecting && !started) { started = true; play(); } }, { threshold: 0.35 }).observe(host);
    var rp = $('#replay'); if (rp) rp.addEventListener('click', play);
  })();

  /* ---- charts: timeline ticks and the flame graph are generated ---- */
  (function () {
    var NS = 'http://www.w3.org/2000/svg', seed = 11;
    function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }
    var ticks = $('#ticks');
    if (ticks) {
      [58, 118, 178, 238].forEach(function (y, lane) {
        var x = 72;
        while (x < 540) {
          var dense = x > 318 && x < 414, w = 2 + rnd() * (lane === 3 ? 3 : 14);
          var r = doc.createElementNS(NS, 'rect');
          r.setAttribute('x', x.toFixed(1)); r.setAttribute('y', y - 9); r.setAttribute('width', w.toFixed(1)); r.setAttribute('height', 18); r.setAttribute('rx', 2);
          if (dense && rnd() < 0.55) r.setAttribute('class', lane === 2 && rnd() < 0.5 ? 'err' : 'hot');
          if (x < 414 || x > 440) ticks.appendChild(r);
          x += w + (dense ? 2 + rnd() * 5 : 5 + rnd() * (lane === 3 ? 60 : 22));
        }
      });
    }
    var flame = $('#flame');
    if (flame) {
      var names = ['main', 'sys_read', 'vfs_read', 'mmc_request', 'schedule', 'wait_event', 'irq', 'memcpy', 'ext4_file', 'blk_mq', 'do_page', 'alloc', 'kworker', 'binder', 'futex'];
      var rowH = 26, n = 0;
      (function grow(x, w, depth) {
        if (depth > 8 || w < 14) return;
        var r = doc.createElementNS(NS, 'rect'), hot = depth > 2 && w > 60 && rnd() < 0.3;
        var y = 282 - (depth + 1) * (rowH + 3), d = depth * 110 + n * 6; n++;
        r.setAttribute('x', x.toFixed(1)); r.setAttribute('y', y); r.setAttribute('width', (w - 2).toFixed(1)); r.setAttribute('height', rowH); r.setAttribute('rx', 4);
        r.setAttribute('fill', hot ? '#ff7a55' : 'hsl(' + (36 + rnd() * 8) + ',' + (58 + rnd() * 20) + '%,' + (48 + depth * 3.4) + '%)');
        r.style.setProperty('--d', d); flame.appendChild(r);
        if (w > 54) {
          var t = doc.createElementNS(NS, 'text');
          t.setAttribute('x', (x + 7).toFixed(1)); t.setAttribute('y', y + 17); t.textContent = names[Math.floor(rnd() * names.length)];
          t.style.setProperty('--d', d); flame.appendChild(t);
        }
        var used = 0, kids = 1 + Math.floor(rnd() * 3);
        for (var k = 0; k < kids; k++) {
          var cw = (w - used) * (0.34 + rnd() * 0.5); if (k === kids - 1) cw = Math.min(cw, w - used);
          grow(x + used, cw, depth + 1); used += cw;
        }
      })(10, 540, 0);
    }
  })();

  /* ---- floating screens follow the pointer a little ---- */
  (function () {
    var host = $('#screens'); if (!host || reduced || window.innerWidth <= 900) return;
    host.addEventListener('pointermove', function (e) {
      var r = host.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
      $$('.screen', host).forEach(function (s) {
        s.style.setProperty('--ry', (base(s) + x * -9).toFixed(2) + 'deg');
        s.style.setProperty('--rx', (y * 7).toFixed(2) + 'deg');
      });
    });
    host.addEventListener('pointerleave', function () {
      $$('.screen', host).forEach(function (s) { s.style.setProperty('--ry', base(s) + 'deg'); s.style.setProperty('--rx', '0deg'); });
    });
    function base(s) { return s.classList.contains('screen--b') ? 26 : s.classList.contains('screen--c') ? -26 : 0; }
  })();

  /* ---- the stack opens as it scrolls through ---- */
  (function () {
    var stack = $('#stack'); if (!stack) return;
    function upd() {
      var r = stack.getBoundingClientRect(), vh = window.innerHeight;
      stack.style.setProperty('--open', reduced ? 1 : clamp((vh * 0.92 - r.top) / (vh * 0.62), 0, 1).toFixed(3));
    }
    addEventListener('scroll', upd, { passive: true }); addEventListener('resize', upd); upd();
  })();

  /* ---- copy ---- */
  $$('.copy').forEach(function (b) {
    b.addEventListener('click', function () {
      var src = doc.getElementById(b.dataset.copy); if (!src) return;
      var text = Array.prototype.map.call(src.querySelectorAll('code'), function (c) { return c.innerText; }).join('\n')
        .split('\n').filter(function (l) { return l.trim() && l.trim().charAt(0) !== '#'; }).join('\n');
      (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () {
        b.classList.add('is-done'); var old = b.innerHTML; b.textContent = '✓';
        setTimeout(function () { b.innerHTML = old; b.classList.remove('is-done'); }, 1400);
      }).catch(function () {});
    });
  });

  lit && lit.rebuild();
  onScroll();
})();

/* Opening scene: Napoleon and Kutuzov cross sabres, sparks fly, they spring apart, the globe rises
   between them. 3.5 s, once per browser (the first visit only); skipped on "reduce motion", by a click or Esc. */
(function () {
  "use strict";
  const el = document.getElementById("intro");
  if (!el) return;
  let seen = false;
  try { seen = localStorage.getItem("ws-intro") === "1"; } catch (e) { /* storage off: show it */ }
  if (seen || matchMedia("(prefers-reduced-motion: reduce)").matches || new URLSearchParams(location.search).has("nointro")) {
    el.remove(); return;
  }
  try { localStorage.setItem("ws-intro", "1"); } catch (e) { /* ignore */ }
  el.hidden = false;
  document.documentElement.classList.add("intro-on");

  let done = false;
  const timers = [];
  function finish() {
    if (done) return;
    done = true;
    timers.forEach(clearTimeout);
    el.classList.add("out");
    document.documentElement.classList.remove("intro-on");
    document.documentElement.classList.add("intro-reveal");
    setTimeout(() => el.remove(), 650);
  }
  el.addEventListener("click", finish);
  addEventListener("keydown", e => { if (e.key === "Escape" || e.key === "Enter" || e.key === " ") finish(); }, {once: true});

  // sparks, on a canvas over the clash point
  const cv = el.querySelector("canvas"), ctx = cv.getContext("2d");
  function sparks() {
    const r = cv.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
    cv.width = r.width * dpr; cv.height = r.height * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const svg = el.querySelector("svg").getBoundingClientRect();
    const cx = svg.left - r.left + svg.width * 0.5, cy = svg.top - r.top + svg.height * (215 / 600);
    const ps = Array.from({length: 56}, () => {
      const a = Math.random() * Math.PI * 2, v = 120 + Math.random() * 380;
      return {x: cx, y: cy, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 120, life: .45 + Math.random() * .6};
    });
    let prev = performance.now();
    const start = prev;
    (function tick(now) {
      const dt = Math.min(.04, (now - prev) / 1000); prev = now;
      ctx.clearRect(0, 0, r.width, r.height);
      let alive = 0;
      for (const p of ps) {
        if (p.life <= 0) continue;
        alive++;
        p.life -= dt; p.vy += 520 * dt; p.x += p.vx * dt; p.y += p.vy * dt;
        const k = Math.max(0, p.life);
        ctx.strokeStyle = `rgba(255,${200 + Math.round(55 * k)},${120 + Math.round(100 * k)},${Math.min(1, k * 1.6)})`;
        ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x - p.vx * .025, p.y - p.vy * .025); ctx.stroke();
      }
      // the flash
      const f = Math.max(0, 1 - (now - start) / 260);
      if (f > 0) {
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, 90);
        g.addColorStop(0, `rgba(255,245,210,${f})`); g.addColorStop(1, "rgba(255,200,90,0)");
        ctx.fillStyle = g; ctx.fillRect(cx - 90, cy - 90, 180, 180);
      }
      if (alive && !done) requestAnimationFrame(tick);
    })(prev);
  }
  timers.push(setTimeout(() => { el.classList.add("clash"); sparks(); }, 1400));
  timers.push(setTimeout(() => el.classList.add("apart"), 1800));
  timers.push(setTimeout(() => el.classList.add("globe"), 2300));
  timers.push(setTimeout(finish, 3600));
})();

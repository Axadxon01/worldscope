/* WorldScope globe: an orthographic globe on a canvas (d3-geo), with the AI map's links drawn as
   great-circle arcs and a small living sign on each one: tanks and a plane for conflict, a market
   stall for trade, people at a palace for diplomacy, and so on. No WebGL, no tile service.

   const g = WSGlobe(canvas, {topo, geo, coords, names, links, colors, onLink, onCountry, compact});
   g.focusLink(i) · g.focusCountry(iso) · g.setLayer(null | {ISO3: [value, color, label]}) · g.setSigns(bool)
*/
(function () {
  "use strict";
  const TAU = Math.PI * 2, RAD = Math.PI / 180;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const MAX_ANIMATED = 8;
  // what each kind of link is called, in words a child knows
  const WORDS = {
    conflict: ["⚔️", "Urush"], trade: ["🚢", "Savdo"], diplomacy: ["🤝", "Muzokara"], cooperation: ["🏗️", "Hamkorlik"],
    aid: ["📦", "Yordam"], pressure: ["✋", "Bosim"], rivalry: ["♟️", "Raqobat"], dependence: ["⛓️", "Qaramlik"],
    news: ["📰", "Yangilikda"],
  };
  const EXPLAIN = {
    news: "Bu ikki davlat haqida gazetalar birga yozmoqda. Nima haqida ekanini ro'yxatdan o'qing.",
    conflict: "Bu ikki tomon o'rtasida urush yoki qattiq janjal bor.",
    trade: "Bu davlatlar bir-biriga mol sotadi va sotib oladi.",
    diplomacy: "Bu davlatlar rahbarlari uchrashib, gaplashib kelishmoqda.",
    cooperation: "Bu davlatlar birga nimadir qurmoqda yoki birga ishlamoqda.",
    aid: "Bir davlat ikkinchisiga pul, oziq-ovqat yoki dori bilan yordam bermoqda.",
    pressure: "Bir davlat ikkinchisini biror narsaga majburlamoqchi: jazo, tahdid yoki talab.",
    rivalry: "Bu davlatlar kim kuchliroq ekanida bellashmoqda.",
    dependence: "Bir davlat ikkinchisiga juda bog'liq: usiz qiynaladi.",
  };

  function theme() {
    const light = document.documentElement.dataset.theme === "light";
    return light
      ? {sea1: "#d8e3ea", sea2: "#b8c9d6", land: "#f3e9cf", hot: "#f2d48a", border: "#9c8a66", grat: "rgba(110,90,50,.18)",
         rim: "rgba(138,106,18,.55)", text: "#2b2116", halo: "rgba(255,248,230,.9)"}
      : {sea1: "#13284a", sea2: "#081226", land: "#1c2d4d", hot: "#4a3d14", border: "#3e5580", grat: "rgba(212,175,55,.10)",
         rim: "rgba(212,175,55,.55)", text: "#efe3c2", halo: "rgba(8,14,28,.85)"};
  }

  // ---------------------------------------------------------------- the little signs
  // Each draws centred on (0,0) in a box about 44 × 30 units; ctx is already scaled and translated.
  const ink = "#1b1b1b";
  function tank(c, x, dir, t, fire) {
    c.save(); c.translate(x, 6); c.scale(dir, 1);
    c.fillStyle = "#5b6b3a"; c.strokeStyle = ink; c.lineWidth = 1;
    c.beginPath(); c.roundRect(-9, -3, 18, 7, 3); c.fill(); c.stroke();           // hull
    c.fillStyle = "#2a2a2a"; c.fillRect(-8, 3, 16, 3);                              // tracks
    c.fillStyle = "#6f8046"; c.beginPath(); c.roundRect(-4, -7, 9, 5, 2); c.fill(); c.stroke(); // turret
    c.fillStyle = ink; c.fillRect(4, -6, 9, 1.8);                                   // barrel
    if (fire) {                                                                      // muzzle flash
      c.fillStyle = "#ffd166"; c.beginPath(); c.arc(15, -5, 2.6 + Math.sin(t * 40), 0, TAU); c.fill();
      c.fillStyle = "#ff7b00"; c.beginPath(); c.arc(15, -5, 1.4, 0, TAU); c.fill();
    }
    c.restore();
  }
  function plane(c, t) {
    const px = ((t * 22) % 60) - 30, py = -14 + Math.sin(t * 3) * 1.5;
    c.save(); c.translate(px, py); c.fillStyle = "#cfd8e3"; c.strokeStyle = ink; c.lineWidth = .8;
    c.beginPath(); c.moveTo(8, 0); c.lineTo(-7, -1.6); c.lineTo(-7, 1.6); c.closePath(); c.fill(); c.stroke();
    c.beginPath(); c.moveTo(1, 0); c.lineTo(-3, -6); c.lineTo(-5, -6); c.lineTo(-2, 0); c.lineTo(-5, 6); c.lineTo(-3, 6); c.closePath(); c.fill(); c.stroke();
    c.restore();
  }
  function person(c, x, y, color, bob) {
    c.fillStyle = color; c.strokeStyle = ink; c.lineWidth = .8;
    c.beginPath(); c.arc(x, y - 9 + bob, 2.6, 0, TAU); c.fill(); c.stroke();
    c.beginPath(); c.roundRect(x - 2.6, y - 6 + bob, 5.2, 8, 2); c.fill(); c.stroke();
  }
  const SIGNS = {
    conflict(c, t) {
      const phase = Math.floor(t * 1.6) % 2;
      tank(c, -13, 1, t, phase === 0 && (t * 1.6) % 1 < .25);
      tank(c, 13, -1, t, phase === 1 && (t * 1.6) % 1 < .25);
      plane(c, t);
    },
    pressure(c, t) {
      c.fillStyle = "#6b4f2a"; c.fillRect(-1, -14, 2, 26);
      c.fillStyle = "#c0392b"; c.beginPath(); c.moveTo(1, -14);
      for (let i = 0; i <= 10; i++) c.lineTo(1 + i * 1.6, -14 + Math.sin(t * 6 + i * .7) * 1.4);
      for (let i = 10; i >= 0; i--) c.lineTo(1 + i * 1.6, -5 + Math.sin(t * 6 + i * .7) * 1.4);
      c.closePath(); c.fill();
    },
    rivalry(c, t) {
      const hopA = Math.max(0, Math.sin(t * 4)) * 4, hopB = Math.max(0, -Math.sin(t * 4)) * 4;
      for (const [x, h, col] of [[-8, hopA, "#f4efe2"], [8, hopB, "#2b2b2b"]]) {
        c.fillStyle = col; c.strokeStyle = col === "#2b2b2b" ? "#999" : ink; c.lineWidth = .8;
        c.beginPath(); c.arc(x, -6 - h, 3.2, 0, TAU); c.fill(); c.stroke();
        c.beginPath(); c.moveTo(x - 4, 8 - h); c.lineTo(x - 2, -3 - h); c.lineTo(x + 2, -3 - h); c.lineTo(x + 4, 8 - h); c.closePath(); c.fill(); c.stroke();
      }
    },
    trade(c, t) {
      c.strokeStyle = ink; c.lineWidth = .8;
      for (let i = 0; i < 5; i++) {                                                  // striped awning
        c.fillStyle = i % 2 ? "#f4efe2" : "#1f8a5b";
        c.beginPath(); c.moveTo(-14 + i * 5.6, -10); c.lineTo(-8.4 + i * 5.6, -10); c.lineTo(-8.4 + i * 5.6, -5); c.lineTo(-14 + i * 5.6, -5); c.fill();
      }
      c.strokeRect(-14, -10, 28, 5);
      c.fillStyle = "#8a5a2b"; c.fillRect(-12, 2, 24, 4); c.fillRect(-13, -5, 1.5, 11); c.fillRect(11.5, -5, 1.5, 11);
      c.fillStyle = "#f2a93b"; c.beginPath(); c.arc(-5, 1, 1.8, 0, TAU); c.arc(0, 1, 1.8, 0, TAU); c.arc(5, 1, 1.8, 0, TAU); c.fill();
      const walk = Math.sin(t * 2) * 6;
      person(c, -18 + walk, 12, "#3b6fd8", Math.abs(Math.sin(t * 8)) * .8);
      person(c, 18 - walk, 12, "#d4af37", Math.abs(Math.cos(t * 8)) * .8);
    },
    diplomacy(c, t) {
      c.fillStyle = "#e9dfc6"; c.strokeStyle = ink; c.lineWidth = .8;                  // palace
      c.beginPath(); c.moveTo(-14, -6); c.lineTo(0, -14); c.lineTo(14, -6); c.closePath(); c.fill(); c.stroke();
      c.fillRect(-13, -6, 26, 2);
      for (let i = 0; i < 4; i++) c.fillRect(-11 + i * 7, -4, 2.4, 10);
      c.fillRect(-14, 6, 28, 2); c.strokeRect(-14, 6, 28, 2);
      const shake = Math.sin(t * 10) * .8;
      person(c, -5, 18, "#3b6fd8", 0); person(c, 5, 18, "#b3261e", 0);
      c.strokeStyle = ink; c.lineWidth = 1.2; c.beginPath(); c.moveTo(-3, 12 + shake); c.lineTo(3, 12 - shake); c.stroke();
    },
    cooperation(c, t) {
      c.strokeStyle = "#d4af37"; c.lineWidth = 1.6;                                    // crane
      c.beginPath(); c.moveTo(-10, 12); c.lineTo(-10, -14); c.lineTo(12, -14); c.stroke();
      const up = (t * 6) % 14;
      c.lineWidth = .8; c.beginPath(); c.moveTo(6, -14); c.lineTo(6, -2 - up + 6); c.stroke();
      c.fillStyle = "#c97b3a"; c.fillRect(3, -2 - up + 6, 6, 4);
      c.fillStyle = "#8f9bb3"; c.fillRect(-4, 4, 16, 8); c.strokeStyle = ink; c.strokeRect(-4, 4, 16, 8);
      c.fillStyle = "#f2d48a"; c.fillRect(-1, 6, 3, 3); c.fillRect(6, 6, 3, 3);
    },
    aid(c, t) {
      const x = ((t * 14) % 40) - 20;
      c.save(); c.translate(x, 4); c.fillStyle = "#f4efe2"; c.strokeStyle = ink; c.lineWidth = .8;
      c.fillRect(-10, -7, 13, 9); c.strokeRect(-10, -7, 13, 9);
      c.fillStyle = "#c0392b"; c.fillRect(-5, -6, 3, 7); c.fillRect(-7, -4, 7, 3);
      c.fillStyle = "#2f6f9f"; c.fillRect(3, -4, 7, 6); c.strokeRect(3, -4, 7, 6);
      c.fillStyle = ink; c.beginPath(); c.arc(-6, 3, 2, 0, TAU); c.arc(6, 3, 2, 0, TAU); c.fill();
      c.restore();
    },
    dependence(c, t) {
      const pull = Math.sin(t * 2) * 2;
      c.strokeStyle = "#b8b8b8"; c.lineWidth = 2.2;
      for (const [x, rot] of [[-9 - pull, 0], [0, Math.PI / 2], [9 + pull, 0]]) {
        c.save(); c.translate(x, 0); c.rotate(rot); c.beginPath(); c.ellipse(0, 0, 6, 3.4, 0, 0, TAU); c.stroke(); c.restore();
      }
    },
  };

  // ---------------------------------------------------------------- the globe
  window.WSGlobe = function (canvas, opt) {
    const ctx = canvas.getContext("2d");
    const geo = topojson.feature(opt.topo, opt.topo.objects.countries).features;
    const isoOf = f => opt.geo[f.properties.name];
    const byIso = {};
    for (const f of geo) { const iso = isoOf(f); if (iso) byIso[iso] = f; }
    const proj = d3.geoOrthographic().clipAngle(90).precision(0.4);
    const path = d3.geoPath(proj, ctx);
    const grat = d3.geoGraticule10();
    let W = 0, H = 0, R = 0, zoom = 1, rot = [-60, -25, 0], dragging = null, lastTouch = 0, anim = null, hover = null;
    let layer = null, signs = true, focused = -1, focusIso = null, hitSigns = [], solo = null, overSign = false;
    const involved = new Set();
    let links = [];
    function build(list) {
      involved.clear();
      links = (list || []).map((l, i) => {
        const a = opt.coords[l.from], b = opt.coords[l.to];
        if (!a || !b) return null;
        involved.add(l.from); involved.add(l.to);
        const A = [a[1], a[0]], B = [b[1], b[0]], interp = d3.geoInterpolate(A, B);
        const n = Math.max(8, Math.round(d3.geoDistance(A, B) * 30));
        return {i, l, interp, line: {type: "LineString", coordinates: d3.range(n + 1).map(k => interp(k / n))}, mid: interp(.5), end: B, start: A};
      }).filter(Boolean);
    }
    build(opt.links);
    const co = (opt.co || []).map(c => {
      const a = opt.coords[c.from], b = opt.coords[c.to];
      return a && b ? {type: "LineString", coordinates: [[a[1], a[0]], [b[1], b[0]]]} : null;
    }).filter(Boolean);

    if (links.length) {                                       // start looking at the first link
      const m = links[0].mid; rot = [-m[0], -m[1] * .6, 0];
    } else if (opt.start) rot = [-opt.start[1], -opt.start[0] * .6, 0];

    // leave room for a side panel that covers the left of the canvas
    const cx = () => W / 2 + (opt.panel && W > 900 ? Math.min(190, opt.panel / 2) : 0);
    function size() {
      const r = canvas.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
      W = r.width; H = r.height;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      R = Math.min(W, H) * .46;
    }

    const visible = p => d3.geoDistance(p, [-rot[0], -rot[1]]) < Math.PI / 2 * .93;

    function draw(now) {
      const t = now / 1000, th = theme();
      proj.translate([cx(), H / 2]).scale(R * zoom).rotate(rot);
      ctx.clearRect(0, 0, W, H);
      // sea, with a soft light from the upper left
      const g = ctx.createRadialGradient(cx() - R * zoom * .35, H / 2 - R * zoom * .4, R * zoom * .1, cx(), H / 2, R * zoom);
      g.addColorStop(0, th.sea1); g.addColorStop(1, th.sea2);
      ctx.beginPath(); path({type: "Sphere"}); ctx.fillStyle = g; ctx.fill();
      ctx.beginPath(); path(grat); ctx.strokeStyle = th.grat; ctx.lineWidth = .6; ctx.stroke();
      // countries
      for (const f of geo) {
        const iso = isoOf(f);
        let fill = th.land;
        if (layer) { const v = iso && layer[iso]; if (v && v[1]) fill = v[1]; }
        else if (iso && involved.has(iso)) fill = th.hot;
        if (iso && iso === focusIso) fill = th.hot;
        ctx.beginPath(); path(f);
        ctx.globalAlpha = layer && layer[iso] ? .88 : 1;
        ctx.fillStyle = fill; ctx.fill(); ctx.globalAlpha = 1;
        ctx.strokeStyle = iso && (iso === hover || iso === focusIso) ? "#d4af37" : th.border;
        ctx.lineWidth = iso && (iso === hover || iso === focusIso) ? 1.6 : .5; ctx.stroke();
      }
      ctx.beginPath(); path({type: "Sphere"}); ctx.strokeStyle = th.rim; ctx.lineWidth = 1.4; ctx.stroke();
      // co-mentions: thin dashed grey
      if (!links.length && co.length) {
        ctx.setLineDash([3, 5]); ctx.strokeStyle = "rgba(160,170,190,.7)"; ctx.lineWidth = 1;
        for (const c of co) { ctx.beginPath(); path(c); ctx.stroke(); }
        ctx.setLineDash([]);
      }
      // arcs, with a travelling dash so the direction shows
      for (const k of links) {
        if (solo && k.l.type !== solo) continue;
        const color = k.l.color || (opt.colors || {})[k.l.type] || "#d4af37", dim = focused >= 0 && focused !== k.i;
        const width = k.l.width || 2.2;
        ctx.globalAlpha = dim ? .25 : .95;
        ctx.beginPath(); path(k.line); ctx.strokeStyle = color; ctx.lineWidth = focused === k.i ? width + 1 : width;
        ctx.setLineDash(k.l.confidence === "low" ? [5, 6] : []); ctx.stroke();
        if (!reduced && !dim) {  // a small light travels from the first country to the second
          const f = ((t * 0.22 + k.i * 0.37) % 1), pos = k.interp(f);
          if (visible(pos)) {
            const p = proj(pos);
            const g2 = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], 7);
            g2.addColorStop(0, "rgba(255,255,255,.95)"); g2.addColorStop(1, "rgba(255,255,255,0)");
            ctx.fillStyle = g2; ctx.beginPath(); ctx.arc(p[0], p[1], 7, 0, TAU); ctx.fill();
          }
        }
        ctx.setLineDash([]);
        if (visible(k.end)) {                                  // arrow end
          const p = proj(k.end); ctx.fillStyle = color; ctx.beginPath(); ctx.arc(p[0], p[1], 3.2, 0, TAU); ctx.fill();
        }
        ctx.globalAlpha = 1;
      }
      // the signs: nearest to the centre first, at most MAX_ANIMATED of them alive
      hitSigns = [];
      const captions = [];                                     // [x, y, width] of the word pills, so names dodge them
      if (signs) {
        const order = links.filter(k => visible(k.mid) && (!solo || k.l.type === solo))
          .sort((a, b) => d3.geoDistance(a.mid, [-rot[0], -rot[1]]) - d3.geoDistance(b.mid, [-rot[0], -rot[1]]));
        const placed = [];                                     // keep pictures from piling on each other
        order.forEach((k, n) => {
          const p = proj(k.mid), draw = k.l.sign === false ? null : SIGNS[k.l.type];
          if (!draw && k.l.type !== "news") return;
          const conf = k.l.confidence === "high" ? 1 : k.l.confidence === "low" ? .7 : .85;
          const s = (opt.compact ? 1.05 : 1.15) * conf * Math.min(1.6, Math.max(.8, zoom));
          const crowded = !draw || k.i !== focused && placed.some(q => Math.abs(q[0] - p[0]) < 84 * s && Math.abs(q[1] - p[1]) < 56 * s);
          if (crowded) {                                       // too close to a bigger picture: a small dot instead
            const w0 = WORDS[k.l.type];
            ctx.globalAlpha = focused >= 0 && focused !== k.i ? .35 : .95;
            ctx.fillStyle = k.l.color || (opt.colors || {})[k.l.type] || "#d4af37";
            ctx.beginPath(); ctx.arc(p[0], p[1], 9, 0, TAU); ctx.fill();
            if (w0) { ctx.font = "11px sans-serif"; ctx.textAlign = "center"; ctx.fillText(w0[0], p[0], p[1] + 4); }
            ctx.globalAlpha = 1;
            hitSigns.push({i: k.i, x: p[0], y: p[1], r: 12});
            return;
          }
          placed.push(p);
          const alive = n < MAX_ANIMATED && !reduced;
          ctx.save(); ctx.translate(p[0], p[1] - 10 * s);
          ctx.globalAlpha = (focused >= 0 && focused !== k.i ? .35 : 1) * (k.l.confidence === "low" ? .75 : 1);
          ctx.fillStyle = th.halo; ctx.beginPath(); ctx.ellipse(0, 2 * s, 26 * s, 20 * s, 0, 0, TAU); ctx.fill();
          ctx.scale(s, s); draw(ctx, alive ? t + k.i * .37 : 0);
          ctx.restore();
          const w = WORDS[k.l.type];
          if (w && !k.l.title?.includes("$")) {  // a word under the picture: "⚔️ Urush"
            const label = `${w[0]} ${w[1]}`, y = p[1] + 14 * s;
            ctx.font = "700 11.5px Inter, sans-serif"; ctx.textAlign = "center";
            const tw = ctx.measureText(label).width + 12;
            ctx.globalAlpha = focused >= 0 && focused !== k.i ? .35 : .95;
            ctx.fillStyle = k.l.color || (opt.colors || {})[k.l.type] || "#d4af37";
            ctx.beginPath(); ctx.roundRect(p[0] - tw / 2, y - 9, tw, 18, 9); ctx.fill();
            ctx.fillStyle = "#fff"; ctx.fillText(label, p[0], y + 4); ctx.globalAlpha = 1;
            captions.push([p[0], y, tw]);
          }
          if (!opt.compact) {                                   // its number, as in the side list
            ctx.fillStyle = k.l.color || (opt.colors || {})[k.l.type] || "#d4af37";
            ctx.beginPath(); ctx.arc(p[0] + 22 * s, p[1] - 24 * s, 7, 0, TAU); ctx.fill();
            ctx.fillStyle = "#fff"; ctx.font = "600 9.5px Inter, sans-serif"; ctx.textAlign = "center";
            ctx.fillText(String(k.i + 1), p[0] + 22 * s, p[1] - 24 * s + 3.3);
          }
          hitSigns.push({i: k.i, x: p[0], y: p[1] - 10 * s, r: 26 * s});
        });
      }
      // names of the countries in play
      ctx.font = "600 11px Inter, sans-serif"; ctx.textAlign = "center";
      const named = new Set([...(opt.compact && !focusIso ? [] : involved), ...(hover ? [hover] : []), ...(focusIso ? [focusIso] : [])]);
      for (const iso of named) {
        const c = opt.coords[iso]; if (!c) continue;
        const ll = [c[1], c[0]]; if (!visible(ll)) continue;
        const p = proj(ll), label = (opt.names || {})[iso] || iso;
        if (iso !== hover && captions.some(q => Math.abs(q[0] - p[0]) < q[2] / 2 + 30 && Math.abs(q[1] - p[1] - 14) < 16)) continue;
        ctx.lineWidth = 3; ctx.strokeStyle = th.halo; ctx.strokeText(label, p[0], p[1] + 14);
        ctx.fillStyle = th.text; ctx.fillText(label, p[0], p[1] + 14);
      }
      if (hover && layer && layer[hover]) {                     // the layer's value next to the pointer
        const v = layer[hover]; tip(`${(opt.names || {})[hover] || hover}: ${v[2] ?? v[0]}`);
      } else if (hover) tip((opt.names || {})[hover] || hover); else tip("");
    }

    // tooltip
    const tipEl = document.createElement("div"); tipEl.className = "globe-tip"; canvas.parentNode.appendChild(tipEl);
    let tipXY = [0, 0];
    function tip(text) {
      tipEl.textContent = text; tipEl.hidden = !text;
      tipEl.style.left = tipXY[0] + 14 + "px"; tipEl.style.top = tipXY[1] + 10 + "px";
    }

    // motion
    let last = performance.now(), idleSince = performance.now();
    function frame(now) {
      const dt = Math.min(.05, (now - last) / 1000); last = now;
      if (anim) {
        const k = Math.min(1, (now - anim.t0) / anim.ms), e = k < .5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
        rot = [anim.from[0] + (anim.to[0] - anim.from[0]) * e, anim.from[1] + (anim.to[1] - anim.from[1]) * e, 0];
        zoom = anim.z0 + (anim.z1 - anim.z0) * e;
        if (k >= 1) anim = null;
      } else if (!dragging && !reduced && focused < 0 && !focusIso && now - idleSince > 3500) {
        if (!hover && !overSign) rot = [rot[0] + dt * 3.5, rot[1], 0];  // a slow turn while nobody is touching it
      }
      draw(now);
      if (!document.hidden) requestAnimationFrame(frame); else paused = true;
    }
    let paused = false;
    document.addEventListener("visibilitychange", () => { if (!document.hidden && paused) { paused = false; last = performance.now(); requestAnimationFrame(frame); } });

    function turnTo(lonlat, z = zoom) {
      let to0 = -lonlat[0];
      while (to0 - rot[0] > 180) to0 -= 360;
      while (rot[0] - to0 > 180) to0 += 360;
      anim = {from: rot.slice(), to: [to0, Math.max(-60, Math.min(60, -lonlat[1])), 0], t0: performance.now(), ms: reduced ? 1 : 900, z0: zoom, z1: z};
    }

    // input
    const pt = e => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    if (opt.compact) canvas.style.touchAction = "pan-y";  // a finger moving up or down scrolls the page
    canvas.addEventListener("pointerdown", e => {
      dragging = {p: pt(e), rot: rot.slice(), moved: false, touch: e.pointerType === "touch"};
      canvas.setPointerCapture(e.pointerId); anim = null;
    });
    canvas.addEventListener("pointercancel", () => { dragging = null; });
    canvas.addEventListener("pointermove", e => {
      const p = pt(e); tipXY = p;
      if (dragging) {
        const dx = p[0] - dragging.p[0], dy = p[1] - dragging.p[1];
        if (Math.abs(dx) + Math.abs(dy) > 3) dragging.moved = true;
        const k = 70 / (R * zoom);
        const tilt = dragging.touch && opt.compact ? 0 : dy;
        rot = [dragging.rot[0] + dx * k * 1.6, Math.max(-75, Math.min(75, dragging.rot[1] - tilt * k * 1.6)), 0];
        idleSince = performance.now();
        return;
      }
      const ll = proj.invert(p), f = ll && d3.geoDistance(ll, [-rot[0], -rot[1]]) < Math.PI / 2 ? geo.find(f => d3.geoContains(f, ll)) : null;
      hover = f ? isoOf(f) || null : null;
      overSign = hitSigns.some(h => Math.hypot(h.x - p[0], h.y - p[1]) < h.r);
      canvas.style.cursor = overSign || hover ? "pointer" : "grab";
    });
    canvas.addEventListener("pointerleave", () => { hover = null; overSign = false; });
    canvas.addEventListener("pointerup", e => {
      const d = dragging; dragging = null; idleSince = performance.now();
      if (!d || d.moved) return;
      const p = pt(e), sign = hitSigns.find(h => Math.hypot(h.x - p[0], h.y - p[1]) < h.r);
      hideHint();
      if (sign) {
        const useCard = typeof opt.card === "function" ? opt.card() : opt.card;
        if (useCard) showCard(sign.i);
        if (opt.onLink && (!useCard || typeof opt.card === "function")) opt.onLink(sign.i);
        return;
      }
      if (hover && opt.onCountry) opt.onCountry(hover);
    });
    canvas.addEventListener("wheel", e => {
      e.preventDefault(); anim = null;
      zoom = Math.max(.8, Math.min(5, zoom * Math.exp(-e.deltaY * .0015)));
    }, {passive: false});
    let pinch = null;
    canvas.addEventListener("touchstart", e => { if (e.touches.length === 2) pinch = {d: Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY), z: zoom}; }, {passive: true});
    canvas.addEventListener("touchmove", e => {
      if (pinch && e.touches.length === 2) {
        const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
        zoom = Math.max(.8, Math.min(5, pinch.z * d / pinch.d)); dragging = null;
      }
    }, {passive: true});
    canvas.addEventListener("touchend", () => { pinch = null; lastTouch = performance.now(); }, {passive: true});

    // the legend: only the kinds on the globe right now; a click shows just that kind
    const box = canvas.parentNode;
    const legend = document.createElement("div"); legend.className = "globe-legend"; box.appendChild(legend);
    function paintLegend() {
      const counts = {};
      for (const k of links) if (WORDS[k.l.type] && !k.l.title?.includes("$")) counts[k.l.type] = (counts[k.l.type] || 0) + 1;
      legend.innerHTML = "";
      legend.hidden = !Object.keys(counts).length;
      for (const [type, n] of Object.entries(counts).sort((a, b) => b[1] - a[1])) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = solo === type ? "solo" : solo ? "off" : "";
        b.title = EXPLAIN[type] + " Bosing — faqat shularni ko'rsatadi.";
        b.innerHTML = `<i style="background:${(opt.colors || {})[type] || "#d4af37"}"></i>${WORDS[type][0]} ${WORDS[type][1]} · ${n}`;
        b.onclick = () => { solo = solo === type ? null : type; paintLegend(); hideHint(); };
        legend.appendChild(b);
      }
    }
    // a hint for first-time visitors, gone after six seconds or the first touch
    const hint = document.createElement("div"); hint.className = "globe-hint";
    hint.textContent = links.length ? "👆 Aylantiring, rasmcha yoki davlatni bosing" : "👆 Globusni barmoq bilan aylantiring";
    box.appendChild(hint);
    function hideHint() { hint.classList.add("gone"); }
    setTimeout(hideHint, 6000);
    canvas.addEventListener("pointerdown", hideHint, {once: true});
    // a card that says in one breath what a sign means
    const card = document.createElement("div"); card.className = "globe-card"; card.hidden = true; box.appendChild(card);
    const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
    function showCard(i) {
      const k = links.find(x => x.i === i); if (!k) return;
      const l = k.l, w = WORDS[l.type] || ["🌍", l.type], nm = c => (opt.names || {})[c] || c;
      focused = i;
      card.innerHTML = `<button type="button" aria-label="Yopish">×</button>
        <span class="kind" style="background:${(opt.colors || {})[l.type] || "#d4af37"}">${w[0]} ${w[1]}</span>
        <div class="ttl">${esc(nm(l.from))} → ${esc(nm(l.to))}</div>
        <div>${esc(l.bubble || l.title || "")}</div>
        <div style="opacity:.8;margin-top:6px">💡 ${esc(EXPLAIN[l.type] || "")}</div>
        ${opt.more && opt.more(i) ? `<div style="margin-top:8px"><a href="${esc(opt.more(i))}"${/^https?:/.test(opt.more(i)) ? ' target="_blank" rel="noopener"' : ""}>Batafsil →</a></div>` : ""}`;
      card.hidden = false;
      card.querySelector("button").onclick = () => { card.hidden = true; focused = -1; idleSince = performance.now(); };
    }
    paintLegend();

    addEventListener("resize", size);
    size();
    requestAnimationFrame(frame);

    return {
      focusLink(i) { const k = links.find(x => x.i === i); focused = i; if (k) turnTo(k.mid, Math.max(zoom, 1.4)); },
      clearFocus() { focused = -1; focusIso = null; idleSince = performance.now(); },
      focusCountry(iso, z = 1.5) { focusIso = iso; const f = byIso[iso]; if (f) turnTo(d3.geoCentroid(f), z); },
      setLayer(v) { layer = v; },
      setLinks(list) { build(list); focused = -1; solo = null; card.hidden = true; paintLegend(); },
      setSigns(v) { signs = v; },
    };
  };
})();

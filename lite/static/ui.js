/* Small touches shared by every page: the night/parchment switch, the hourglass while AI works,
   the phone menu, drop-down menus and the PDF button. */
(function () {
  "use strict";
  const root = document.documentElement, btn = document.getElementById("themebtn");
  const paint = () => { if (btn) btn.textContent = root.dataset.theme === "light" ? "☀" : "☾"; };
  paint();
  if (btn) btn.onclick = () => {
    const light = root.dataset.theme !== "light";
    if (light) root.dataset.theme = "light"; else delete root.dataset.theme;
    try { localStorage.setItem("ws-theme", light ? "light" : "dark"); } catch (e) { /* ignore */ }
    paint();
    document.dispatchEvent(new Event("ws-theme"));
  };

  // AI forms: an hourglass with a running count, so a 40-second wait does not look like a hang.
  const AI = ["/brief", "/map", "/pair", "/debt", "/framing"];
  document.querySelectorAll("form[method=post]").forEach(f => {
    const path = new URL(f.action, location.href).pathname;
    if (!AI.includes(path) && !/^\/predictions\/\d+\/check$/.test(path)) return;
    f.addEventListener("submit", () => {
      const box = document.getElementById("aiwait"), secs = document.getElementById("secs");
      if (!box) return;
      box.hidden = false;
      const t0 = Date.now();
      setInterval(() => { secs.textContent = Math.round((Date.now() - t0) / 1000); }, 1000);
    });
  });
  // the telegraph key taps while the news is being fetched
  document.querySelectorAll("form.refresh").forEach(f => f.addEventListener("submit", e => {
    if (e.submitter && e.submitter.getAttribute("formaction")) return;
    f.querySelector(".telegraph")?.classList.add("tapping");
  }));

  // phone: the full menu opens over the page from the bottom bar
  const menuBtn = document.getElementById("menubtn"), nav = document.getElementById("mainnav");
  if (menuBtn && nav) menuBtn.onclick = () => {
    const open = nav.classList.toggle("open");
    menuBtn.setAttribute("aria-expanded", String(open));
    if (open) nav.querySelector("details.more")?.setAttribute("open", "");
  };
  document.addEventListener("keydown", e => { if (e.key === "Escape") nav?.classList.remove("open"); });

  // drop-down menus close when you click elsewhere
  document.addEventListener("click", e => {
    document.querySelectorAll("details.more[open]").forEach(d => { if (!d.contains(e.target) && !nav?.classList.contains("open")) d.removeAttribute("open"); });
  });

  // Hard words explained where they stand: the first time each glossary word appears on the page
  // it gets a dotted underline; hovering or tapping it shows a plain explanation.
  try {
    const G = JSON.parse(document.getElementById("glossary")?.textContent || "{}");
    const words = Object.keys(G).sort((a, b) => b.length - a.length);
    const esc = w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp("(^|[^\\p{L}])(" + words.map(esc).join("|") + ")", "iu");
    const seen = new Set(), skip = "A,BUTTON,INPUT,SELECT,OPTION,TEXTAREA,SCRIPT,STYLE,CODE,SUMMARY,LABEL,H1,TH,svg,CANVAS".split(",");
    const main = document.querySelector("main");
    const walker = main && document.createTreeWalker(main, NodeFilter.SHOW_TEXT, {acceptNode: n => {
      for (let p = n.parentElement; p && p !== main; p = p.parentElement) {
        if (skip.includes(p.tagName) || p.classList.contains("term") || p.closest?.("#intro")) return NodeFilter.FILTER_REJECT;
      }
      return re.test(n.data) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }});
    const nodes = [];
    while (walker && walker.nextNode() && nodes.length < 400) nodes.push(walker.currentNode);
    let made = 0;
    for (const n of nodes) {
      if (made >= 30) break;
      const m = n.data.match(re);
      if (!m) continue;
      const word = m[2], k = words.find(w => w.toLowerCase() === word.toLowerCase());
      if (!k || seen.has(k)) continue;
      seen.add(k); made++;
      const at = m.index + m[1].length, rest = n.splitText(at);
      rest.splitText(word.length);
      const span = document.createElement("span");
      span.className = "term"; span.tabIndex = 0; span.dataset.tip = G[k]; span.textContent = rest.data;
      rest.replaceWith(span);
    }
  } catch (e) { /* the page works without the glossary */ }

  // "PDF": the browser's own print dialog, where "Save as PDF" gives a clean file; the title
  // becomes the suggested file name.
  document.querySelectorAll("[data-pdf]").forEach(b => b.addEventListener("click", () => {
    const old = document.title;
    document.title = b.dataset.pdf || old;
    const main = document.querySelector("main");
    if (main) main.dataset.print = (b.dataset.pdf || old).replace(/^WorldScope - /, "") + " · " + new Date().toISOString().slice(0, 10);
    document.querySelectorAll("details").forEach(d => d.setAttribute("data-was", d.open ? "1" : "0"));
    document.querySelectorAll("main details").forEach(d => { d.open = true; });
    window.print();
    setTimeout(() => {
      document.title = old;
      document.querySelectorAll("details[data-was]").forEach(d => { d.open = d.dataset.was === "1"; d.removeAttribute("data-was"); });
    }, 500);
  }));

  // Share a story: the phone's own share sheet, else Telegram's share page
  document.addEventListener("click", e => {
    const b = e.target.closest?.("button.share"); if (!b) return;
    const title = b.dataset.title, url = b.dataset.url, text = b.dataset.text + " — WorldScope orqali";
    if (navigator.share) { navigator.share({title, text, url}).catch(() => {}); return; }
    window.open("https://t.me/share/url?url=" + encodeURIComponent(url) + "&text=" + encodeURIComponent(text), "_blank", "noopener");
  });

  // The app on the home screen and the browser's notifications
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  const b64 = s => { const p = "=".repeat((4 - s.length % 4) % 4), raw = atob((s + p).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0))); };
  const pushOk = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  async function currentSub() { const reg = await navigator.serviceWorker.ready; return reg.pushManager.getSubscription(); }
  const post = (url, body) => fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  async function paintPush() {
    const sub = pushOk ? await currentSub().catch(() => null) : null;
    document.querySelectorAll("[data-push]").forEach(b => {
      if (!pushOk) { b.disabled = true; b.textContent = "🔕 Bu brauzer bildirishnomani qo'llamaydi"; return; }
      b.dataset.on = sub ? "1" : "";
      b.textContent = sub ? "🔕 Bildirishnomani o'chirish" : "🔔 Ertalabki bildirishnomani yoqish";
    });
  }
  document.addEventListener("click", async e => {
    const b = e.target.closest?.("[data-push]"); if (!b || !pushOk) return;
    b.disabled = true;
    try {
      const old = await currentSub();
      if (old) { await post("/push/unsubscribe", {endpoint: old.endpoint}); await old.unsubscribe(); }
      else {
        if (await Notification.requestPermission() !== "granted") { alert("Brauzer sozlamalarida bu sayt uchun bildirishnomaga ruxsat bering."); return; }
        const {key} = await (await fetch("/push/key")).json();
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64(key)});
        await post("/push/subscribe", sub.toJSON());
      }
    } catch (err) { alert("Yoqib bo'lmadi: " + err.message); }
    finally { b.disabled = false; paintPush(); }
  });
  if (pushOk) {
    paintPush();
    // once a day the subscription is sent again, so a change of interests reaches the morning message
    currentSub().then(sub => {
      const today = new Date().toISOString().slice(0, 10);
      let last = ""; try { last = localStorage.getItem("ws-push-sync") || ""; } catch (err) {}
      if (sub && last !== today) { post("/push/subscribe", sub.toJSON()); try { localStorage.setItem("ws-push-sync", today); } catch (err) {} }
    }).catch(() => {});
  }
})();

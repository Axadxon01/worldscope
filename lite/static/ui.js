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
})();

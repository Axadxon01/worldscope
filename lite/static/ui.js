/* Small touches shared by every page: the night/parchment switch, the hourglass while AI works. */
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
    if (!AI.includes(path)) return;
    f.addEventListener("submit", () => {
      const box = document.getElementById("aiwait"), secs = document.getElementById("secs");
      if (!box) return;
      box.hidden = false;
      const t0 = Date.now();
      setInterval(() => { secs.textContent = Math.round((Date.now() - t0) / 1000); }, 1000);
    });
  });
  // the telegraph key taps while the news is being fetched
  document.querySelectorAll("form.refresh").forEach(f => f.addEventListener("submit", () => f.querySelector(".telegraph")?.classList.add("tapping")));
})();

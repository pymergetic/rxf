/* ui.js — the one live slot of the shell (docs/21): the attached session, or exactly `no image`.
 *
 * The slot in server/static/ui/shell.html reads `no image` until something better is known. A
 * page served with a live session may have its slot filled at serve time (the tutor does); this
 * script fills it from the client only while the slot still holds that default, so it never
 * overwrites a server-filled slot. With a running image the wording mirrors the tutor's live
 * slot minus the link: no API on this app exposes `session.spec` yet, and the slot may not
 * invent one. Semantic colors live in ui.css (.ok = teal, .bad = red).
 */
"use strict";
(() => {
  const slot = document.querySelector(".shell .live");
  if (!slot || slot.textContent.trim() !== "no image") return;
  const get = async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: ${response.status}`);
    return response.json();
  };
  (async () => {
    try {
      const seam = await get("/api/editor/numbers");
      if (!seam || seam.ok === false) return; // no running image: the default is the truth
      const info = await get("/api/info");
      const mark = info.issue_count ? "bad" : "ok";
      slot.innerHTML =
        `live: ${info.objects} cells &middot; checksum <span class="${mark}">&bull; ${mark}</span>`;
    } catch {
      // no seam, no truth to add: the default `no image` stays
    }
  })();
})();

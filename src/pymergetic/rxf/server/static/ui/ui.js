/* ui.js — the one live feed of the shell (docs/21): the live session spec of the app
 * (`GET /api/live`), rendered into the live slot of every surface that carries the shell. One
 * feed component, one slot: the editor and the inspector are two consumers of this very script
 * through the shared shell (server/static/ui/shell.html), so the wording is the same everywhere.
 *
 * The slot reads `no image` until something better is known. A page served with a live session
 * may have its slot filled at serve time (the tutor does); this script takes only the slots that
 * still hold that default, so it never overwrites a server-filled slot. `no image` is the answer
 * only when the app has no session at all — an attached session that does not answer says so,
 * with the channel's own refusal. Everything shown is what the running image itself answered:
 * its image id (digest), the version its channel reports, the handle and state of its live leaf,
 * and its live counters. Semantic colors live in ui.css (.ok = teal, .bad = red, .badge = the
 * state pill).
 */
"use strict";
(() => {
  const slots = [...document.querySelectorAll(".shell .live")].filter(
    (slot) => slot.textContent.trim() === "no image",
  );
  if (!slots.length) return;
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const get = async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: ${response.status}`);
    return response.json();
  };
  const stateWords = (state) =>
    `${esc(state.provenance)}/${esc(state.disposition)}/${esc(state.cleanliness)}/${esc(state.mobility)}`;
  const leafState = (leaf) =>
    leaf ? `${esc(leaf.path.split(".").pop())} ${stateWords(leaf.state)}` : "";

  // the state badge and the facts under it: the answers of the running image, nothing else
  const render = (live) => {
    let html, title;
    if (!live.attached) {
      html = "no image";
      title = "no live session: the app runs no image behind it (rxf serve --link=...)";
    } else if (live.image) {
      const c = live.counters;
      html =
        `live: <code>${esc(live.spec)}</code> · image <code>${esc(live.image.digest.slice(0, 8))}</code>` +
        ` · interpreter v${esc(live.interpreter.version)}` +
        (live.leaf ? ` · leaf ${leafState(live.leaf)}` : "") +
        ` · commits ${esc(c.commits)} · rejects ${esc(c.rejects)}` +
        ` <span class="badge ok">live</span>`;
      title =
        `${live.spec}: image ${live.image.digest} (${live.image.handles} handles, ` +
        `${live.image.body_size} bytes); interpreter v${live.interpreter.version}; ` +
        (live.leaf ? `leaf ${live.leaf.path} @${live.leaf.handle} — ${stateWords(live.leaf.state)}; ` : "no live leaf; ") +
        `commits ${c.commits}, rejects ${c.rejects}, committed ${c.committed} of ${c.arena_capacity}`;
    } else {
      html =
        `live: <code>${esc(live.spec)}</code> <span class="badge bad">not answering</span>` +
        (live.refusal ? ` <span class="bad">${esc(live.refusal)}</span>` : "");
      title = live.refusal || "the attached session does not answer";
    }
    for (const slot of slots) {
      slot.innerHTML = html;
      slot.title = title;
    }
  };

  let busy = false;
  const tick = async () => {
    // The editor holds the channel while a seam call is in flight (body[data-channel-busy],
    // the contract between this feed and the editor's script): a feed poll must never land
    // between the front's delta operations — the image would refuse the whole delta.
    if (busy || document.body.dataset.channelBusy) return;
    busy = true;
    try {
      render(await get("/api/live"));
    } catch {
      // the feed itself is down: the slot keeps the last truth it rendered
    }
    busy = false;
  };
  tick();
  setInterval(tick, 4000); // the feed polls: a slot that shows a session shows it as it is
})();

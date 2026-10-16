/* The HUD skeleton of the world page: the top resource bar, the bottom status bar, and the
 * minimap. Visual only in this stage — where live data does not exist yet the slot shows
 * "—" (a seam, not a lie). The scene fills what the image already knows (runs, counts) and the
 * later stages fill the rest through the same slots.
 *
 * The minimap is a real top-down projection of the same document the scene draws: district
 * plates on their tile grids, buildings as dots by family, void tiles in red, the camera's
 * viewport as a rectangle, and click-to-jump (the click hands the world coordinate back to
 * the scene's camera rig). The map is scene-relative: the scene draws world +X toward screen
 * LEFT and +Z toward screen DOWN (verified against the camera), so the map's x axis is mirrored
 * to match — the minimap must lay out exactly like the world looks from above.
 *
 * window.RXFWorldHud = { metrics, counts, status, sheet, lod, minimap, error }
 */
(function (global) {
  "use strict";

  function slot(selector, text) {
    var node = global.document.querySelector(selector);
    if (node) node.textContent = text;
  }

  function value(text) {
    return text === null || text === undefined ? "—" : String(text);
  }

  function each(name, fill) {
    for (var node of global.document.querySelectorAll("[data-" + name + "]")) {
      fill(node, node.getAttribute("data-" + name));
    }
  }

  /* ---- the minimap: a top-down projection of the same document the scene draws ------------ */
  var map = {
    canvas: null,
    base: null,
    scale: 1,
    minX: 0,
    minZ: 0,
    onJump: null,
    bounds: null
  };

  function mapSize() {
    var canvas = map.canvas;
    return {
      width: (canvas.width = canvas.clientWidth * 2),
      height: (canvas.height = canvas.clientHeight * 2)
    };
  }

  function toMap(x, z) {
    /* scene-relative orientation: x mirrored (world +X draws screen left), z straight down
     * (world +Z draws screen down) — the minimap reads exactly like the top-down scene */
    return {
      x: map.canvas.width - (x - map.minX) * map.scale,
      y: (z - map.minZ) * map.scale
    };
  }

  function invMap(px, py) {
    /* the exact inverse of toMap: a minimap click hands a world coordinate to the rig */
    return {
      x: map.minX + (map.canvas.width - px) / map.scale,
      z: map.minZ + py / map.scale
    };
  }

  function renderBase(doc) {
    var size = mapSize();
    var base = global.document.createElement("canvas");
    base.width = size.width;
    base.height = size.height;
    var context = base.getContext("2d");
    context.fillStyle = "#0d1218";
    context.fillRect(0, 0, size.width, size.height);

    /* district plates on their tile rasters (the same plots the scene stands on) */
    var bands = { header: "#2b3846", index: "#2e3b48", cells: "#26323e" };
    for (var region of doc.regions) {
      var at = toMap(region.plot.x, region.plot.z);
      context.fillStyle = bands[region.band] || bands.cells;
      context.globalAlpha = region.empty ? 0.55 : 1;
      context.fillRect(at.x, at.y, region.plot.w * map.scale, region.plot.d * map.scale);
      context.globalAlpha = 1;
      context.strokeStyle = "#3a4a58";
      context.lineWidth = 1;
      context.strokeRect(at.x, at.y, region.plot.w * map.scale, region.plot.d * map.scale);
    }

    /* buildings as dots by family (run towers a touch larger and lighter) */
    var families = global.RXFWorld ? global.RXFWorld.FAMILY_COLORS : {};
    for (var cell of doc.cells) {
      var dot = toMap(cell.plot.x, cell.plot.z);
      context.fillStyle = families[cell.family] || "#6b7783";
      var size2 = cell.family === "tower" ? 5 : 3;
      context.fillRect(dot.x - size2 / 2, dot.y - size2 / 2, size2, size2);
    }

    /* void tiles: the poison of compaction, in red */
    for (var tile of doc.voids) {
      var pit = toMap(tile.plot.x, tile.plot.z);
      context.fillStyle = "#a3372c";
      context.fillRect(pit.x - 3, pit.y - 3, 6, 6);
    }
    map.base = base;
  }

  function renderMap(view, selected) {
    if (!map.canvas || !map.base) return;
    var context = map.canvas.getContext("2d");
    context.clearRect(0, 0, map.canvas.width, map.canvas.height);
    context.drawImage(map.base, 0, 0);
    if (view) {
      context.strokeStyle = "#37e0c4";
      context.lineWidth = 2;
      if (view.corners && view.corners.length === 4) {
        /* the framed ground quad, yaw and all (the camera knows its own corners) */
        context.beginPath();
        for (var i = 0; i < view.corners.length; i++) {
          var corner = toMap(view.corners[i].x, view.corners[i].z);
          if (i === 0) context.moveTo(corner.x, corner.y);
          else context.lineTo(corner.x, corner.y);
        }
        context.closePath();
        context.stroke();
      } else {
        var a = toMap(view.x - view.w / 2, view.z - view.h / 2);
        var b = toMap(view.x + view.w / 2, view.z + view.h / 2);
        context.strokeRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
      }
    }
    if (selected) {
      var at = toMap(selected.plot.x, selected.plot.z);
      context.strokeStyle = "#ffd27a";
      context.lineWidth = 2;
      context.strokeRect(at.x - 7, at.y - 7, 14, 14);
    }
    var needle = global.document.querySelector("[data-compass-needle]");
    if (needle && view) needle.style.transform = "rotate(" + -view.yaw + "deg)";
  }

  /* ---- the datasheet side pane: the clicked cell, spelled out ----------------------------- */
  /* the semantic hue of a value: identity in the accent, kind words in steel, byte bands and
   * numbers in mono with their own hue, the state words colored by meaning (rxf-world.css) */
  var ROW_STYLE = {
    id: "v-accent",
    name: "v-accent",
    handle: "v-accent",
    refs: "v-accent",
    kind: "v-kind",
    family: "v-kind",
    region: "v-kind",
    band: "v-data",
    bytes: "v-data",
    payload: "v-data",
    generation: "v-data",
    forward: "v-data",
    claim: "v-data",
    checksum: "v-data",
    anatomy: "v-data",
    floors: "v-data",
    provenance: "v-prov",
    mobility: "v-move"
  };

  function valueStyle(name, text) {
    /* the four state words read by meaning: retain/clean in the good hues, refused/poisoned
     * (and their family: tombstone, dirty) in red, the in-between words in between */
    if (name === "disposition") {
      return /retain|keep/i.test(text) ? "v-good" : /tombstone|refus/i.test(text) ? "v-bad" : "v-warn";
    }
    if (name === "cleanliness") {
      return /dirty|poison/i.test(text) ? "v-bad" : /clean/i.test(text) ? "v-clean" : "v-warn";
    }
    return ROW_STYLE[name] || "";
  }

  function stateWordSpans(words) {
    /* the bottom bar's state cell: one colored span per state word (the separator stays plain) */
    var parts = [];
    for (var i = 0; i < words.length; i++) {
      if (i) parts.push({ text: " · ", style: "" });
      parts.push(words[i]);
    }
    return parts;
  }

  function fillSpans(node, parts) {
    node.textContent = "";
    for (var part of parts) {
      var span = global.document.createElement("span");
      if (part.style) span.className = part.style;
      span.textContent = part.text;
      node.appendChild(span);
    }
  }

  function datasheetRow(body, name, text, seam, style) {
    var line = global.document.createElement("div");
    line.className = "datasheet-row" + (seam ? " seam" : "");
    var key = global.document.createElement("span");
    key.className = "datasheet-key";
    key.textContent = name;
    var val = global.document.createElement("span");
    val.className = "datasheet-value " + (style || valueStyle(name, text));
    val.textContent = text;
    line.appendChild(key);
    line.appendChild(val);
    body.appendChild(line);
  }

  function datasheetGroup(body, name, note) {
    /* a provenance group: "image" holds what the cell says, "world" what the view decided.
     * Rows collect inside the group (they are appended to it, not to the body), so the zebra
     * stripes run per group and the heads stand outside them. */
    var group = global.document.createElement("div");
    group.className = "datasheet-group";
    var head = global.document.createElement("div");
    head.className = "datasheet-group-head";
    var label = global.document.createElement("span");
    label.className = "datasheet-group-name";
    label.textContent = name;
    var hint = global.document.createElement("span");
    hint.className = "datasheet-group-note";
    hint.textContent = note;
    head.appendChild(label);
    head.appendChild(hint);
    group.appendChild(head);
    body.appendChild(group);
    return group;
  }

  function hex(byteOffset) {
    return "0x" + Number(byteOffset).toString(16).toUpperCase();
  }

  var Hud = {
    metrics: function (metrics) {
      each("metric", function (node, name) {
        node.textContent = value(metrics[name]);
        node.classList.toggle("seam", metrics[name] === null || metrics[name] === undefined);
      });
    },
    counts: function (counts) {
      each("count", function (node, name) {
        node.textContent = value(counts[name]);
      });
    },
    status: function (payload) {
      if (!payload) {
        each("cell-field", function (node) {
          node.textContent = "—";
        });
        slot("[data-selection]", "no selection");
        return;
      }
      var cell = payload.cell;
      var fields = {
        name: cell.path || cell.name || cell.id,
        id: cell.id,
        kind: cell.kind,
        family: cell.family,
        handle: cell.handle === null ? "—" : String(cell.handle),
        generation: String(cell.generation),
        offset: "0x" + cell.offset.toString(16) + " +" + cell.size,
        payload: String(cell.payload_size),
        forward: cell.forward ? "C_FORWARD " + cell.forward : "0",
        state: cell.state
          ? cell.state.provenance +
            " · " +
            cell.state.disposition +
            " · " +
            cell.state.cleanliness +
            " · " +
            cell.state.mobility
          : "—",
        claim: value(cell.claim),
        checksum: value(cell.checksum),
        edges: String(payload.edges.length)
      };
      var stateWords = cell.state
        ? [
            { text: cell.state.provenance, style: "v-prov" },
            { text: cell.state.disposition, style: valueStyle("disposition", cell.state.disposition) },
            { text: cell.state.cleanliness, style: valueStyle("cleanliness", cell.state.cleanliness) },
            { text: cell.state.mobility, style: "v-move" }
          ]
        : null;
      each("cell-field", function (node, name) {
        if (name === "state" && stateWords) {
          /* the four state words, each in its own meaningful hue */
          fillSpans(node, stateWordSpans(stateWords));
          return;
        }
        node.textContent = value(fields[name]);
      });
      slot("[data-selection]", fields.name);
    },
    sheet: function (payload) {
      /* the datasheet side pane: every field the image knows about the selected cell, plus
       * its cable list and its floor-stack anatomy. Seams show "—" and say so in grey. The rows
       * are split by who says them: "image" rows are read from the cell, "world" rows are this
       * view's own reading — our classifications never pass as the image's facts. */
      var pane = global.document.getElementById("rxf-world-datasheet");
      if (!pane) return;
      if (!payload) {
        pane.classList.remove("open");
        return;
      }
      var body = global.document.getElementById("datasheet-body");
      var cell = payload.cell;
      var region = payload.region;
      body.textContent = "";
      slot("[data-datasheet-title]", cell.path || cell.name || cell.id);
      /* the image group: what the cell itself says — state bits, spans, words, cables, and the
       * byte anatomy (the floor VALUES are real addresses; only the word "floors" is ours) */
      var image = datasheetGroup(body, "image", "read from the cell");
      datasheetRow(image, "id", value(cell.id));
      datasheetRow(image, "name", value(cell.path || cell.name));
      datasheetRow(image, "handle", value(cell.handle));
      datasheetRow(image, "kind", value(cell.kind));
      datasheetRow(image, "bytes", hex(cell.offset) + "–" + hex(cell.offset + cell.size) + " (" + cell.size + " B)");
      datasheetRow(image, "payload", cell.payload_size + " B");
      datasheetRow(image, "generation", value(cell.generation));
      if (cell.state) {
        datasheetRow(image, "provenance", value(cell.state.provenance));
        datasheetRow(image, "disposition", value(cell.state.disposition));
        datasheetRow(image, "cleanliness", value(cell.state.cleanliness));
        datasheetRow(image, "mobility", value(cell.state.mobility));
      } else {
        datasheetRow(image, "state", "—", true);
      }
      datasheetRow(image, "forward", cell.forward ? "C_FORWARD " + cell.forward : "0");
      /* poisoned is the one row where "yes" is the bad hue and "—" is a seam */
      datasheetRow(image, "poisoned", cell.poisoned ? "yes" : "—", !cell.poisoned, cell.poisoned ? "v-bad" : "");
      datasheetRow(image, "claim", value(cell.claim), cell.claim === null || cell.claim === undefined);
      datasheetRow(
        image,
        "checksum",
        value(cell.checksum),
        cell.checksum === null || cell.checksum === undefined
      );
      datasheetRow(image, "refs", value(cell.ref_count) + " row(s) · " + payload.edges.length + " cable(s)");
      for (var edge of payload.edges.slice(0, 8)) {
        var way = edge.from === cell.id ? "→ " + edge.to : "← " + edge.from;
        /* cables in the accent; a ghost cable is the bad hue (it leads nowhere) */
        datasheetRow(image, "cable", way + (edge.stale ? " (ghost)" : ""), edge.stale, edge.stale ? "v-bad" : "v-accent");
      }
      if (payload.edges.length > 8) {
        datasheetRow(image, "cable", "+" + (payload.edges.length - 8) + " more", true);
      }
      if (cell.anatomy && cell.anatomy.length) {
        datasheetRow(image, "anatomy", cell.anatomy.length + " floors");
        for (var band of cell.anatomy.slice(0, 12)) {
          /* the anatomy floors are byte addresses: the data hue, in mono */
          datasheetRow(image, band.role, hex(band.offset) + " +" + band.size + " B", false, "v-data");
        }
        if (cell.anatomy.length > 12) {
          datasheetRow(image, "floors", "+" + (cell.anatomy.length - 12) + " more", true);
        }
      }
      /* the world group: what this view decided — the paint family, the district grid, the
       * band assignment. The image never said "tower"; we did. */
      var world = datasheetGroup(body, "world", "the view's own reading");
      datasheetRow(world, "family", value(cell.family));
      datasheetRow(
        world,
        "region",
        region ? region.name + " · " + region.id + " · " + region.band : value(cell.region)
      );
      if (region) {
        datasheetRow(world, "band", hex(region.start) + "–" + hex(region.end));
      }
      pane.classList.add("open");
    },
    lod: function (level) {
      slot("[data-lod]", level.name + " · " + level.shows);
    },
    minimap: function (doc, bounds, onJump) {
      var canvas = global.document.getElementById("rxf-world-minimap");
      if (!canvas) return;
      map.canvas = canvas;
      map.onJump = onJump;
      map.bounds = bounds;

      /* the world rectangle the map frames (districts and void tiles, with a margin) */
      var spanX = 1;
      var spanZ = 1;
      for (var region of doc.regions) {
        spanX = Math.max(spanX, region.plot.x + region.plot.w);
        spanZ = Math.max(spanZ, region.plot.z + region.plot.d);
      }
      var size = mapSize();
      map.scale = Math.min(size.width / (spanX * 1.1), size.height / (spanZ * 1.1));
      map.minX = -((size.width / map.scale - spanX) / 2);
      map.minZ = -((size.height / map.scale - spanZ) / 2);
      renderBase(doc);
      renderMap(null, null);

      canvas.addEventListener("pointerdown", function (event) {
        if (!map.onJump) return;
        var rect = canvas.getBoundingClientRect();
        var at = invMap(
          ((event.clientX - rect.left) * canvas.width) / rect.width,
          ((event.clientY - rect.top) * canvas.height) / rect.height
        );
        map.onJump(at.x, at.z);
      });
    },
    error: function (error) {
      slot("[data-selection]", "scene failed: " + error.message);
    }
  };

  Hud.minimap.view = renderMap;

  global.RXFWorldHud = Hud;
})(typeof window !== "undefined" ? window : globalThis);

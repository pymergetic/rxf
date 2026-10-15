/* The HUD skeleton of the world page: the top resource bar, the bottom status bar, and the
 * minimap. Visual only in this stage — where live data does not exist yet the slot shows
 * "—" (a seam, not a lie). The scene fills what the image already knows (runs, counts) and the
 * later stages fill the rest through the same slots.
 *
 * The minimap is a real top-down projection of the scene document: district plates on their tile
 * grids, buildings as dots by family, void tiles in red, the camera's viewport as a rectangle,
 * and click-to-jump (the click hands the world coordinate back to the scene's camera rig).
 *
 * window.RXFWorldHud = { metrics, counts, status, lod, minimap, error }
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
    originX: 0,
    originZ: 0,
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
    return {
      x: (x - map.originX) * map.scale,
      y: (z - map.originZ) * map.scale
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
      context.strokeRect(
        (view.x - view.w / 2 - map.originX) * map.scale,
        (view.z - view.h / 2 - map.originZ) * map.scale,
        view.w * map.scale,
        view.h * map.scale
      );
    }
    if (selected) {
      var at = toMap(selected.plot.x, selected.plot.z);
      context.strokeStyle = "#ffd27a";
      context.lineWidth = 2;
      context.strokeRect(at.x - 7, at.y - 7, 14, 14);
    }
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
      each("cell-field", function (node, name) {
        node.textContent = value(fields[name]);
      });
      slot("[data-selection]", fields.name);
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
      map.originX = -((size.width / map.scale - spanX) / 2);
      map.originZ = -((size.height / map.scale - spanZ) / 2);
      renderBase(doc);
      renderMap(null, null);

      canvas.addEventListener("pointerdown", function (event) {
        if (!map.onJump) return;
        var rect = canvas.getBoundingClientRect();
        var x = ((event.clientX - rect.left) * (canvas.width / rect.width)) / map.scale + map.originX;
        var z = ((event.clientY - rect.top) * (canvas.height / rect.height)) / map.scale + map.originZ;
        map.onJump(x, z);
      });
    },
    error: function (error) {
      slot("[data-selection]", "scene failed: " + error.message);
    }
  };

  Hud.minimap.view = renderMap;

  global.RXFWorldHud = Hud;
})(typeof window !== "undefined" ? window : globalThis);

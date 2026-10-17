/* The 3D world of the RXF runtime: one Babylon.js scene, generated purely from the scene JSON
 * (the schema is documented in src/pymergetic/rxftk/world/export.py).
 *
 * The world IS the arena — a projection, never a second truth. This file renders what the
 * exporter wrote and invents nothing: districts (memory regions) are tiled ground plots, cells
 * are block-stack buildings whose floors are the cell anatomy (the 64-byte header word for word,
 * the payload regions of S1, the reference rows), dead/poisoned memory is rubble and void tiles,
 * handle references are ground traces routed on the tile grid (stale handles = red ghost traces
 * to graves), and the ready-ring is a ring road with tram units on it. No Math.random: the scene
 * is a deterministic function of the JSON and the animation clock.
 *
 * One scene, one camera rig: orthographic top-down (classic RTS) <-> perspective fly view with a
 * smooth, continuous zoom through the LOD levels (L0 strategic, L1 operational, L2 tactical,
 * L3 byte view is a later stage). Movement is camera-relative (W = view direction, A = camera
 * left) in every mode. Vendored Babylon.js (WebGL2 + WebGPU behind one API) — no CDN.
 *
 * Public face (window.RXFWorld):
 *   RXFWorld.CAMERA_POSES          the fixed named poses, for deterministic screenshots
 *   RXFWorld.LOD_LEVELS            the zoom thresholds and what each level shows
 *   RXFWorld.FAMILY_COLORS         building family colors (also used by the HUD)
 *   RXFWorld.ROLE_COLORS           block-view floor colors (cell anatomy roles)
 *   RXFWorld.create(canvas, doc, options) -> Promise<api>
 *   api.onSelect(fn)               fn({cell, edges, region}) on click-to-select
 *   api.highlight(cellId)          select + highlight a building from code
 *   api.setPose(name, instant)     jump or fly to a named pose
 *   api.setMotion(on)              freeze/unfreeze the tram units (off = screenshot-deterministic)
 *   api.seams                      the stubs of the later stages (see below)
 *   api.dispose()
 *
 * Later-stage seams (stubs, deliberately not implemented here):
 *   api.seams.flow         live value flow on cables   (animation of edge values)
 *   api.seams.animate      run animation               (run state on the tram units)
 *   api.seams.edit         construction/edit mode      (spawning/moving buildings)
 *   api.seams.missions     missions                    (guided scenarios over the world)
 */
(function (global) {
  "use strict";

  var SCHEMA = "rxf-world-2";
  var FOV = 0.9;
  var TRAM_LAP = 90; /* seconds for one lap of the ring road */
  var RING_ROAD_WIDTH = 5.6; /* matches export.py RING_WIDTH: the detours keep this room */

  /* ---- fixed camera poses: deterministic screenshots ---------------------------------------- */
  /* pitch: degrees above the ground plane (90 = straight down, the RTS look).
   * target: "world-center" | "first-tower" | "selection" (falls back to world-center).
   * framing: "world" fits the whole arena in the frame (deterministic per document), otherwise
   * the pose flies at its fixed altitude. */
  var CAMERA_POSES = [
    { name: "overview", mode: "perspective", pitch: 55, yaw: 40, framing: "world", target: "world-center" },
    { name: "strategic", mode: "ortho", pitch: 90, yaw: 0, framing: "world", target: "world-center" },
    { name: "operational", mode: "ortho", pitch: 90, yaw: 0, altitude: 120, target: "world-center" },
    { name: "tactical", mode: "ortho", pitch: 82, yaw: 25, altitude: 30, target: "first-tower" },
    { name: "fly-ne", mode: "perspective", pitch: 52, yaw: 45, framing: "world", target: "world-center" },
    { name: "inspect", mode: "perspective", pitch: 68, yaw: 20, altitude: 26, target: "selection" }
  ];

  /* ---- LOD: continuous zoom through the levels --------------------------------------------- */
  var LOD_LEVELS = [
    { name: "L0 strategic", above: 200, shows: "districts + aggregate massing" },
    { name: "L1 operational", above: 55, shows: "buildings + name signs + ground traces" },
    { name: "L2 tactical", above: 12, shows: "cell anatomy (block view, checksum displays)" },
    { name: "L3 byte view", above: -1, shows: "later stage (editor)" }
  ];

  /* ---- the look: one palette for buildings and block-view floors ---------------------------- */
  var FAMILY_COLORS = {
    tower: "#6d9fd6",
    monument: "#c2a163",
    block: "#6b7783",
    utility: "#4c5a66",
    rubble: "#5c2f2c" /* dead cells read as dead: red-brown, not neutral */
  };
  var ROLE_COLORS = {
    type: "#55677a",
    parent: "#55677a",
    payload_size: "#5e7180",
    generation: "#d9a13a",
    name: "#55677a",
    flags: "#5e7180",
    ref_count: "#5e7180",
    forward: "#55677a",
    index: "#5e7180",
    reserved: "#46545f",
    payload: "#6d7c8a",
    concurrent: "#3d86ad",
    handoff: "#4f9579",
    program: "#6f80d4",
    checksum: "#2fd9c0",
    poison: "#7d2836",
    refrows: "#9170a8",
    pad: "#2e3740"
  };
  var FORWARD_HOT = "#e0663c";
  var CABLE_FRESH = "#37e0c4";
  var CABLE_STALE = "#ff5040";

  /* ---- the depth dimension: cables fly on module-depth sheets ----------------------------
   * One property drives the height of every trace: the module depth of its deepest endpoint
   * (root 0, a 1, a.b 2, ...). The flat wiring becomes a stack of sheets — intra-level
   * wiring shares a sheet, cross-level wiring climbs between them — which is what untangles
   * a dense reference mesh into readable layers. */
  var FLIGHT_BASE = 3.6; /* the ground sheet: cables between shallow modules */
  var FLIGHT_STEP = 2.6; /* one module level of altitude per level of depth */
  var FLIGHT_MAX = 9; /* deeper endpoints clamp on the top sheet */
  var PAD_LIFT = 0.72; /* where a trace leaves its pad: pad top + half the trace thickness */
  var RAMP = 4.0; /* the climb happens over the first and last stretch of the run */
  /* the ready-ring is the map's landmark: its paint must read at a glance against the ground
   * AND against the muted local streets (those stay dark). All of it is flat paint on the
   * ground — the round-5 law stands: no curb lips, no side walls, no street shadows. */
  var RING_ASPHALT = "#2b4256"; /* the boulevard's asphalt: a clear step up from street #141c25 */
  var RING_MARK = "#bfe6f2"; /* the bright edge marking painted along both boundaries */
  var RING_DASH = "#8fc4dc"; /* the centerline dashes, a legible step up from street #33505c */
  /* district floor tints: two variants per band family, blended per district — neighboring
   * districts must read as different plots, never as one slab */
  var TILE_TINT = {
    header: ["#3a4472", "#474c82"],
    index: ["#33506b", "#3d5f7a"],
    cells: ["#2c5058", "#37625e"]
  };

  function hash(value) {
    /* a deterministic 0..1 from an integer (no randomness anywhere in this file) */
    var mixed = (value * 2654435761) % 4294967296;
    return (mixed >>> 0) / 4294967296;
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function color(hex, variation) {
    return global.BABYLON.Color3.FromHexString(hex).scale(variation);
  }

  function tintOf(band, seed) {
    /* one district's tint inside its band family: deterministic, distinct from its neighbors */
    var pair = TILE_TINT[band] || TILE_TINT.cells;
    var t = hash(Math.round(seed * 131) + 7);
    var lift = 0.82 + 0.46 * hash(Math.round(seed * 57) + 3); /* per-district lightness */
    var a = global.BABYLON.Color3.FromHexString(pair[0]);
    var b = global.BABYLON.Color3.FromHexString(pair[1]);
    return new global.BABYLON.Color3(
      (a.r + (b.r - a.r) * t) * lift,
      (a.g + (b.g - a.g) * t) * lift,
      (a.b + (b.b - a.b) * t) * lift
    );
  }

  function matrixBuffer(matrices) {
    /* thin instances take a flat Float32Array of 16-float matrices, not Matrix objects */
    var data = new Float32Array(matrices.length * 16);
    for (var i = 0; i < matrices.length; i++) matrices[i].copyToArray(data, i * 16);
    return data;
  }

  function placed(x, y, z, w, h, d) {
    /* A unit box scaled, then moved to its place. The order is the law here: Babylon takes
     * row vectors (v' = v * M), so the matrix is Scaling * Translation -- the translation is
     * applied last and stays raw. Translating first (Translation.multiply(Scaling)) would
     * multiply the position by the box size and scatter the city by its own footprints. */
    return BABYLON.Matrix.Scaling(w, h, d).multiply(BABYLON.Matrix.Translation(x, y, z));
  }

  /* ---- the camera rig: one camera, ortho <-> perspective, camera-relative movement ---------- */
  function Rig(camera, canvas) {
    this.camera = camera;
    this.canvas = canvas;
    this.state = { x: 0, z: 0, altitude: 170, pitch: 55, yaw: 40, mode: "perspective" };
    this.goal = Object.assign({}, this.state);
    this.fov = FOV;
  }

  Rig.prototype.aspect = function () {
    var height = this.canvas.clientHeight || 1;
    return (this.canvas.clientWidth || 1) / height;
  };

  Rig.prototype.basis = function () {
    /* the screen directions on the ground plane, for every pitch and yaw:
     * up = away from the camera in view direction (W), right = camera right (D). */
    var yaw = (this.state.yaw * Math.PI) / 180;
    return {
      up: { x: -Math.sin(yaw), z: -Math.cos(yaw) },
      right: { x: Math.cos(yaw), z: -Math.sin(yaw) }
    };
  };

  Rig.prototype.apply = function () {
    var s = this.state;
    var pitch = (s.pitch * Math.PI) / 180;
    var yaw = (s.yaw * Math.PI) / 180;
    var camera = this.camera;
    camera.position.set(
      s.x + s.altitude * Math.cos(pitch) * Math.sin(yaw),
      s.altitude * Math.sin(pitch),
      s.z + s.altitude * Math.cos(pitch) * Math.cos(yaw)
    );
    /* the up vector is what makes straight-down framing well-defined (no lookAt degeneracy) */
    camera.upVector = new global.BABYLON.Vector3(
      -Math.sin(yaw) * Math.sin(pitch),
      Math.cos(pitch),
      -Math.cos(yaw) * Math.sin(pitch)
    );
    camera.setTarget(new global.BABYLON.Vector3(s.x, 0, s.z));
    camera.fov = this.fov;
    var half = s.altitude * Math.tan(this.fov / 2); /* ortho framing == perspective framing here */
    if (s.mode === "ortho") {
      camera.mode = global.BABYLON.Camera.ORTHOGRAPHIC_CAMERA;
      camera.orthoTop = half;
      camera.orthoBottom = -half;
      camera.orthoLeft = -half * this.aspect();
      camera.orthoRight = half * this.aspect();
    } else {
      camera.mode = global.BABYLON.Camera.PERSPECTIVE_CAMERA;
    }
  };

  Rig.prototype.step = function (dt) {
    var rate = 1 - Math.exp(-dt * 6);
    for (var key of ["x", "z", "altitude", "pitch", "yaw"]) {
      this.state[key] += (this.goal[key] - this.state[key]) * rate;
    }
    this.state.mode = this.goal.mode;
    this.apply();
  };

  Rig.prototype.go = function (goal, instant) {
    Object.assign(this.goal, goal);
    if (instant) {
      Object.assign(this.state, goal);
      this.apply();
    }
  };

  Rig.prototype.fitRect = function (rect, target, pitch, yaw, mode) {
    /* the altitude at which the content box `rect` (world coords, x/z rectangle plus the
     * buildings' `top`) fills the frame around `target`: project its corners through the same
     * camera model `apply()` uses and bisect until every corner sits inside the frame.
     * Perspective magnifies the near edge and the tall tops — the parallel formula alone lets
     * the city spill out of the frame edge. Deterministic per document and pose. */
    var half = Math.tan(this.fov / 2);
    var aspect = Math.max(0.5, this.aspect());
    var yawRad = (yaw * Math.PI) / 180;
    var pitchRad = (pitch * Math.PI) / 180;
    var margin = 1.13;
    var halfW = rect.w / 2;
    var halfD = rect.d / 2;
    var top = rect.top || 0;
    var corners = [];
    for (var level of [0, top]) {
      for (var corner of [
        { x: rect.x - halfW, z: rect.z - halfD },
        { x: rect.x + halfW, z: rect.z - halfD },
        { x: rect.x - halfW, z: rect.z + halfD },
        { x: rect.x + halfW, z: rect.z + halfD }
      ]) {
        corners.push({ x: corner.x, y: level, z: corner.z });
      }
    }

    function fits(altitude) {
      /* the camera `apply()` builds: orbiting `target` at this pitch and yaw */
      var cx = target.x + altitude * Math.cos(pitchRad) * Math.sin(yawRad);
      var cy = altitude * Math.sin(pitchRad);
      var cz = target.z + altitude * Math.cos(pitchRad) * Math.cos(yawRad);
      var ux = -Math.sin(yawRad) * Math.sin(pitchRad); /* the up vector apply() sets */
      var uy = Math.cos(pitchRad);
      var uz = -Math.cos(yawRad) * Math.sin(pitchRad);
      var fx = target.x - cx;
      var fy = -cy;
      var fz = target.z - cz;
      var fl = Math.hypot(fx, fy, fz) || 1;
      fx /= fl;
      fy /= fl;
      fz /= fl;
      var rx = fy * uz - fz * uy;
      var ry = fz * ux - fx * uz;
      var rz = fx * uy - fy * ux;
      var rl = Math.hypot(rx, ry, rz) || 1;
      rx /= rl;
      ry /= rl;
      rz /= rl;
      for (var corner of corners) {
        var dx = corner.x - cx;
        var dy = corner.y - cy;
        var dz = corner.z - cz;
        var sx;
        var sy;
        if (mode === "ortho") {
          sx = (dx * rx + dy * ry + dz * rz) / (altitude * half * aspect);
          sy = (dx * ux + dy * uy + dz * uz) / (altitude * half);
        } else {
          var depth = dx * fx + dy * fy + dz * fz; /* distance along the view ray (f = to target) */
          if (depth <= 0.001) return false;
          sx = (dx * rx + dy * ry + dz * rz) / (depth * half * aspect);
          sy = (dx * ux + dy * uy + dz * uz) / (depth * half);
        }
        if (Math.abs(sx) * margin > 1 || Math.abs(sy) * margin > 1) return false;
      }
      return true;
    }

    var low = 12;
    var high = 600;
    if (!fits(high)) return 600;
    for (var iteration = 0; iteration < 32; iteration++) {
      var mid = (low + high) / 2;
      if (fits(mid)) high = mid;
      else low = mid;
    }
    return high;
  };

  Rig.prototype.lod = function () {
    for (var level of LOD_LEVELS) {
      if (this.state.altitude > level.above || level.above < 0) return level;
    }
    return LOD_LEVELS[LOD_LEVELS.length - 1];
  };

  Rig.prototype.view = function () {
    /* the ground rectangle the camera looks at (for the minimap viewport), the four corners of
     * the framed ground quad (the quad rotates with the yaw; the minimap draws the polygon),
     * and the yaw itself (the HUD compass needle). Screen up on the ground is basis.up; screen
     * right is the camera's true right = -basis.right (the rig's `right` is what D moves along,
     * the lookAt hands the mirrored axis back — the minimap mirrors its x for the same reason). */
    var half = this.state.altitude * Math.tan(this.fov / 2);
    var slant = Math.max(0.45, Math.sin((this.state.pitch * Math.PI) / 180));
    var spanZ = (2 * half) / slant;
    var basis = this.basis();
    var up = basis.up;
    var right = { x: -basis.right.x, z: -basis.right.z };
    var halfW = (spanZ * this.aspect()) / 2;
    var halfH = spanZ / 2;
    var x = this.state.x;
    var z = this.state.z;
    function corner(u, r) {
      return {
        x: x + up.x * u * halfH + right.x * r * halfW,
        z: z + up.z * u * halfH + right.z * r * halfW
      };
    }
    return {
      x: x,
      z: z,
      w: spanZ * this.aspect(),
      h: spanZ,
      yaw: this.state.yaw,
      corners: [corner(1, 1), corner(1, -1), corner(-1, -1), corner(-1, 1)]
    };
  };

  /* ---- building the scene from the document ------------------------------------------------ */
  function buildWorld(scene, doc, glow) {
    var BABYLON = global.BABYLON;
    var built = {
      groups: {},
      cellsById: {},
      cellList: [],
      edges: doc.edges || [],
      ring: doc.ring
    };

    /* materials: dark tactical ground, PBR, flat theme colors for the chrome */
    function pbr(name, hex, metallic, roughness) {
      var material = new BABYLON.PBRMaterial(name, scene);
      material.albedoColor = BABYLON.Color3.FromHexString(hex);
      material.metallic = metallic;
      material.roughness = roughness;
      return material;
    }
    function roleMaterial(name, emissiveHex, emissiveScale) {
      var material = new BABYLON.PBRMaterial(name, scene);
      material.albedoColor = BABYLON.Color3.White(); /* per-instance colors carry the palette */
      material.metallic = 0.18;
      material.roughness = 0.62;
      if (emissiveHex) {
        material.emissiveColor = BABYLON.Color3.FromHexString(emissiveHex).scale(emissiveScale);
      }
      return material;
    }
    function flatPaint(name, hex, alpha) {
      /* street paint: unlit, flat, semi-transparent — markings painted on the ground, never
       * volumes with lit faces, side walls or their own shadows */
      var material = new BABYLON.StandardMaterial(name, scene);
      var shade = BABYLON.Color3.FromHexString(hex);
      material.diffuseColor = shade;
      material.emissiveColor = shade;
      material.specularColor = new BABYLON.Color3(0, 0, 0);
      material.disableLighting = true;
      material.alpha = alpha === undefined ? 1 : alpha;
      return material;
    }

    /* the world bounds: the arena's platform (the plots and the ring road) — the plinth and
     * the minimap use it; the camera poses frame the content bounds below */
    var minX = 0;
    var maxX = 1;
    var minZ = 0;
    var maxZ = 1;
    for (var region of doc.regions) {
      minX = Math.min(minX, region.plot.x);
      maxX = Math.max(maxX, region.plot.x + region.plot.w);
      minZ = Math.min(minZ, region.plot.z);
      maxZ = Math.max(maxZ, region.plot.z + region.plot.d);
    }
    if (doc.ring && doc.ring.path) {
      for (var ringCorner of doc.ring.path) {
        minX = Math.min(minX, ringCorner.x);
        maxX = Math.max(maxX, ringCorner.x);
        minZ = Math.min(minZ, ringCorner.z);
        maxZ = Math.max(maxZ, ringCorner.z);
      }
    }
    built.bounds = {
      x: (minX + maxX) / 2,
      z: (minZ + maxZ) / 2,
      w: maxX - minX,
      d: maxZ - minZ
    };

    /* the content bounds and the content centroid: what the whole-arena poses frame and where
     * they look. The ring road is context; the content is the district plots and the cell lots
     * with their footprints. The centroid is the mass of the city (lots and void pits weighted
     * by their area) — empty districts stay in the frame as floor but never pull the camera
     * into a dead corner */
    var cMinX = Infinity;
    var cMaxX = -Infinity;
    var cMinZ = Infinity;
    var cMaxZ = -Infinity;
    var mass = 0;
    var massX = 0;
    var massZ = 0;
    function addContent(x, z, halfW, halfD, weight) {
      cMinX = Math.min(cMinX, x - halfW);
      cMaxX = Math.max(cMaxX, x + halfW);
      cMinZ = Math.min(cMinZ, z - halfD);
      cMaxZ = Math.max(cMaxZ, z + halfD);
      mass += weight;
      massX += x * weight;
      massZ += z * weight;
    }
    for (var contentRegion of doc.regions) {
      var contentPlot = contentRegion.plot;
      addContent(
        contentPlot.x + contentPlot.w / 2,
        contentPlot.z + contentPlot.d / 2,
        contentPlot.w / 2,
        contentPlot.d / 2,
        0
      );
    }
    for (var contentCell of doc.cells) {
      var lot = contentCell.footprint / 2;
      addContent(contentCell.plot.x, contentCell.plot.z, lot, lot, contentCell.footprint * contentCell.footprint);
    }
    for (var contentVoid of doc.voids) {
      var pit = contentVoid.footprint / 2;
      addContent(contentVoid.plot.x, contentVoid.plot.z, pit, pit, contentVoid.footprint * contentVoid.footprint * 2);
    }
    built.content = {
      x: (cMinX + cMaxX) / 2,
      z: (cMinZ + cMaxZ) / 2,
      w: cMaxX - cMinX,
      d: cMaxZ - cMinZ
    };
    var contentTop = 0;
    for (var heightCell of doc.cells) {
      contentTop = Math.max(contentTop, Number(heightCell.height) || 0);
    }
    built.content.top = contentTop + 5; /* the buildings' height, with the labels' headroom */
    built.centroid =
      mass > 0
        ? { x: massX / mass, z: massZ / mass }
        : { x: built.content.x, z: built.content.z };

    /* the outer ground and the arena plinth share one grid language: world-sized cells
     * (grid.step per cell) with real line contrast — the floor must read as tiles at any zoom */
    var gridStep = (doc.grid && doc.grid.step) || 3.4;
    function gridTexture(name, sizeWu, depthWu, base, minor, major) {
      var texture = new BABYLON.DynamicTexture(name, { width: 1024, height: 1024 }, scene, true);
      var brush = texture.getContext();
      brush.fillStyle = base;
      brush.fillRect(0, 0, 1024, 1024);
      for (var line = 0; line <= 1024; line += 128) {
        var thick = line % 512 === 0;
        brush.strokeStyle = thick ? major : minor;
        brush.lineWidth = thick ? 3 : 1.5;
        brush.beginPath();
        brush.moveTo(line, 0);
        brush.lineTo(line, 1024);
        brush.moveTo(0, line);
        brush.lineTo(1024, line);
        brush.stroke();
      }
      texture.update(false);
      texture.wrapU = BABYLON.Texture.WRAP_ADDRESSMODE;
      texture.wrapV = BABYLON.Texture.WRAP_ADDRESSMODE;
      texture.uScale = sizeWu / (8 * gridStep); /* one texture = 8 placement-grid cells */
      texture.vScale = depthWu / (8 * gridStep);
      return texture;
    }

    /* the outer ground: dark, gridded, the arena's floor */
    var groundMaterial = pbr("ground", "#ffffff", 0.05, 1.0); /* the texture carries the color */
    groundMaterial.albedoTexture = gridTexture(
      "ground-grid",
      4000,
      4000,
      "#0e141b",
      "#1b2530",
      "#2b3a49"
    );

    var ground = BABYLON.MeshBuilder.CreateGround("ground", { width: 4000, height: 4000 }, scene);
    ground.material = groundMaterial;
    ground.receiveShadows = true;
    ground.isPickable = false;

    /* the arena plinth: the platform the world stands on, inside the ring road — the seam
     * between the arena and the void beyond it, on the same placement grid */
    var plinth = BABYLON.MeshBuilder.CreateBox("arena-plinth", { size: 1 }, scene);
    var plinthMaterial = pbr("arena-plinth-material", "#ffffff", 0.06, 0.95);
    plinthMaterial.albedoTexture = gridTexture(
      "plinth-grid",
      built.bounds.w + 8,
      built.bounds.d + 8,
      "#141c25",
      "#223040",
      "#31435a"
    );
    plinth.material = plinthMaterial;
    plinth.isPickable = false;
    plinth.position = new BABYLON.Vector3(built.bounds.x, 0.06, built.bounds.z);
    plinth.scaling = new BABYLON.Vector3(built.bounds.w + 8, 0.12, built.bounds.d + 8);

    /* ---- districts: tiled floor plots (the placement raster of the exporter) --------------- */
    var tileMeshes = {};
    var tileMatrices = { header: [], index: [], cells: [] };
    var tileColors = { header: [], index: [], cells: [] };
    for (var band of ["header", "index", "cells"]) {
      var mesh = BABYLON.MeshBuilder.CreateBox("tiles-" + band, { size: 1 }, scene);
      mesh.material = roleMaterial("tiles-" + band + "-material");
      mesh.isPickable = false;
      mesh.alwaysSelectAsActiveMesh = true;
      mesh.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, true);
      mesh.thinInstanceSetBuffer("color", [], 4, true);
      tileMeshes[band] = mesh;
    }
    var baseMesh = BABYLON.MeshBuilder.CreateBox("plot-bases", { size: 1 }, scene);
    baseMesh.material = pbr("plot-base", "#10161d", 0.1, 0.9); /* the dark street between tiles */
    baseMesh.isPickable = false;
    baseMesh.alwaysSelectAsActiveMesh = true;
    baseMesh.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, true);
    var baseMatrices = [];
    var rimMesh = BABYLON.MeshBuilder.CreateBox("plot-rims", { size: 1 }, scene);
    rimMesh.material = roleMaterial("plot-rims-material", "#7fa4b4", 0.05);
    rimMesh.isPickable = false;
    rimMesh.alwaysSelectAsActiveMesh = true;
    rimMesh.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, true);
    rimMesh.thinInstanceSetBuffer("color", [], 4, true);
    var rimMatrices = [];
    var rimColors = [];

    for (var region of doc.regions) {
      var plot = region.plot;
      var raster = region.tiles;
      var bandName = tileMatrices[region.band] ? region.band : "cells";
      baseMatrices.push(placed(plot.x + plot.w / 2, 0.145, plot.z + plot.d / 2, plot.w, 0.05, plot.d));
      /* the district's own tint: one band family, one district — the floor reads as districts */
      var district = tintOf(bandName, region.start + plot.x * 16 + plot.z);
      var lit = region.empty ? 0.55 : 1;
      /* every tile of the district's raster: a floor plate with street-dark gaps */
      var inset = 0.55;
      for (var row = 0; row < raster.rows; row++) {
        for (var col = 0; col < raster.cols; col++) {
          var tileX = plot.x + (col + 0.5) * raster.step_x;
          var tileZ = plot.z + (row + 0.5) * raster.step_z;
          tileMatrices[bandName].push(
            placed(tileX, 0.165, tileZ, raster.step_x - inset, 0.05, raster.step_z - inset)
          );
          var vary = 0.96 + 0.08 * hash(col * 31 + row * 17 + Math.round(plot.x * 7));
          tileColors[bandName].push(
            district.r * lit * vary,
            district.g * lit * vary,
            district.b * lit * vary,
            1
          );
        }
      }
      /* the district's edge: a quiet painted border, flat on the ground like the streets */
      var edge = 0.26;
      var rimTint = district.scale(region.empty ? 1.0 : 1.4);
      var rimY = 0.185;
      var rimSides = [
        [plot.x + plot.w / 2, rimY, plot.z + edge / 2, plot.w + edge, 0.05, edge],
        [plot.x + plot.w / 2, rimY, plot.z + plot.d - edge / 2, plot.w + edge, 0.05, edge],
        [plot.x + edge / 2, rimY, plot.z + plot.d / 2, edge, 0.05, plot.d - edge],
        [plot.x + plot.w - edge / 2, rimY, plot.z + plot.d / 2, edge, 0.05, plot.d - edge]
      ];
      for (var side of rimSides) {
        rimMatrices.push(placed(side[0], side[1], side[2], side[3], side[4], side[5]));
        rimColors.push(rimTint.r, rimTint.g, rimTint.b, 1);
      }
    }
    for (var bandKey of ["header", "index", "cells"]) {
      tileMeshes[bandKey].thinInstanceSetBuffer("matrix", matrixBuffer(tileMatrices[bandKey]), 16, true);
      tileMeshes[bandKey].thinInstanceSetBuffer("color", tileColors[bandKey], 4, true);
    }
    baseMesh.thinInstanceSetBuffer("matrix", matrixBuffer(baseMatrices), 16, true);
    rimMesh.thinInstanceSetBuffer("matrix", matrixBuffer(rimMatrices), 16, true);
    rimMesh.thinInstanceSetBuffer("color", rimColors, 4, true);

    /* ---- buildings: one block stack per cell, floors = the cell anatomy -------------------- */
    var roleMeshes = {};
    var roleMatrices = {};
    var roleColors = {};
    function roleMesh(role) {
      if (!roleMeshes[role]) {
        var mesh = BABYLON.MeshBuilder.CreateBox("floors-" + role, { size: 1 }, scene);
        /* every anatomy floor self-lights faintly by its role color: the block view must read
         * the regions (checksum teal, poison red, generation gold) at any light angle */
        mesh.material = roleMaterial(
          "floors-" + role + "-material",
          role === "poison" ? CABLE_STALE : ROLE_COLORS[role] || ROLE_COLORS.payload,
          role === "checksum" ? 0.55 : role === "poison" ? 0.28 : role === "generation" ? 0.24 : 0.16
        );
        mesh.isPickable = false;
        mesh.alwaysSelectAsActiveMesh = true;
        mesh.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, true);
        mesh.thinInstanceSetBuffer("color", [], 4, true);
        roleMeshes[role] = mesh;
        roleMatrices[role] = [];
        roleColors[role] = [];
      }
      return roleMeshes[role];
    }

    function floorColor(role, cell) {
      var vary = 0.92 + 0.16 * hash(cell.offset);
      if (role === "forward" && cell.forward) return color(FORWARD_HOT, vary);
      if (role === "payload") return color(FAMILY_COLORS[cell.family], vary);
      return color(ROLE_COLORS[role] || ROLE_COLORS.payload, vary);
    }

    var volumeMatrices = []; /* the L1 buildings: one volume in the family's paint */
    var volumeColors = [];
    for (var cell of doc.cells) {
      built.cellsById[cell.id] = cell;
      built.cellList.push(cell);
      volumeMatrices.push(
        placed(cell.plot.x, cell.height / 2, cell.plot.z, cell.footprint, cell.height, cell.footprint)
      );
      var volumeTint = color(FAMILY_COLORS[cell.family] || FAMILY_COLORS.block, 0.92 + 0.16 * hash(cell.offset));
      volumeColors.push(volumeTint.r, volumeTint.g, volumeTint.b, 1);
      var bands = cell.anatomy;
      var total = Math.max(1, cell.size);
      var raws = [];
      var rawSum = 0;
      for (var bandIndex = 0; bandIndex < bands.length; bandIndex++) {
        var raw = Math.max(0.22, (cell.height * bands[bandIndex].size) / total);
        raws.push(raw);
        rawSum += raw;
      }
      var shrink = cell.height / rawSum;
      var y = 0;
      var width = cell.footprint;
      for (var floor = 0; floor < bands.length; floor++) {
        var floorRole = bands[floor].role;
        var floorHeight = raws[floor] * shrink;
        roleMesh(floorRole);
        roleMatrices[floorRole].push(
          placed(cell.plot.x, y + floorHeight / 2, cell.plot.z, width, floorHeight, width)
        );
        var tint = floorColor(floorRole, cell);
        roleColors[floorRole].push(tint.r, tint.g, tint.b, 1);
        y += floorHeight;
      }
    }
    for (var roleKey of Object.keys(roleMeshes)) {
      roleMeshes[roleKey].thinInstanceSetBuffer("matrix", matrixBuffer(roleMatrices[roleKey]), 16, true);
      roleMeshes[roleKey].thinInstanceSetBuffer("color", roleColors[roleKey], 4, true);
    }
    /* the L1 building volume: the massing reads as buildings before the anatomy floors of
     * L2 arrive — one box per cell in the family's paint, one draw call for the city */
    var volumeMesh = BABYLON.MeshBuilder.CreateBox("building-volumes", { size: 1 }, scene);
    volumeMesh.material = roleMaterial("building-volumes-material", "#ffffff", 0.16);
    volumeMesh.isPickable = false;
    volumeMesh.alwaysSelectAsActiveMesh = true;
    volumeMesh.thinInstanceSetBuffer("matrix", matrixBuffer(volumeMatrices), 16, true);
    volumeMesh.thinInstanceSetBuffer("color", volumeColors, 4, true);

    /* caps: the silhouettes that tell the families apart (tower beacons, monument crowns) */
    var beaconMesh = BABYLON.MeshBuilder.CreateBox("beacons", { size: 1 }, scene);
    beaconMesh.material = roleMaterial("beacon-material", CABLE_FRESH, 0.9);
    beaconMesh.isPickable = false;
    beaconMesh.alwaysSelectAsActiveMesh = true;
    beaconMesh.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, true);
    var crownMesh = BABYLON.MeshBuilder.CreateBox("crowns", { size: 1 }, scene);
    crownMesh.material = roleMaterial("crown-material", "#c2a163", 0.35);
    crownMesh.metallic = 0.6;
    crownMesh.isPickable = false;
    crownMesh.alwaysSelectAsActiveMesh = true;
    crownMesh.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, true);
    var beaconMatrices = [];
    var crownMatrices = [];
    for (var capped of built.cellList) {
      if (capped.family === "tower") {
        beaconMatrices.push(
          placed(capped.plot.x, capped.height + 0.45, capped.plot.z, 0.34, 0.9, 0.34)
        );
      } else if (capped.family === "monument") {
        crownMatrices.push(
          placed(capped.plot.x, capped.height + 0.12, capped.plot.z, capped.footprint * 0.92, 0.24, capped.footprint * 0.92)
        );
      }
    }
    beaconMesh.thinInstanceSetBuffer("matrix", matrixBuffer(beaconMatrices), 16, true);
    crownMesh.thinInstanceSetBuffer("matrix", matrixBuffer(crownMatrices), 16, true);

    /* ---- void tiles: poison pits, clearly red ------------------------------------------------ */
    var voidMaterial = pbr("void", "#4a1416", 0.1, 0.98);
    voidMaterial.emissiveColor = new BABYLON.Color3(0.62, 0.08, 0.07);
    var voidMesh = BABYLON.MeshBuilder.CreateBox("voids", { size: 1 }, scene);
    voidMesh.material = voidMaterial;
    voidMesh.isPickable = false;
    voidMesh.alwaysSelectAsActiveMesh = true;
    voidMesh.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, true);
    var voidMatrices = [];
    for (var tile of doc.voids) {
      voidMatrices.push(
        placed(tile.plot.x, 0.235, tile.plot.z, tile.footprint, 0.05, tile.footprint)
      );
    }
    voidMesh.thinInstanceSetBuffer("matrix", matrixBuffer(voidMatrices), 16, true);

    /* ---- cables: ground traces on the tile grid (circuit board, not cable car) ------------ */
    function snap(value) {
      return Math.round(value / gridStep) * gridStep;
    }
    function chamfer(points, radius) {
      /* cut every corner at 45°, the way a PCB router does */
      if (points.length < 3) return points;
      var out = [points[0]];
      for (var i = 1; i < points.length - 1; i++) {
        var previous = points[i - 1];
        var point = points[i];
        var next = points[i + 1];
        var ux = point.x - previous.x;
        var uz = point.z - previous.z;
        var vx = next.x - point.x;
        var vz = next.z - point.z;
        var uLength = Math.hypot(ux, uz) || 1;
        var vLength = Math.hypot(vx, vz) || 1;
        var r = Math.min(radius, uLength / 2, vLength / 2);
        out.push({ x: point.x - (ux / uLength) * r, z: point.z - (uz / uLength) * r });
        out.push({ x: point.x + (vx / vLength) * r, z: point.z + (vz / vLength) * r });
      }
      out.push(points[points.length - 1]);
      return out;
    }
    function route(from, to) {
      /* Manhattan routing between two buildings: leave the lot, run on the grid, arrive */
      var points;
      if (Math.abs(from.x - to.x) < 0.01 || Math.abs(from.z - to.z) < 0.01) {
        points = [from, to];
      } else {
        var midX = snap((from.x + to.x) / 2);
        points = [from, { x: midX, z: from.z }, { x: midX, z: to.z }, to];
      }
      return chamfer(points, 0.8);
    }

    /* the wiring runs in three tiers — the skeleton (both endpoints named), the backbone
     * (one endpoint named) and the data wiring — one detail limit per LOD level, so the
     * wide views stay alive and the close views carry everything. */
    var tiers = [];
    for (var tierIndex = 0; tierIndex < 3; tierIndex++) {
      tiers.push({ base: [], fresh: [], stale: [], pads: [], padColors: [] });
    }
    function traceSegments(points, thickness, width, core, fresh, tier) {
      /* one stretched box per leg, oriented along the leg's 3D slope: the trace flies */
      for (var i = 0; i < points.length - 1; i++) {
        var a = points[i];
        var b = points[i + 1];
        var dx = b.x - a.x;
        var dy = b.y - a.y;
        var dz = b.z - a.z;
        var length = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (length < 0.01) continue;
        var yaw = Math.atan2(dx, dz);
        var pitch = Math.atan2(dy, Math.hypot(dx, dz));
        var rotation = BABYLON.Quaternion.RotationAxis(BABYLON.Axis.Y, yaw).multiply(
          BABYLON.Quaternion.RotationAxis(BABYLON.Axis.X, -pitch)
        );
        var matrix = BABYLON.Matrix.Compose(
          new BABYLON.Vector3(width, thickness, length),
          rotation,
          new BABYLON.Vector3((a.x + b.x) / 2, (a.y + b.y) / 2 + thickness / 2, (a.z + b.z) / 2)
        );
        var bucket = core ? (fresh ? tier.fresh : tier.stale) : tier.base;
        bucket.push(matrix);
      }
    }
    function climb(points, cruise) {
      /* the polyline gains altitudes: pad level at both ends, a ramp over the first and last
       * stretch, cruise in between — the cable leaves its pad, flies its sheet, dives into
       * the target's pad */
      var spans = [];
      var total = 0;
      for (var i = 0; i < points.length - 1; i++) {
        var span = Math.hypot(points[i + 1].x - points[i].x, points[i + 1].z - points[i].z);
        spans.push(span);
        total += span;
      }
      var ramp = Math.min(RAMP, total / 2);
      var walked = 0;
      var out = [];
      for (var i = 0; i < points.length; i++) {
        var away = Math.min(walked, total - walked);
        var t = ramp > 0 ? Math.min(1, away / ramp) : 1;
        out.push({ x: points[i].x, z: points[i].z, y: PAD_LIFT + (cruise - PAD_LIFT) * t });
        walked += spans[i] || 0;
      }
      return out;
    }
    function flightAltitude(from, to) {
      /* the sheet rule: a cable flies at the module depth of its deepest endpoint */
      var depth = Math.max(from ? from.depth || 0 : 0, to ? to.depth || 0 : 0);
      return FLIGHT_BASE + Math.min(FLIGHT_MAX, depth) * FLIGHT_STEP;
    }

    var graveMatrices = [];
    function padAt(x, z, fresh, tier) {
      /* every trace plugs into something: a pad at each endpoint, never a naked line */
      tier.pads.push(placed(x, 0.40, z, 1.6, 0.34, 1.6));
      var padTint = color(fresh ? CABLE_FRESH : CABLE_STALE, 0.6);
      tier.padColors.push(padTint.r, padTint.g, padTint.b, 1);
    }
    for (var edge of doc.edges) {
      var source = built.cellsById[edge.from];
      if (!source) continue;
      var target = edge.to ? built.cellsById[edge.to] : null;
      /* the exporter routes every cable along the street network (``route``): out of the source's
       * lot, the lanes and avenues across the city, into the target's lot — never across a
       * building volume. The local Manhattan run is the fallback for scenes without routes. */
      var edgeRoute = edge.route && edge.route.length >= 2 ? edge.route : null;
      var start = edgeRoute
        ? { x: edgeRoute[0].x, z: edgeRoute[0].z }
        : { x: source.plot.x, z: source.plot.z };
      var end;
      if (edgeRoute) {
        end = { x: edgeRoute[edgeRoute.length - 1].x, z: edgeRoute[edgeRoute.length - 1].z };
      } else if (target) {
        end = { x: target.plot.x, z: target.plot.z };
      } else {
        /* the grave of a stale handle: a deterministic stub beside its source */
        var graveAngle = hash(edge.to_handle) * 2 * Math.PI;
        end = {
          x: source.plot.x + Math.cos(graveAngle) * 3.4,
          z: source.plot.z + Math.sin(graveAngle) * 3.4
        };
      }
      if (!target) {
        graveMatrices.push(placed(end.x, 0.34, end.z, 0.8, 0.6, 0.8));
      }
      var namedA = source.family === "tower" || source.family === "monument";
      var namedB = target !== null && (target.family === "tower" || target.family === "monument");
      var tier = namedA && namedB ? tiers[0] : namedA || namedB ? tiers[1] : tiers[2];
      padAt(start.x, start.z, edge.fresh, tier);
      padAt(end.x, end.z, edge.fresh, tier);
      var points = edgeRoute || route(start, end);
      var stagger = (edge.index === null ? 0 : edge.index % 3) * 0.14; /* same-sheet crossings */
      var cruise = flightAltitude(source, target) + stagger;
      traceSegments(climb(points, cruise), 0.14, 0.75, false, edge.fresh, tier);
      traceSegments(climb(points, cruise + 0.18), 0.10, 0.24, true, edge.fresh, tier);
    }
    /* one material per voice, one mesh set per tier: every sheet draws the same paint */
    var traceBaseMaterial = pbr("traces-base-material", "#1a2a33", 0.3, 0.7);
    traceBaseMaterial.emissiveColor = new BABYLON.Color3(0.04, 0.09, 0.11);
    var traceFreshMaterial = roleMaterial("traces-fresh-material", CABLE_FRESH, 0.85);
    var traceStaleMaterial = roleMaterial("traces-stale-material", CABLE_STALE, 0.85);
    var tracePadMaterial = roleMaterial("trace-pads-material", "#ffffff", 0.5);
    function tierMesh(name, t, material, matrices, colors) {
      var mesh = BABYLON.MeshBuilder.CreateBox(name + "-" + t, { size: 1 }, scene);
      mesh.material = material;
      mesh.isPickable = false;
      mesh.alwaysSelectAsActiveMesh = true;
      mesh.thinInstanceSetBuffer("matrix", matrixBuffer(matrices), 16, true);
      if (colors) mesh.thinInstanceSetBuffer("color", colors, 4, true);
      return mesh;
    }
    var tierMeshes = [];
    for (var t = 0; t < tiers.length; t++) {
      tierMeshes.push({
        base: tierMesh("traces-base", t, traceBaseMaterial, tiers[t].base),
        fresh: tierMesh("traces-fresh", t, traceFreshMaterial, tiers[t].fresh),
        stale: tierMesh("traces-stale", t, traceStaleMaterial, tiers[t].stale),
        pads: tierMesh("trace-pads", t, tracePadMaterial, tiers[t].pads, tiers[t].padColors)
      });
    }

    var graveMesh = BABYLON.MeshBuilder.CreateBox("graves", { size: 1 }, scene);
    graveMesh.material = pbr("grave", "#2a1512", 0.1, 0.9);
    graveMesh.material.emissiveColor = new BABYLON.Color3(0.5, 0.05, 0.03);
    graveMesh.isPickable = false;
    graveMesh.alwaysSelectAsActiveMesh = true;
    graveMesh.thinInstanceSetBuffer("matrix", matrixBuffer(graveMatrices), 16, true);

    for (var glowTier of tierMeshes) {
      glow.addIncludedOnlyMesh(glowTier.fresh);
      glow.addIncludedOnlyMesh(glowTier.stale);
    }
    glow.addIncludedOnlyMesh(beaconMesh);
    glow.addIncludedOnlyMesh(graveMesh);

    /* the selection's own wires: pick a building and its cables light up on their sheets —
     * the interactive untangler on top of the stacked sheets (built.wire, called on select) */
    var wireBaseMaterial = roleMaterial("selection-wire-material", "#c8a24a", 0.7);
    var wireCoreMaterial = roleMaterial("selection-wire-core-material", "#ffd27a", 0.9);
    var wireBase = BABYLON.MeshBuilder.CreateBox("selection-wire-base", { size: 1 }, scene);
    wireBase.material = wireBaseMaterial;
    wireBase.isPickable = false;
    wireBase.alwaysSelectAsActiveMesh = true;
    wireBase.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, false);
    var wireCore = BABYLON.MeshBuilder.CreateBox("selection-wire-core", { size: 1 }, scene);
    wireCore.material = wireCoreMaterial;
    wireCore.isPickable = false;
    wireCore.alwaysSelectAsActiveMesh = true;
    wireCore.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, false);
    wireBase.setEnabled(false);
    wireCore.setEnabled(false);
    glow.addIncludedOnlyMesh(wireCore);
    built.wire = function (cell) {
      var base = [];
      var core = [];
      if (cell) {
        for (var edge of doc.edges) {
          if (edge.from !== cell.id && edge.to !== cell.id) continue;
          var from = built.cellsById[edge.from];
          if (!from) continue;
          var to = edge.to ? built.cellsById[edge.to] : null;
          var points =
            edge.route && edge.route.length >= 2
              ? edge.route
              : route(
                  { x: from.plot.x, z: from.plot.z },
                  to ? { x: to.plot.x, z: to.plot.z } : { x: from.plot.x + 3.4, z: from.plot.z + 3.4 }
                );
          var stagger = (edge.index === null ? 0 : edge.index % 3) * 0.14;
          var cruise = flightAltitude(from, to) + stagger;
          traceSegments(climb(points, cruise), 0.22, 1.0, false, true, {
            base: base, fresh: [], stale: []
          });
          traceSegments(climb(points, cruise + 0.24), 0.12, 0.34, true, true, {
            base: [], fresh: core, stale: []
          });
        }
      }
      wireBase.thinInstanceSetBuffer("matrix", matrixBuffer(base), 16, false);
      wireCore.thinInstanceSetBuffer("matrix", matrixBuffer(core), 16, false);
      wireBase.setEnabled(base.length > 0);
      wireCore.setEnabled(core.length > 0);
    };

    /* ---- the ready-ring: the ring road of the document, with lanes and tram units --------- */
    var ringRoad = null;
    var ringMarkings = []; /* the bright edge lines: flat paint along both band boundaries */
    var dashMesh = null;
    var tramMesh = null;
    var trams = [];
    var loop = null;
    if (doc.ring && (doc.ring.path || []).length > 3) {
      var ring = doc.ring;
      var path = ring.path;
      loop = makeLoop(path);

      /* the road: ONE flat ribbon along the closed path. A stack of per-leg boxes wedges open
       * on the outside of every chamfer (the black needle gaps at the corners) and spikes where
       * short detour legs miter together; a clamped-miter band has no joints at all. */
      var half = RING_ROAD_WIDTH / 2;
      var leftEdge = [];
      var rightEdge = [];
      var points = path.length;
      for (var leg = 0; leg < points; leg++) {
        var previous = path[(leg - 1 + points) % points];
        var point = path[leg];
        var next = path[(leg + 1) % points];
        var d0x = (point.x - previous.x) / (Math.hypot(point.x - previous.x, point.z - previous.z) || 1);
        var d0z = (point.z - previous.z) / (Math.hypot(point.x - previous.x, point.z - previous.z) || 1);
        var d1x = (next.x - point.x) / (Math.hypot(next.x - point.x, next.z - point.z) || 1);
        var d1z = (next.z - point.z) / (Math.hypot(next.x - point.x, next.z - point.z) || 1);
        /* the band's offset direction: the average of the legs' left normals */
        var mx = -d0z - d1z;
        var mz = d0x + d1x;
        var mLength = Math.hypot(mx, mz) || 1;
        var nx = mx / mLength;
        var nz = mz / mLength;
        var miter = half / Math.max(0.45, nx * -d1z + nz * d1x); /* clamped: no needle spikes */
        leftEdge.push(new BABYLON.Vector3(point.x - nz * miter, 0.216, point.z + nx * miter));
        rightEdge.push(new BABYLON.Vector3(point.x + nz * miter, 0.216, point.z - nx * miter));
      }
      ringRoad = BABYLON.MeshBuilder.CreateRibbon(
        "ring-road",
        { pathArray: [leftEdge, rightEdge], closePath: true, sideOrientation: BABYLON.Mesh.DOUBLESIDE },
        scene
      );
      ringRoad.material = flatPaint("ring-asphalt", RING_ASPHALT, 0.92);
      ringRoad.isPickable = false;

      /* the edge markings: bright flat paint along both boundaries of the band. The ring is
       * the landmark of the map — the asphalt alone (muted, like the streets) reads as a shade
       * off the background, so the paint carries edge lines that stay FLAT ground markings:
       * ribbons lifted 8 mm over the asphalt, never curbs, walls or shadows. */
      function markingLip(edge, other, width, y) {
        /* the boundary pulled `width` toward the road's middle line: the band's inner lip */
        var line = [];
        for (var i = 0; i < edge.length; i++) {
          var toward = other[i];
          var dx = toward.x - edge[i].x;
          var dz = toward.z - edge[i].z;
          var span = Math.hypot(dx, dz) || 1;
          line.push(new BABYLON.Vector3(edge[i].x + (dx / span) * width, y, edge[i].z + (dz / span) * width));
        }
        return line;
      }
      function markingLift(edge, y) {
        return edge.map(function (point) {
          return new BABYLON.Vector3(point.x, y, point.z);
        });
      }
      var markingY = 0.224;
      for (var markingSides of [
        [markingLift(leftEdge, markingY), markingLip(leftEdge, rightEdge, 0.5, markingY)],
        [markingLift(rightEdge, markingY), markingLip(rightEdge, leftEdge, 0.5, markingY)]
      ]) {
        var marking = BABYLON.MeshBuilder.CreateRibbon(
          "ring-edge-" + ringMarkings.length,
          { pathArray: markingSides, closePath: true, sideOrientation: BABYLON.Mesh.DOUBLESIDE },
          scene
        );
        marking.material = flatPaint("ring-edge-material", RING_MARK, 0.95);
        marking.isPickable = false;
        ringMarkings.push(marking);
      }

      /* the lane markings: flat paint at an even arc-length rhythm along the whole loop */
      var dashMatrices = [];
      for (var dashAt = 3; dashAt < loop.length - 3; dashAt += 7) {
        var spot = loop.at(dashAt / loop.length);
        dashMatrices.push(
          BABYLON.Matrix.Compose(
            new BABYLON.Vector3(2.2, 0.012, 0.34),
            BABYLON.Quaternion.FromEulerAngles(0, spot.heading, 0),
            new BABYLON.Vector3(spot.x, 0.222, spot.z)
          )
        );
      }
      dashMesh = BABYLON.MeshBuilder.CreateBox("lanes", { size: 1 }, scene);
      dashMesh.material = flatPaint("dash-material", RING_DASH, 0.95);
      dashMesh.isPickable = false;
      dashMesh.alwaysSelectAsActiveMesh = true;
      dashMesh.thinInstanceSetBuffer("matrix", matrixBuffer(dashMatrices), 16, true);

      /* tram units: one per ring slot — a run the image places on the road (READY/RUNNING);
       * a PARKED run is off the road and gets no unit (docs/23 §4.5) */
      tramMesh = BABYLON.MeshBuilder.CreateBox("trams", { size: 1 }, scene);
      tramMesh.material = roleMaterial("tram-material", CABLE_FRESH, 0.5);
      tramMesh.isPickable = false;
      tramMesh.alwaysSelectAsActiveMesh = true;
      var lanes = Math.max(1, ring.lanes || 1);
      for (var tramIndex = 0; tramIndex < (doc.trams || []).length; tramIndex++) {
        var tram = doc.trams[tramIndex];
        trams.push({
          phase: tram.angle / (2 * Math.PI),
          lane: ((tramIndex % lanes) - (lanes - 1) / 2) * 3.2
        });
      }
      tramMesh.thinInstanceSetBuffer("matrix", matrixBuffer(tramMatrices(0)), 16, true);
      glow.addIncludedOnlyMesh(tramMesh);
      built.trams = trams;
      built.tramMesh = tramMesh;
      built.tramMatrices = tramMatrices;
    }

    /* ---- streets: the grid that connects every district to the ring road ------------------ */
    /* flat paint only: hairline plates on the ground plane, dark and semi-transparent, with
     * muted blue-grey markings — the city reads buildings first, streets second */
    var streetRoadMatrices = [];
    var streetDashMatrices = [];
    for (var street of doc.streets || []) {
      var alongX = street.axis === "x";
      var length = street.to - street.from;
      var centerX = alongX ? (street.from + street.to) / 2 : street.at;
      var centerZ = alongX ? street.at : (street.from + street.to) / 2;
      streetRoadMatrices.push(
        placed(centerX, 0.21, centerZ, alongX ? length : street.width, 0.012, alongX ? street.width : length)
      );
      for (var dashAt = 3; dashAt < length - 3; dashAt += 7) {
        var dashT = street.from + dashAt;
        streetDashMatrices.push(
          placed(
            alongX ? dashT : centerX,
            0.218,
            alongX ? centerZ : dashT,
            alongX ? 2.2 : 0.32,
            0.012,
            alongX ? 0.32 : 2.2
          )
        );
      }
    }
    var streetRoad = BABYLON.MeshBuilder.CreateBox("streets", { size: 1 }, scene);
    streetRoad.material = flatPaint("street-asphalt", "#141c25", 0.62);
    streetRoad.isPickable = false;
    streetRoad.alwaysSelectAsActiveMesh = true;
    streetRoad.thinInstanceSetBuffer("matrix", matrixBuffer(streetRoadMatrices), 16, true);

    var streetDash = BABYLON.MeshBuilder.CreateBox("street-lanes", { size: 1 }, scene);
    streetDash.material = flatPaint("street-dash-material", "#33505c", 0.85);
    streetDash.isPickable = false;
    streetDash.alwaysSelectAsActiveMesh = true;
    streetDash.thinInstanceSetBuffer("matrix", matrixBuffer(streetDashMatrices), 16, true);

    /* ---- city blocks: the massing the wide views read (block > building > floors) -------- */
    var blockMatrices = [];
    var blockColors = [];
    var massMatrices = [];
    var massColors = [];
    for (var cityBlock of doc.blocks || []) {
      var blockPlot = cityBlock.plot;
      var blockTint = color(FAMILY_COLORS[cityBlock.family] || FAMILY_COLORS.block, 0.5);
      blockMatrices.push(
        placed(
          blockPlot.x + blockPlot.w / 2,
          0.24,
          blockPlot.z + blockPlot.d / 2,
          blockPlot.w,
          0.12,
          blockPlot.d
        )
      );
      blockColors.push(blockTint.r, blockTint.g, blockTint.b, 1);
      /* the aggregate massing: the block's shaded volume, visible where buildings are dots */
      var massHeight = cityBlock.height * 0.55 + 1.2;
      var massTint = color(FAMILY_COLORS[cityBlock.family] || FAMILY_COLORS.block, 0.82);
      massMatrices.push(
        placed(
          blockPlot.x + blockPlot.w / 2,
          0.3 + massHeight / 2,
          blockPlot.z + blockPlot.d / 2,
          blockPlot.w * 0.82,
          massHeight,
          blockPlot.d * 0.82
        )
      );
      massColors.push(massTint.r, massTint.g, massTint.b, 1);
    }
    var blockBaseMesh = BABYLON.MeshBuilder.CreateBox("block-bases", { size: 1 }, scene);
    blockBaseMesh.material = roleMaterial("block-base-material");
    blockBaseMesh.isPickable = false;
    blockBaseMesh.alwaysSelectAsActiveMesh = true;
    blockBaseMesh.receiveShadows = true;
    blockBaseMesh.thinInstanceSetBuffer("matrix", matrixBuffer(blockMatrices), 16, true);
    blockBaseMesh.thinInstanceSetBuffer("color", blockColors, 4, true);

    var massMesh = BABYLON.MeshBuilder.CreateBox("massing-blocks", { size: 1 }, scene);
    massMesh.material = roleMaterial("massing-blocks-material");
    massMesh.isPickable = false;
    massMesh.alwaysSelectAsActiveMesh = true;
    massMesh.receiveShadows = true;
    massMesh.thinInstanceSetBuffer("matrix", matrixBuffer(massMatrices), 16, true);
    massMesh.thinInstanceSetBuffer("color", massColors, 4, true);

    function makeLoop(points) {
      /* a closed polyline with arc-length parameterization: where a tram is at fraction t */
      var lengths = [];
      var total = 0;
      for (var i = 0; i < points.length; i++) {
        var first = points[i];
        var second = points[(i + 1) % points.length];
        var span = Math.hypot(second.x - first.x, second.z - first.z);
        lengths.push(span);
        total += span;
      }
      return {
        length: total,
        at: function (fraction) {
          var want = (((fraction % 1) + 1) % 1) * total;
          for (var i = 0; i < lengths.length; i++) {
            if (want <= lengths[i]) {
              var first = points[i];
              var second = points[(i + 1) % points.length];
              var t = lengths[i] > 0 ? want / lengths[i] : 0;
              return {
                x: first.x + (second.x - first.x) * t,
                z: first.z + (second.z - first.z) * t,
                heading: Math.atan2(second.x - first.x, second.z - first.z)
              };
            }
            want -= lengths[i];
          }
          return { x: points[0].x, z: points[0].z, heading: 0 };
        }
      };
    }

    function tramMatrices(clock) {
      var matrices = [];
      if (!loop) return matrices;
      for (var unit of trams) {
        var spot = loop.at(unit.phase + clock / TRAM_LAP);
        matrices.push(
          BABYLON.Matrix.Compose(
            new BABYLON.Vector3(2.1, 1.7, 4.0),
            BABYLON.Quaternion.FromEulerAngles(0, spot.heading, 0),
            new BABYLON.Vector3(
              spot.x + Math.cos(spot.heading) * unit.lane,
              1.05,
              spot.z - Math.sin(spot.heading) * unit.lane
            )
          )
        );
      }
      return matrices;
    }

    /* ---- L2 details: name labels and the towers' checksum displays ------------------------ */
    var labelMeshes = [];
    function label(text, position, width, height, accent, priority) {
      if (labelMeshes.length > 140) return null;
      var plane = BABYLON.MeshBuilder.CreatePlane(
        "label-" + labelMeshes.length,
        { width: width, height: height },
        scene
      );
      var texture = new BABYLON.DynamicTexture(
        "label-text-" + labelMeshes.length,
        { width: 256, height: 64 },
        scene,
        true
      );
      var brush = texture.getContext();
      brush.fillStyle = "#0e141c"; /* opaque plate: overlapping signs never ghost through */
      brush.fillRect(0, 0, 256, 64);
      brush.strokeStyle = accent || "rgba(120, 150, 170, 0.6)";
      brush.lineWidth = 3;
      brush.strokeRect(1.5, 1.5, 253, 61);
      brush.font = "bold 26px monospace";
      brush.fillStyle = accent || "#cfe4ef";
      brush.textAlign = "center";
      brush.textBaseline = "middle";
      brush.fillText(text.slice(0, 26), 128, 33, 244);
      texture.update(false);
      var material = new BABYLON.StandardMaterial("label-" + labelMeshes.length + "-material", scene);
      /* the opaque plate lives in the texture, so the texture is the surface *and* the light:
       * put it on diffuse (the dark plate + accent + text is the face) and on emissive at full
       * white (self-lit signage that reads at every LOD and under any rig light). An
       * emissive-only material whose emissiveColor is left at its default black renders the
       * sign black — invisible on the dark arena floor — which is how the signs vanished. */
      material.diffuseTexture = texture;
      material.emissiveTexture = texture;
      material.emissiveColor = new BABYLON.Color3(1, 1, 1);
      material.specularColor = new BABYLON.Color3(0, 0, 0);
      material.backFaceCulling = true; /* the back is the mirrored text — it must never show */
      material.disableLighting = true;
      plane.material = material;
      plane.position = position;
      /* screen-aligned signage: the render loop aims every label at the camera with the
       * camera's own screen axes, so the text is upright and unmirrored at every pitch/yaw
       * (a Y-billboard alone reads backwards from steep angles) */
      plane.billboardMode = BABYLON.Mesh.BILLBOARDMODE_NONE;
      plane.isPickable = false;
      plane.metadata = { halfW: width / 2, halfH: height / 2, priority: priority || 0 };
      labelMeshes.push(plane);
      return plane;
    }

    var districtPlanes = []; /* district signs: name + byte band, above every plot */
    var wayfindPlanes = []; /* street name plates along the main roads (L1 and closer) */
    /* the signage hues separate by voice so the eye sorts them at a glance: district signs in
     * the per-band pastels, street plates in warm sand (the only warm wayfinding hue), object
     * labels in cyan (towers) and gold (monuments), checksum displays in the checksum teal */
    var bandAccent = { header: "#9fb4e8", index: "#8fc4dc", cells: "#9fd8c8" };
    for (var signRegion of doc.regions) {
      var signPlot = signRegion.plot;
      districtPlanes.push(
        label(
          signRegion.id +
            " · 0x" +
            signRegion.start.toString(16).toUpperCase() +
            "-0x" +
            signRegion.end.toString(16).toUpperCase(),
          new BABYLON.Vector3(signPlot.x + signPlot.w / 2, 15, signPlot.z + signPlot.d / 2),
          13,
          1.8,
          bandAccent[signRegion.band] || bandAccent.cells,
          0
        )
      );
    }
    for (var plate of doc.streets || []) {
      if (plate.kind !== "avenue" && plate.kind !== "cross") continue; /* main roads only */
      var plateX = plate.axis === "x";
      wayfindPlanes.push(
        label(
          (plate.kind === "avenue" ? "AVE · " : "CROSS · ") +
            (plateX ? "Z" : "X") +
            Number(plate.at).toFixed(1),
          new BABYLON.Vector3(
            plateX ? (plate.from + plate.to) / 2 : plate.at,
            3.2,
            plateX ? plate.at : (plate.from + plate.to) / 2
          ),
          6.5,
          1.3,
          "#e2c07c", /* warm sand: the wayfinding voice, never mistaken for a district sign */
          0
        )
      );
    }

    var signPlanes = []; /* name signage: shown from L1 up (the city's signs) */
    var detailPlanes = []; /* checksum displays: L2 anatomy detail */
    var important = built.cellList
      .filter(function (cell) {
        return cell.family === "tower" || cell.family === "monument";
      })
      .slice(0, 140);
    for (var named of important) {
      var name = named.path || named.name || named.id;
      if (named.family === "tower") {
        signPlanes.push(
          label(
            name,
            new BABYLON.Vector3(named.plot.x, named.height + 1.9, named.plot.z),
            8,
            1.6,
            "#a2e8ff" /* object-label cyan: distinct from the district band pastels */
          )
        );
        detailPlanes.push(
          label(
            named.checksum ? String(named.checksum) : "checksum —",
            new BABYLON.Vector3(named.plot.x, named.height + 0.9, named.plot.z),
            5.4,
            1.2,
            named.checksum ? "#2fd9c0" : "#5b6b78",
            1
          )
        );
      } else {
        signPlanes.push(
          label(
            name,
            new BABYLON.Vector3(named.plot.x, named.height + 1.3, named.plot.z),
            8,
            1.6,
            "#d8bf8a"
          )
        );
      }
    }

    /* ---- groups for the LOD levels -------------------------------------------------------- */
    function group(name) {
      var node = new BABYLON.TransformNode(name, scene);
      built.groups[name] = node;
      return node;
    }
    var districts = group("districts");
    var buildings = group("buildings");
    var massing = group("massing");
    var cables = group("cables");
    var cablesMore = group("cablesMore");
    var cablesAll = group("cablesAll");
    var streets = group("streets");
    var labels = group("labels");
    var details = group("details");
    var wayfind = group("wayfind");
    for (var tileBand of ["header", "index", "cells"]) tileMeshes[tileBand].parent = districts;
    baseMesh.parent = districts;
    rimMesh.parent = districts;
    streetRoad.parent = streets;
    streetDash.parent = streets;
    blockBaseMesh.parent = districts;
    massMesh.parent = massing;
    if (ringRoad) ringRoad.parent = districts;
    for (var ringMarking of ringMarkings) ringMarking.parent = districts;
    for (var role of Object.keys(roleMeshes)) roleMeshes[role].parent = details;
    volumeMesh.parent = buildings;
    beaconMesh.parent = buildings;
    crownMesh.parent = buildings;
    voidMesh.parent = buildings;
    graveMesh.parent = buildings;
    if (tramMesh) tramMesh.parent = districts;
    if (dashMesh) dashMesh.parent = districts;
    var cableGroups = [cables, cablesMore, cablesAll];
    for (var t = 0; t < tierMeshes.length; t++) {
      for (var tierKind of ["base", "fresh", "stale", "pads"]) {
        tierMeshes[t][tierKind].parent = cableGroups[t];
      }
    }
    /* label() hands back null past its budget: guard the parenting, never crash the build */
    for (var plane of signPlanes) if (plane) plane.parent = labels;
    for (var plane of districtPlanes) if (plane) plane.parent = labels;
    for (var plane of wayfindPlanes) if (plane) plane.parent = wayfind;
    for (var plane of detailPlanes) if (plane) plane.parent = details;
    built.labels = labelMeshes;
    built.signs = signPlanes;
    built.detailLabels = detailPlanes;

    /* selection: UNMISTAKABLE — a bright edge box on the building volume plus a footprint
     * mark painted on the ground under it. Hover stays the weaker cue (thin outline + the
     * pointer); the selection is clearly the stronger one: wider, brighter, and it glows. */
    var selection = new BABYLON.TransformNode("selection", scene);
    var box = BABYLON.MeshBuilder.CreateBox("selection-box", { size: 1 }, scene);
    box.enableEdgesRendering();
    box.edgesColor = new BABYLON.Color4(1, 0.86, 0.55, 1); /* bright, warm, against the cool city */
    box.edgesWidth = 7;
    var boxMaterial = new BABYLON.PBRMaterial("selection-box-material", scene);
    boxMaterial.albedoColor = new BABYLON.Color3(1, 0.82, 0.35);
    boxMaterial.alpha = 0.12;
    box.material = boxMaterial;
    box.parent = selection;

    /* the footprint mark: flat paint on the ground under the picked building (the streets'
     * law — flat paint, never a curb, wall or shadow): a soft warm glow, a crisp frame and
     * heavy corner brackets, so the pick reads even in the wide tactical view */
    var footprintTexture = new BABYLON.DynamicTexture(
      "selection-footprint-texture",
      { width: 256, height: 256 },
      scene,
      true
    );
    var foot = footprintTexture.getContext();
    foot.clearRect(0, 0, 256, 256);
    var spill = foot.createRadialGradient(128, 128, 16, 128, 128, 126);
    spill.addColorStop(0, "rgba(255, 180, 84, 0.5)");
    spill.addColorStop(0.55, "rgba(255, 180, 84, 0.18)");
    spill.addColorStop(1, "rgba(255, 180, 84, 0)");
    foot.fillStyle = spill;
    foot.fillRect(0, 0, 256, 256);
    foot.strokeStyle = "#ffd27a";
    foot.lineWidth = 7;
    foot.strokeRect(24, 24, 208, 208);
    foot.lineWidth = 17; /* the corner brackets double down on the frame */
    foot.beginPath();
    for (var bracket of [
      [24, 74, 24, 24, 74, 24],
      [182, 24, 232, 24, 232, 74],
      [232, 182, 232, 232, 182, 232],
      [74, 232, 24, 232, 24, 182]
    ]) {
      foot.moveTo(bracket[0], bracket[1]);
      foot.lineTo(bracket[2], bracket[3]);
      foot.lineTo(bracket[4], bracket[5]);
    }
    foot.stroke();
    footprintTexture.hasAlpha = true;
    footprintTexture.update(false);
    var footprintMaterial = new BABYLON.StandardMaterial("selection-footprint-material", scene);
    footprintMaterial.diffuseTexture = footprintTexture;
    footprintMaterial.emissiveTexture = footprintTexture;
    footprintMaterial.emissiveColor = new BABYLON.Color3(1, 1, 1);
    footprintMaterial.useAlphaFromDiffuseTexture = true;
    footprintMaterial.disableLighting = true;
    footprintMaterial.specularColor = new BABYLON.Color3(0, 0, 0);
    footprintMaterial.backFaceCulling = false;
    var footprint = BABYLON.MeshBuilder.CreateGround("selection-footprint", { width: 1, height: 1 }, scene);
    footprint.material = footprintMaterial;
    footprint.position.y = 0.38; /* flat over the street paint: still just paint on the ground */
    footprint.isPickable = false;
    footprint.parent = selection;
    glow.addIncludedOnlyMesh(footprint);
    selection.setEnabled(false);
    built.selection = selection;
    built.footprint = footprint;
    built.box = box;

    /* hover: the quiet outline that pairs with the pointer cursor (click then selects) */
    var hover = new BABYLON.TransformNode("hover", scene);
    var hoverBox = BABYLON.MeshBuilder.CreateBox("hover-box", { size: 1 }, scene);
    hoverBox.enableEdgesRendering();
    hoverBox.edgesColor = new BABYLON.Color4(0.45, 0.88, 0.8, 1);
    hoverBox.edgesWidth = 3;
    var hoverMaterial = new BABYLON.PBRMaterial("hover-box-material", scene);
    hoverMaterial.albedoColor = new BABYLON.Color3(0.45, 0.88, 0.8);
    hoverMaterial.alpha = 0.05;
    hoverBox.material = hoverMaterial;
    hoverBox.parent = hover;
    hover.setEnabled(false);
    built.hover = hover;
    built.hoverBox = hoverBox;

    return built;
  }

  /* ---- the api ------------------------------------------------------------------------------ */
  function create(canvas, doc, options) {
    var BABYLON = global.BABYLON;
    options = options || {};
    if (!doc || doc.schema !== SCHEMA) {
      return Promise.reject(new Error("scene document is not " + SCHEMA));
    }

    function makeEngine() {
      /* WebGL is the engine the screenshot pins are taken on: one deterministic raster everywhere.
       * WebGPU is opt-in ("engine": "webgpu" in the scene config) because a host that advertises
       * navigator.gpu does not promise a working frame loop: some webviews list the API, init a
       * device, and never present a frame — the scene then freezes at its first render. */
      if (options.engine === "webgpu" && global.navigator && global.navigator.gpu && BABYLON.WebGPUEngine) {
        var gpu = new BABYLON.WebGPUEngine(canvas);
        return gpu
          .initAsync()
          .then(function () {
            return gpu;
          })
          .catch(function () {
            return new BABYLON.Engine(canvas, true, { stencil: true }, true);
          });
      }
      return Promise.resolve(new BABYLON.Engine(canvas, true, { stencil: true }, true));
    }

    return makeEngine().then(function (engine) {
      var scene = new BABYLON.Scene(engine);
      scene.clearColor = new BABYLON.Color4(0.03, 0.04, 0.055, 1);
      scene.imageProcessingConfiguration.contrast = 1.12;
      scene.imageProcessingConfiguration.exposure = 1.32;
      scene.fogMode = BABYLON.Scene.FOGMODE_EXP2; /* the far ground fades into the sky */
      scene.fogDensity = 0.0022;
      scene.fogColor = new BABYLON.Color3(0.03, 0.04, 0.055);

      var glow = new BABYLON.GlowLayer("glow", scene, { mainTextureFixedSize: 512, blurKernelSize: 28 });
      glow.intensity = 0.45;

      var fill = new BABYLON.HemisphericLight("fill", new BABYLON.Vector3(0.35, 1, -0.25), scene);
      fill.intensity = 0.9;
      fill.groundColor = new BABYLON.Color3(0.07, 0.09, 0.12);
      var key = new BABYLON.DirectionalLight("key", new BABYLON.Vector3(-0.55, -1, 0.4), scene);
      key.position = new BABYLON.Vector3(160, 260, -120);
      key.intensity = 1.5;
      var shadows = new BABYLON.ShadowGenerator(2048, key);
      shadows.useBlurExponentialShadowMap = true;
      shadows.blurKernel = 24;
      shadows.setDarkness(0.5); /* real side shading and base shadows: the city must have depth */

      var world = buildWorld(scene, doc, glow);
      /* the pick grid: the cursor's cell without walking every building per pointer move */
      var pickGrid = new Map();
      var PICK_BUCKET = 6;
      for (var pickCellOfWorld of world.cellList) {
        var pickKey =
          Math.floor(pickCellOfWorld.plot.x / PICK_BUCKET) +
          "," +
          Math.floor(pickCellOfWorld.plot.z / PICK_BUCKET);
        var pickBucket = pickGrid.get(pickKey);
        if (!pickBucket) pickGrid.set(pickKey, (pickBucket = []));
        pickBucket.push(pickCellOfWorld);
      }
      for (var mesh of scene.meshes) {
        /* every volume casts: buildings, caps, and the L0 block massing */
        if (
          mesh.name.indexOf("floors-") === 0 ||
          mesh.name === "beacons" ||
          mesh.name === "crowns" ||
          mesh.name === "massing-blocks" ||
          mesh.name === "building-volumes"
        ) {
          shadows.addShadowCaster(mesh);
        }
        if (mesh.name.indexOf("tiles-") === 0 || mesh.name === "plot-bases" || mesh.name === "plot-rims") {
          mesh.receiveShadows = true;
        }
      }

      var camera = new BABYLON.UniversalCamera("rig", new BABYLON.Vector3(0, 170, 0), scene);
      camera.inputs.clear(); /* the rig owns the controls */
      camera.minZ = 0.5;
      camera.maxZ = 6000;
      var rig = new Rig(camera, canvas);
      rig.state.x = rig.goal.x = world.centroid.x;
      rig.state.z = rig.goal.z = world.centroid.z;
      rig.apply();

      var listeners = [];
      var selected = null;
      var motion = options.motion !== false;
      var clock = 0;

      function select(cell) {
        selected = cell;
        world.wire(cell); /* the cell's own cables light up on their sheets */
        if (!cell) {
          world.selection.setEnabled(false);
        } else {
          world.selection.setEnabled(true);
          world.selection.position.x = cell.plot.x;
          world.selection.position.z = cell.plot.z;
          world.footprint.scaling.x = world.footprint.scaling.z = Math.max(1.8, cell.footprint + 1.5);
          world.box.scaling.x = world.box.scaling.z = Math.max(1, cell.footprint + 0.6);
          world.box.scaling.y = cell.height + 0.4;
          world.box.position.y = cell.height / 2;
        }
        var payload = cell
          ? {
              cell: cell,
              edges: world.edges.filter(function (edge) {
                return edge.from === cell.id || edge.to === cell.id;
              }),
              region: (doc.regions || []).find(function (region) {
                return region.id === cell.region;
              })
            }
          : null;
        if (global.RXFWorldHud) {
          global.RXFWorldHud.status(payload);
          global.RXFWorldHud.sheet(payload); /* the datasheet pane: opens with the selection */
        }
        for (var listener of listeners) listener(payload);
      }

      var hoverCell = null;
      function setHover(cell) {
        /* hover: the quiet outline + pointer cursor that say "this one is clickable" */
        if (cell === hoverCell) return;
        hoverCell = cell;
        if (!cell) {
          world.hover.setEnabled(false);
          canvas.style.cursor = "";
          return;
        }
        world.hover.setEnabled(true);
        world.hover.position.x = cell.plot.x;
        world.hover.position.z = cell.plot.z;
        world.hoverBox.scaling.x = world.hoverBox.scaling.z = Math.max(1, cell.footprint + 1);
        world.hoverBox.scaling.y = cell.height + 0.3;
        world.hoverBox.position.y = cell.height / 2;
        canvas.style.cursor = "pointer";
      }

      function pickCell(clientX, clientY) {
        /* the ground point under the cursor, then the nearest building: one shared pick for
         * click and hover so both always agree on what is under the pointer */
        var picked = scene.createPickingRay(
          clientX - canvas.getBoundingClientRect().left,
          clientY - canvas.getBoundingClientRect().top,
          BABYLON.Matrix.Identity(),
          camera
        );
        /* vendored Babylon 9.30 hands back the Ray itself; be kind to either shape */
        var ray = picked.ray || picked;
        if (ray.direction.y === 0) return null;
        var ground = -ray.origin.y / ray.direction.y;
        if (ground <= 0) return null;
        var point = ray.origin.add(ray.direction.scale(ground));
        var best = 3.2;
        var hitDistance = Infinity;
        var hit = null;
        var gx = Math.floor(point.x / PICK_BUCKET);
        var gz = Math.floor(point.z / PICK_BUCKET);
        for (var bx = gx - 1; bx <= gx + 1; bx++) {
          for (var bz = gz - 1; bz <= gz + 1; bz++) {
            for (var cell of pickGrid.get(bx + "," + bz) || []) {
              var distance = Math.hypot(point.x - cell.plot.x, point.z - cell.plot.z) - cell.footprint * 0.5;
              if (distance < best && distance < hitDistance) {
                hitDistance = distance;
                hit = cell;
              }
            }
          }
        }
        return hit;
      }

      function targetOf(pose) {
        /* "world-center" is the content centroid: the city, never a dead corner */
        var target = { x: world.centroid.x, z: world.centroid.z };
        if (pose.target === "first-tower") {
          var tower = world.cellList.find(function (cell) {
            return cell.family === "tower";
          });
          if (tower) target = tower.plot;
        } else if (pose.target === "selection" && selected) {
          target = selected.plot;
        }
        return target;
      }

      var api = {
        poses: CAMERA_POSES,
        lods: LOD_LEVELS,
        scene: scene,
        engine: engine,
        onSelect: function (fn) {
          listeners.push(fn);
        },
        highlight: function (cellId) {
          select(world.cellsById[cellId] || null);
        },
        setPose: function (name, instant) {
          var pose = CAMERA_POSES.find(function (candidate) {
            return candidate.name === name;
          });
          if (!pose) return;
          var target = targetOf(pose);
          var altitude =
            pose.framing === "world"
              ? rig.fitRect(world.content, target, pose.pitch, pose.yaw, pose.mode)
              : pose.altitude;
          rig.go(
            {
              x: target.x,
              z: target.z,
              altitude: altitude,
              pitch: pose.pitch,
              yaw: pose.yaw,
              mode: pose.mode
            },
            instant !== false
          );
        },
        setView: function (view) {
          /* a raw camera view: the deterministic tool for proof captures at extreme angles */
          rig.go(
            {
              x: view.x === undefined ? world.centroid.x : view.x,
              z: view.z === undefined ? world.centroid.z : view.z,
              altitude: view.altitude === undefined ? 60 : view.altitude,
              pitch: view.pitch === undefined ? 55 : view.pitch,
              yaw: view.yaw === undefined ? 40 : view.yaw,
              mode: view.mode || "perspective"
            },
            true
          );
        },
        setMotion: function (on) {
          motion = on !== false;
        },
        seams: {
          flow: {
            setValueProvider: function () {},
            pulse: function () {}
          },
          animate: {
            setRunAnimator: function () {},
            tick: function () {}
          },
          edit: {
            enter: function () {},
            leave: function () {}
          },
          missions: {
            set: function () {},
            progress: function () {
              return null;
            }
          }
        },
        dispose: function () {
          scene.dispose();
          engine.stopRenderLoop();
          engine.dispose();
        }
      };
      api.select = select;

      /* the datasheet pane's close button dismisses exactly like ESC and empty-ground clicks */
      var closeButton = global.document.querySelector("[data-datasheet-close]");
      if (closeButton) {
        closeButton.addEventListener("click", function () {
          select(null);
        });
      }

      /* click-to-select: the ground plane, then the nearest building */
      var pointer = { x: 0, y: 0, moved: 0, button: -1 };
      canvas.addEventListener("pointerdown", function (event) {
        pointer.x = event.clientX;
        pointer.y = event.clientY;
        pointer.moved = 0;
        pointer.button = event.button;
      });
      canvas.addEventListener("pointermove", function (event) {
        var dx = event.clientX - pointer.x;
        var dy = event.clientY - pointer.y;
        pointer.moved += Math.abs(dx) + Math.abs(dy);
        var basis = rig.basis();
        if (pointer.button === 0 && (event.buttons & 1)) {
          /* drag pans on the camera plane: the world follows the cursor */
          var scale = rig.state.altitude * 0.0016;
          rig.goal.x -= (dx * basis.right.x + dy * basis.up.x) * scale;
          rig.goal.z -= (dx * basis.right.z + dy * basis.up.z) * scale;
          rig.state.x = rig.goal.x;
          rig.state.z = rig.goal.z;
          rig.apply();
          setHover(null); /* no hover highlight while the hand is dragging the world */
        } else if (pointer.button === 2 && (event.buttons & 2)) {
          rig.goal.yaw -= dx * 0.25;
          rig.goal.pitch = clamp(rig.goal.pitch + dy * 0.2, 22, 90);
          rig.state.yaw = rig.goal.yaw;
          rig.state.pitch = rig.goal.pitch;
          rig.apply();
        } else {
          setHover(pickCell(event.clientX, event.clientY));
        }
        pointer.x = event.clientX;
        pointer.y = event.clientY;
      });
      canvas.addEventListener("pointerup", function (event) {
        if (pointer.button === 0 && pointer.moved < 6) {
          /* click selects; the click on empty ground deselects and closes the datasheet */
          select(pickCell(event.clientX, event.clientY));
        }
        pointer.button = -1;
      });
      canvas.addEventListener(
        "wheel",
        function (event) {
          event.preventDefault();
          rig.goal.altitude = clamp(rig.goal.altitude * Math.exp(event.deltaY * 0.0012), 12, 600);
          rig.state.altitude = rig.goal.altitude;
          rig.apply();
        },
        { passive: false }
      );
      canvas.addEventListener("contextmenu", function (event) {
        event.preventDefault();
      });

      global.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          select(null); /* ESC dismisses the datasheet and the selection */
          return;
        }
        if (event.key >= "1" && event.key <= String(CAMERA_POSES.length)) {
          api.setPose(CAMERA_POSES[Number(event.key) - 1].name);
          return;
        }
        if (event.key === "o" || event.key === "O") {
          rig.goal.mode = rig.goal.mode === "ortho" ? "perspective" : "ortho";
          return;
        }
        /* WASD is camera-relative: W = view direction, A = camera left (screen up at any yaw) */
        var basis = rig.basis();
        var move = { w: basis.up, s: { x: -basis.up.x, z: -basis.up.z }, a: { x: -basis.right.x, z: -basis.right.z }, d: basis.right }[
          event.key.toLowerCase()
        ];
        if (move) {
          var pace = rig.state.altitude * 0.035;
          rig.goal.x += move.x * pace;
          rig.goal.z += move.z * pace;
          rig.state.x = rig.goal.x;
          rig.state.z = rig.goal.z;
          rig.apply(); /* input answers immediately; the loop only eases the camera between keys */
        }
      });

      var lastLevel = "";
      var lastLabelScale = 0;
      engine.runRenderLoop(function () {
        var dt = Math.min(0.1, engine.getDeltaTime() / 1000);
        rig.step(dt);
        /* the fog is camera-relative: it fades the far ground at street level, but thins as the
         * camera pulls up. EXP2 fog works on camera distance alone — at a zoomed-out altitude
         * every pixel sits past the fog wall and the whole city drowns in the (near black) fog
         * color, which reads as "the scene goes dark when I scroll out". */
        scene.fogDensity = 0.0022 * Math.min(1, 70 / Math.max(70, rig.state.altitude));
        /* labels are screen-aligned signage: they take the camera's own rotation, so their
         * text is upright and unmirrored from every pitch and yaw the rig can reach. The
         * 180° turn about the plane's own Y points the plane's readable face (CreatePlane
         * draws its texture for the +Z face) at the camera — the camera's own +Z is its view
         * direction, so a bare camera rotation always shows the mirrored back face. */
        var cameraRotation = camera.getWorldMatrix().clone();
        cameraRotation.setTranslationFromFloats(0, 0, 0);
        var labelFacing = global.BABYLON.Quaternion.FromRotationMatrix(cameraRotation).multiply(
          global.BABYLON.Quaternion.RotationAxis(global.BABYLON.Axis.Y, Math.PI)
        );
        for (var labelPlane of world.labels) labelPlane.rotationQuaternion = labelFacing;
        /* labels hold their screen size through the zoom: one scale for the whole LOD range */
        var labelScale = clamp(rig.state.altitude / 46, 0.42, 1.5);
        if (Math.abs(labelScale - lastLabelScale) > 0.01) {
          lastLabelScale = labelScale;
          for (var labelPlane of world.labels) {
            labelPlane.scaling.x = labelPlane.scaling.y = labelScale;
          }
        }
        /* overlapping signs are culled nearest-first (name signs before checksum displays):
         * every label that draws must read cleanly, never over the ghost of another */
        if (motion) {
          clock += dt; /* the scene's one clock: the trams and the selection's gentle breathe */
          if (tramsMoving()) updateTrams();
        }
        if (world.selection.isEnabled()) {
          /* the footprint mark breathes gently (no strobe); motion-off freezes it at phase 0,
           * so the deterministic screenshot frames stay byte-stable */
          world.footprint.material.alpha = 0.86 + 0.12 * Math.sin(clock * 1.8);
        }
        var level = rig.lod();
        world.groups.buildings.setEnabled(level !== LOD_LEVELS[0]);
        world.groups.massing.setEnabled(level === LOD_LEVELS[0]);
        /* the cable detail limit (the depth dimension carries the rest): the skeleton — both
         * ends named — from L1, the backbone — one end named — from L2, every cable at L3.
         * The flight sheets make each tier readable; the limit keeps the wide views alive. */
        world.groups.cables.setEnabled(level !== LOD_LEVELS[0]);
        world.groups.cablesMore.setEnabled(level === LOD_LEVELS[2] || level === LOD_LEVELS[3]);
        world.groups.cablesAll.setEnabled(level === LOD_LEVELS[3]);
        world.groups.streets.setEnabled(level !== LOD_LEVELS[0]);
        /* name signage is the city's identity: it reads at every level and every pitch/yaw
         * (the billboard keeps it upright), so the L0 massing overview carries the names too —
         * the round-3 hero shows shaded massing *and* the floating signs together */
        world.groups.labels.setEnabled(true);
        world.groups.wayfind.setEnabled(level !== LOD_LEVELS[0]);
        world.groups.details.setEnabled(level === LOD_LEVELS[2] || level === LOD_LEVELS[3]);
        updateLabels(labelScale);
        if (level.name !== lastLevel) {
          lastLevel = level.name;
          if (global.RXFWorldHud) global.RXFWorldHud.lod(level);
        }
        scene.render();
        if (global.RXFWorldHud && global.RXFWorldHud.minimap && global.RXFWorldHud.minimap.view) {
          global.RXFWorldHud.minimap.view(rig.view(), selected);
        }
      });

      function updateLabels(scale) {
        /* projection-based overlap culling: screen rectangles in priority then distance order */
        var renderWidth = engine.getRenderWidth() || 1;
        var renderHeight = engine.getRenderHeight() || 1;
        var transform = scene.getTransformMatrix();
        var viewport = camera.viewport.toGlobal(renderWidth, renderHeight);
        var ordered = world.labels.slice().sort(function (a, b) {
          if (a.metadata.priority !== b.metadata.priority) return a.metadata.priority - b.metadata.priority;
          var da = a.position.subtract(camera.position).lengthSquared();
          var db = b.position.subtract(camera.position).lengthSquared();
          return da - db;
        });
        var taken = [];
        for (var plane of ordered) {
          if (!world.groups.labels.isEnabled() && !world.groups.details.isEnabled()) {
            plane.setEnabled(false);
            continue;
          }
          if (plane.parent && !plane.parent.isEnabled()) {
            plane.setEnabled(false);
            continue;
          }
          var screen = global.BABYLON.Vector3.Project(
            plane.position,
            global.BABYLON.Matrix.Identity(),
            transform,
            viewport
          );
          var distance = plane.position.subtract(camera.position).length() || 1;
          var pixelPerUnit = renderHeight / (2 * distance * Math.tan(FOV / 2));
          var halfW = plane.metadata.halfW * scale * pixelPerUnit;
          var halfH = plane.metadata.halfH * scale * pixelPerUnit;
          var rect = [screen.x - halfW, screen.y - halfH, screen.x + halfW, screen.y + halfH];
          var hidden = screen.z < 0 || screen.z > 1;
          if (!hidden) {
            for (var other of taken) {
              if (rect[0] < other[2] && rect[2] > other[0] && rect[1] < other[3] && rect[3] > other[1]) {
                hidden = true;
                break;
              }
            }
          }
          plane.setEnabled(!hidden);
          if (!hidden) taken.push(rect);
        }
      }

      function tramsMoving() {
        return Boolean(world.tramMesh && world.trams && world.trams.length);
      }      function updateTrams() {
        /* the tram units ride the ring road: one lap per TRAM_LAP seconds, formation intact */
        world.tramMesh.thinInstanceSetBuffer(
          "matrix",
          matrixBuffer(world.tramMatrices(clock)),
          16,
          true
        );
      }

      global.addEventListener("resize", function () {
        engine.resize();
      });

      api.setPose(options.pose || "overview", true);
      if (global.RXFWorldHud) {
        global.RXFWorldHud.metrics(doc.metrics || {});
        global.RXFWorldHud.counts(doc.counts || {});
        global.RXFWorldHud.minimap(doc, world.bounds, function (x, z) {
          rig.go({ x: x, z: z }, false);
        });
      }
      return api;
    });
  }

  /* ---- boot: config -> scene JSON -> create ------------------------------------------------- */
  function loadScene(config) {
    function fail() {
      return fetch(config.fallbackUrl).then(function (response) {
        if (!response.ok) throw new Error("no scene at " + config.sceneUrl + " or " + config.fallbackUrl);
        return response.json();
      });
    }
    return fetch(config.sceneUrl)
      .then(function (response) {
        return response.ok ? response.json() : fail();
      })
      .catch(fail);
  }

  function boot() {
    var canvas = document.getElementById("rxf-world-canvas");
    var configNode = document.getElementById("rxf-world-config");
    if (!canvas || !configNode) return;
    var config = JSON.parse(configNode.textContent);
    loadScene(config)
      .then(function (doc) {
        return create(canvas, doc, config);
      })
      .then(function (api) {
        global.RXFWorld.api = api;
      })
      .catch(function (error) {
        if (global.RXFWorldHud) global.RXFWorldHud.error(error);
        throw error;
      });
  }

  global.RXFWorld = {
    SCHEMA: SCHEMA,
    CAMERA_POSES: CAMERA_POSES,
    LOD_LEVELS: LOD_LEVELS,
    FAMILY_COLORS: FAMILY_COLORS,
    ROLE_COLORS: ROLE_COLORS,
    create: create,
    boot: boot
  };

  if (global.document && global.document.readyState !== "loading") boot();
  else if (global.document) global.document.addEventListener("DOMContentLoaded", boot);
})(typeof window !== "undefined" ? window : globalThis);

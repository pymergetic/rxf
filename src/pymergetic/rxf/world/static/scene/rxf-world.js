/* The 3D world of the RXF runtime: one Babylon.js scene, generated purely from the scene JSON
 * ("rxf-world-1" — the schema is documented in src/pymergetic/rxf/world/export.py).
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

  var SCHEMA = "rxf-world-1";
  var FOV = 0.9;
  var TRAM_LAP = 90; /* seconds for one lap of the ring road */

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
    { name: "fly-ne", mode: "perspective", pitch: 52, yaw: 45, altitude: 110, target: "world-center" },
    { name: "inspect", mode: "perspective", pitch: 68, yaw: 20, altitude: 26, target: "selection" }
  ];

  /* ---- LOD: continuous zoom through the levels --------------------------------------------- */
  var LOD_LEVELS = [
    { name: "L0 strategic", above: 130, shows: "districts + aggregate massing" },
    { name: "L1 operational", above: 55, shows: "buildings + ground traces" },
    { name: "L2 tactical", above: 12, shows: "cell anatomy (block view, checksum displays)" },
    { name: "L3 byte view", above: -1, shows: "later stage (editor)" }
  ];

  /* ---- the look: one palette for buildings and block-view floors ---------------------------- */
  var FAMILY_COLORS = {
    tower: "#6d9fd6",
    monument: "#c2a163",
    block: "#6b7783",
    utility: "#4c5a66",
    rubble: "#453b33"
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
  var TILE_TINT = { header: "#33455a", index: "#385063", cells: "#2e4254" };

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

  function matrixBuffer(matrices) {
    /* thin instances take a flat Float32Array of 16-float matrices, not Matrix objects */
    var data = new Float32Array(matrices.length * 16);
    for (var i = 0; i < matrices.length; i++) matrices[i].copyToArray(data, i * 16);
    return data;
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

  Rig.prototype.fitRect = function (rect, pitch, yaw) {
    /* the altitude at which the ground rectangle `rect` (center x/z, size w/d) fills the frame:
     * its corners projected on the screen axes at this yaw, depth compressed by sin(pitch),
     * width and height fitted together. Deterministic per document and pose. */
    var half = Math.tan(this.fov / 2);
    var yawRad = (yaw * Math.PI) / 180;
    var halfW = rect.w / 2;
    var halfD = rect.d / 2;
    var hr = Math.abs(halfW * Math.cos(yawRad)) + Math.abs(halfD * Math.sin(yawRad));
    var hu = Math.abs(halfW * Math.sin(yawRad)) + Math.abs(halfD * Math.cos(yawRad));
    var slant = Math.max(0.4, Math.sin((pitch * Math.PI) / 180));
    var need = Math.max(hu * slant, hr / Math.max(0.5, this.aspect())) * 1.06;
    return clamp(need / half, 24, 600);
  };

  Rig.prototype.lod = function () {
    for (var level of LOD_LEVELS) {
      if (this.state.altitude > level.above || level.above < 0) return level;
    }
    return LOD_LEVELS[LOD_LEVELS.length - 1];
  };

  Rig.prototype.view = function () {
    /* the ground rectangle the camera looks at (for the minimap viewport) */
    var half = this.state.altitude * Math.tan(this.fov / 2);
    var slant = Math.max(0.45, Math.sin((this.state.pitch * Math.PI) / 180));
    var spanZ = (2 * half) / slant;
    return {
      x: this.state.x,
      z: this.state.z,
      w: spanZ * this.aspect(),
      h: spanZ
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

    /* the world bounds: what the whole-arena poses frame (the plots and the ring road) */
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

    /* the outer ground: dark, faintly gridded, the arena's floor */
    var groundMaterial = pbr("ground", "#0f151c", 0.05, 1.0);
    var grid = new BABYLON.DynamicTexture("ground-grid", { width: 1024, height: 1024 }, scene, true);
    var brush = grid.getContext();
    brush.fillStyle = "#0f151c";
    brush.fillRect(0, 0, 1024, 1024);
    for (var step = 0; step <= 1024; step += 64) {
      brush.strokeStyle = step % 256 === 0 ? "#182029" : "#131a22";
      brush.lineWidth = 1;
      brush.beginPath();
      brush.moveTo(step, 0);
      brush.lineTo(step, 1024);
      brush.moveTo(0, step);
      brush.lineTo(1024, step);
      brush.stroke();
    }
    grid.update(false);
    grid.wrapU = BABYLON.Texture.WRAP_ADDRESSMODE;
    grid.wrapV = BABYLON.Texture.WRAP_ADDRESSMODE;
    groundMaterial.albedoTexture = grid;

    var ground = BABYLON.MeshBuilder.CreateGround("ground", { width: 4000, height: 4000 }, scene);
    ground.material = groundMaterial;
    ground.receiveShadows = true;
    ground.isPickable = false;

    /* the arena plinth: the platform the world stands on, inside the ring road — the seam
     * between the arena and the void beyond it */
    var plinth = BABYLON.MeshBuilder.CreateBox("arena-plinth", { size: 1 }, scene);
    plinth.material = pbr("arena-plinth-material", "#141c25", 0.06, 0.95);
    plinth.isPickable = false;
    plinth.position = new BABYLON.Vector3(built.bounds.x, 0.02, built.bounds.z);
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
    baseMesh.material = pbr("plot-base", "#222c38", 0.1, 0.9);
    baseMesh.isPickable = false;
    baseMesh.alwaysSelectAsActiveMesh = true;
    baseMesh.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, true);
    var baseMatrices = [];

    for (var region of doc.regions) {
      var plot = region.plot;
      var raster = region.tiles;
      var bandName = tileMatrices[region.band] ? region.band : "cells";
      baseMatrices.push(
        BABYLON.Matrix.Translation(plot.x + plot.w / 2, 0.05, plot.z + plot.d / 2).multiply(
          BABYLON.Matrix.Scaling(plot.w, 0.1, plot.d)
        )
      );
      /* every tile of the district's raster: a floor plate with grid lines in the gaps */
      var inset = 0.12;
      for (var row = 0; row < raster.rows; row++) {
        for (var col = 0; col < raster.cols; col++) {
          var tileX = plot.x + (col + 0.5) * raster.step_x;
          var tileZ = plot.z + (row + 0.5) * raster.step_z;
          tileMatrices[bandName].push(
            BABYLON.Matrix.Translation(tileX, 0.13, tileZ).multiply(
              BABYLON.Matrix.Scaling(raster.step_x - inset, 0.14, raster.step_z - inset)
            )
          );
          var lit = region.empty ? 0.72 : 1;
          var vary = 0.94 + 0.12 * hash(col * 31 + row * 17 + Math.round(plot.x * 7));
          var tint = color(TILE_TINT[bandName], lit * vary);
          tileColors[bandName].push(tint.r, tint.g, tint.b, 1);
        }
      }
    }
    for (var bandKey of ["header", "index", "cells"]) {
      tileMeshes[bandKey].thinInstanceSetBuffer("matrix", matrixBuffer(tileMatrices[bandKey]), 16, true);
      tileMeshes[bandKey].thinInstanceSetBuffer("color", tileColors[bandKey], 4, true);
    }
    baseMesh.thinInstanceSetBuffer("matrix", matrixBuffer(baseMatrices), 16, true);

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

    for (var cell of doc.cells) {
      built.cellsById[cell.id] = cell;
      built.cellList.push(cell);
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
          BABYLON.Matrix.Translation(cell.plot.x, y + floorHeight / 2, cell.plot.z).multiply(
            BABYLON.Matrix.Scaling(width, floorHeight, width)
          )
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
          BABYLON.Matrix.Translation(capped.plot.x, capped.height + 0.45, capped.plot.z).multiply(
            BABYLON.Matrix.Scaling(0.34, 0.9, 0.34)
          )
        );
      } else if (capped.family === "monument") {
        crownMatrices.push(
          BABYLON.Matrix.Translation(capped.plot.x, capped.height + 0.12, capped.plot.z).multiply(
            BABYLON.Matrix.Scaling(capped.footprint * 0.92, 0.24, capped.footprint * 0.92)
          )
        );
      }
    }
    beaconMesh.thinInstanceSetBuffer("matrix", matrixBuffer(beaconMatrices), 16, true);
    crownMesh.thinInstanceSetBuffer("matrix", matrixBuffer(crownMatrices), 16, true);

    /* ---- void tiles: poison pits --------------------------------------------------------- */
    var voidMaterial = pbr("void", "#160d10", 0.1, 0.98);
    voidMaterial.emissiveColor = new BABYLON.Color3(0.22, 0.02, 0.03);
    var voidMesh = BABYLON.MeshBuilder.CreateBox("voids", { size: 1 }, scene);
    voidMesh.material = voidMaterial;
    voidMesh.isPickable = false;
    voidMesh.alwaysSelectAsActiveMesh = true;
    voidMesh.thinInstanceSetBuffer("matrix", matrixBuffer([]), 16, true);
    var voidMatrices = [];
    for (var tile of doc.voids) {
      voidMatrices.push(
        BABYLON.Matrix.Translation(tile.plot.x, 0.16, tile.plot.z).multiply(
          BABYLON.Matrix.Scaling(tile.footprint, 0.22, tile.footprint)
        )
      );
    }
    voidMesh.thinInstanceSetBuffer("matrix", matrixBuffer(voidMatrices), 16, true);

    /* ---- cables: ground traces on the tile grid (circuit board, not cable car) ------------ */
    var gridStep = (doc.grid && doc.grid.step) || 3.4;
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

    var traceBase = { matrices: [] };
    var traceCore = { fresh: [], stale: [] };
    function traceSegments(points, height, width, core, fresh) {
      for (var i = 0; i < points.length - 1; i++) {
        var a = points[i];
        var b = points[i + 1];
        var length = Math.hypot(b.x - a.x, b.z - a.z);
        if (length < 0.01) continue;
        var angle = Math.atan2(b.x - a.x, b.z - a.z);
        var matrix = BABYLON.Matrix.Compose(
          new BABYLON.Vector3(width, height, length),
          BABYLON.Quaternion.FromEulerAngles(0, angle, 0),
          new BABYLON.Vector3((a.x + b.x) / 2, height / 2 + 0.16, (a.z + b.z) / 2)
        );
        if (core) (fresh ? traceCore.fresh : traceCore.stale).push(matrix);
        else traceBase.matrices.push(matrix);
      }
    }

    var graveMatrices = [];
    for (var edge of doc.edges) {
      var source = built.cellsById[edge.from];
      if (!source) continue;
      var target = edge.to ? built.cellsById[edge.to] : null;
      var end;
      if (target) {
        end = { x: target.plot.x, z: target.plot.z };
      } else {
        /* the grave of a stale handle: a deterministic stub beside its source */
        var graveAngle = hash(edge.to_handle) * 2 * Math.PI;
        end = {
          x: source.plot.x + Math.cos(graveAngle) * 3.4,
          z: source.plot.z + Math.sin(graveAngle) * 3.4
        };
        graveMatrices.push(
          BABYLON.Matrix.Translation(end.x, 0.3, end.z).multiply(BABYLON.Matrix.Scaling(0.8, 0.6, 0.8))
        );
      }
      var points = route({ x: source.plot.x, z: source.plot.z }, end);
      var lift = 0.05 + (edge.index === null ? 0 : edge.index % 3) * 0.05; /* crossings step over */
      traceSegments(points, lift, 0.75, false, edge.fresh);
      traceSegments(points, lift + 0.06, 0.24, true, edge.fresh);
    }
    var baseTraces = BABYLON.MeshBuilder.CreateBox("traces-base", { size: 1 }, scene);
    baseTraces.material = pbr("traces-base-material", "#1a2a33", 0.3, 0.7);
    baseTraces.material.emissiveColor = new BABYLON.Color3(0.04, 0.09, 0.11);
    baseTraces.isPickable = false;
    baseTraces.alwaysSelectAsActiveMesh = true;
    baseTraces.thinInstanceSetBuffer("matrix", matrixBuffer(traceBase.matrices), 16, true);

    var freshTraces = BABYLON.MeshBuilder.CreateBox("traces-fresh", { size: 1 }, scene);
    freshTraces.material = roleMaterial("traces-fresh-material", CABLE_FRESH, 0.85);
    freshTraces.isPickable = false;
    freshTraces.alwaysSelectAsActiveMesh = true;
    freshTraces.thinInstanceSetBuffer("matrix", matrixBuffer(traceCore.fresh), 16, true);

    var staleTraces = BABYLON.MeshBuilder.CreateBox("traces-stale", { size: 1 }, scene);
    staleTraces.material = roleMaterial("traces-stale-material", CABLE_STALE, 0.85);
    staleTraces.isPickable = false;
    staleTraces.alwaysSelectAsActiveMesh = true;
    staleTraces.thinInstanceSetBuffer("matrix", matrixBuffer(traceCore.stale), 16, true);

    var graveMesh = BABYLON.MeshBuilder.CreateBox("graves", { size: 1 }, scene);
    graveMesh.material = pbr("grave", "#2a1512", 0.1, 0.9);
    graveMesh.material.emissiveColor = new BABYLON.Color3(0.5, 0.05, 0.03);
    graveMesh.isPickable = false;
    graveMesh.alwaysSelectAsActiveMesh = true;
    graveMesh.thinInstanceSetBuffer("matrix", matrixBuffer(graveMatrices), 16, true);

    glow.addIncludedOnlyMesh(freshTraces);
    glow.addIncludedOnlyMesh(staleTraces);
    glow.addIncludedOnlyMesh(beaconMesh);
    glow.addIncludedOnlyMesh(graveMesh);

    /* ---- the ready-ring: the ring road of the document, with lanes and tram units --------- */
    var ringRoad = null;
    var dashMesh = null;
    var curbMesh = null;
    var tramMesh = null;
    var trams = [];
    var loop = null;
    if (doc.ring && (doc.ring.path || []).length > 3) {
      var ring = doc.ring;
      var path = ring.path;
      loop = makeLoop(path);
      var laneWidth = 7;

      /* the road: flat asphalt strips along the closed path, with glowing curbs */
      var roadMatrices = [];
      var dashMatrices = [];
      var curbMatrices = [];
      for (var leg = 0; leg < path.length; leg++) {
        var a = path[leg];
        var b = path[(leg + 1) % path.length];
        var length = Math.hypot(b.x - a.x, b.z - a.z);
        if (length < 0.01) continue;
        var heading = Math.atan2(b.x - a.x, b.z - a.z);
        roadMatrices.push(
          BABYLON.Matrix.Compose(
            new BABYLON.Vector3(laneWidth, 0.12, length),
            BABYLON.Quaternion.FromEulerAngles(0, heading, 0),
            new BABYLON.Vector3((a.x + b.x) / 2, 0.17, (a.z + b.z) / 2)
          )
        );
        for (var side of [-1, 1]) {
          /* the curb: a thin glowing strip along each road edge */
          var curbOffset = side * (laneWidth / 2 - 0.18);
          curbMatrices.push(
            BABYLON.Matrix.Compose(
              new BABYLON.Vector3(0.22, 0.07, length),
              BABYLON.Quaternion.FromEulerAngles(0, heading, 0),
              new BABYLON.Vector3(
                (a.x + b.x) / 2 + Math.cos(heading) * curbOffset,
                0.27,
                (a.z + b.z) / 2 - Math.sin(heading) * curbOffset
              )
            )
          );
        }
        for (var dash = 3; dash < length - 3; dash += 7) {
          var t = dash / length;
          dashMatrices.push(
            BABYLON.Matrix.Compose(
              new BABYLON.Vector3(2.2, 0.08, 0.34),
              BABYLON.Quaternion.FromEulerAngles(0, heading, 0),
              new BABYLON.Vector3(a.x + (b.x - a.x) * t, 0.26, a.z + (b.z - a.z) * t)
            )
          );
        }
      }
      ringRoad = BABYLON.MeshBuilder.CreateBox("ring-road", { size: 1 }, scene);
      ringRoad.material = pbr("asphalt", "#2b3540", 0.2, 0.8);
      ringRoad.isPickable = false;
      ringRoad.alwaysSelectAsActiveMesh = true;
      ringRoad.thinInstanceSetBuffer("matrix", matrixBuffer(roadMatrices), 16, true);

      dashMesh = BABYLON.MeshBuilder.CreateBox("lanes", { size: 1 }, scene);
      dashMesh.material = roleMaterial("dash-material", "#5c7482", 0.3);
      dashMesh.isPickable = false;
      dashMesh.alwaysSelectAsActiveMesh = true;
      dashMesh.thinInstanceSetBuffer("matrix", matrixBuffer(dashMatrices), 16, true);

      curbMesh = BABYLON.MeshBuilder.CreateBox("curbs", { size: 1 }, scene);
      curbMesh.material = roleMaterial("curb-material", CABLE_FRESH, 0.22);
      curbMesh.isPickable = false;
      curbMesh.alwaysSelectAsActiveMesh = true;
      curbMesh.thinInstanceSetBuffer("matrix", matrixBuffer(curbMatrices), 16, true);

      /* tram units: one per run cell, riding the loop (run state is the later-stage seam) */
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
    function label(text, position, width, height, accent) {
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
      texture.hasAlpha = true;
      var brush = texture.getContext();
      brush.clearRect(0, 0, 256, 64);
      brush.fillStyle = "rgba(10, 15, 20, 0.8)";
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
      material.emissiveTexture = texture;
      material.opacityTexture = texture;
      material.backFaceCulling = false;
      material.disableLighting = true;
      plane.material = material;
      plane.position = position;
      plane.billboardMode = BABYLON.Mesh.BILLBOARDMODE_Y;
      plane.isPickable = false;
      labelMeshes.push(plane);
      return plane;
    }

    var important = built.cellList
      .filter(function (cell) {
        return cell.family === "tower" || cell.family === "monument";
      })
      .slice(0, 140);
    for (var named of important) {
      var name = named.path || named.name || named.id;
      if (named.family === "tower") {
        label(
          name,
          new BABYLON.Vector3(named.plot.x, named.height + 1.9, named.plot.z),
          8,
          1.6,
          "#8fd8ff"
        );
        label(
          named.checksum ? String(named.checksum) : "checksum —",
          new BABYLON.Vector3(named.plot.x, named.height + 0.9, named.plot.z),
          5.4,
          1.2,
          named.checksum ? "#2fd9c0" : "#5b6b78"
        );
      } else {
        label(
          name,
          new BABYLON.Vector3(named.plot.x, named.height + 1.3, named.plot.z),
          8,
          1.6,
          "#d8bf8a"
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
    var cables = group("cables");
    var details = group("details");
    for (var tileBand of ["header", "index", "cells"]) tileMeshes[tileBand].parent = districts;
    baseMesh.parent = districts;
    if (ringRoad) ringRoad.parent = districts;
    for (var role of Object.keys(roleMeshes)) roleMeshes[role].parent = buildings;
    beaconMesh.parent = buildings;
    crownMesh.parent = buildings;
    voidMesh.parent = buildings;
    graveMesh.parent = buildings;
    if (tramMesh) tramMesh.parent = buildings;
    if (dashMesh) dashMesh.parent = buildings;
    if (curbMesh) curbMesh.parent = buildings;
    baseTraces.parent = cables;
    freshTraces.parent = cables;
    staleTraces.parent = cables;
    for (var plane of labelMeshes) plane.parent = details;
    built.labels = labelMeshes;

    /* selection: a glowing plate and an edge box on the picked building */
    var selection = new BABYLON.TransformNode("selection", scene);
    var halo = BABYLON.MeshBuilder.CreateTorus(
      "selection-halo",
      { diameter: 4, thickness: 0.3, tessellation: 4 },
      scene
    );
    var haloMaterial = new BABYLON.PBRMaterial("selection-halo-material", scene);
    haloMaterial.albedoColor = new BABYLON.Color3(1, 0.82, 0.35);
    haloMaterial.emissiveColor = new BABYLON.Color3(1, 0.72, 0.22);
    haloMaterial.metallic = 0;
    haloMaterial.roughness = 0.4;
    halo.material = haloMaterial;
    halo.scaling.y = 0.16;
    halo.position.y = 0.42;
    halo.parent = selection;
    var box = BABYLON.MeshBuilder.CreateBox("selection-box", { size: 1 }, scene);
    box.enableEdgesRendering();
    box.edgesColor = new BABYLON.Color4(1, 0.82, 0.35, 1);
    box.edgesWidth = 4;
    var boxMaterial = new BABYLON.PBRMaterial("selection-box-material", scene);
    boxMaterial.albedoColor = new BABYLON.Color3(1, 0.82, 0.35);
    boxMaterial.alpha = 0.06;
    box.material = boxMaterial;
    box.parent = selection;
    glow.addIncludedOnlyMesh(halo);
    selection.setEnabled(false);
    built.selection = selection;
    built.halo = halo;
    built.box = box;

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

      var glow = new BABYLON.GlowLayer("glow", scene, { mainTextureFixedSize: 512, blurKernelSize: 42 });
      glow.intensity = 0.8;

      var fill = new BABYLON.HemisphericLight("fill", new BABYLON.Vector3(0.35, 1, -0.25), scene);
      fill.intensity = 0.9;
      fill.groundColor = new BABYLON.Color3(0.07, 0.09, 0.12);
      var key = new BABYLON.DirectionalLight("key", new BABYLON.Vector3(-0.55, -1, 0.4), scene);
      key.position = new BABYLON.Vector3(160, 260, -120);
      key.intensity = 1.5;
      var shadows = new BABYLON.ShadowGenerator(2048, key);
      shadows.useBlurExponentialShadowMap = true;
      shadows.blurKernel = 24;
      shadows.setDarkness(0.35);

      var world = buildWorld(scene, doc, glow);
      for (var mesh of scene.meshes) {
        if (mesh.name.indexOf("floors-") === 0) shadows.addShadowCaster(mesh);
      }

      var camera = new BABYLON.UniversalCamera("rig", new BABYLON.Vector3(0, 170, 0), scene);
      camera.inputs.clear(); /* the rig owns the controls */
      camera.minZ = 0.5;
      camera.maxZ = 6000;
      var rig = new Rig(camera, canvas);
      var bounds = world.bounds;
      rig.state.x = rig.goal.x = bounds.x;
      rig.state.z = rig.goal.z = bounds.z;
      rig.apply();

      var listeners = [];
      var selected = null;
      var motion = options.motion !== false;
      var clock = 0;

      function select(cell) {
        selected = cell;
        if (!cell) {
          world.selection.setEnabled(false);
        } else {
          world.selection.setEnabled(true);
          world.selection.position.x = cell.plot.x;
          world.selection.position.z = cell.plot.z;
          world.halo.scaling.x = world.halo.scaling.z = Math.max(1, cell.footprint + 1.2);
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
        if (global.RXFWorldHud) global.RXFWorldHud.status(payload);
        for (var listener of listeners) listener(payload);
      }

      function targetOf(pose) {
        var target = { x: bounds.x, z: bounds.z };
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
              ? rig.fitRect(bounds, pose.pitch, pose.yaw)
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
        } else if (pointer.button === 2 && (event.buttons & 2)) {
          rig.goal.yaw -= dx * 0.25;
          rig.goal.pitch = clamp(rig.goal.pitch + dy * 0.2, 22, 90);
          rig.state.yaw = rig.goal.yaw;
          rig.state.pitch = rig.goal.pitch;
          rig.apply();
        }
        pointer.x = event.clientX;
        pointer.y = event.clientY;
      });
      canvas.addEventListener("pointerup", function (event) {
        if (pointer.button === 0 && pointer.moved < 6) {
          var pick = scene.createPickingRay(
            event.clientX - canvas.getBoundingClientRect().left,
            event.clientY - canvas.getBoundingClientRect().top,
            BABYLON.Matrix.Identity(),
            camera
          );
          var hit = null;
          if (pick.ray.direction.y !== 0) {
            var ground = -pick.ray.origin.y / pick.ray.direction.y;
            if (ground > 0) {
              var point = pick.ray.origin.add(pick.ray.direction.scale(ground));
              var best = 3.2;
              var hitDistance = Infinity;
              for (var cell of world.cellList) {
                var distance =
                  Math.hypot(point.x - cell.plot.x, point.z - cell.plot.z) - cell.footprint * 0.5;
                if (distance < best && distance < hitDistance) {
                  hitDistance = distance;
                  hit = cell;
                }
              }
            }
          }
          select(hit);
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
        /* labels hold their screen size through the zoom: one scale for the whole LOD range */
        var labelScale = clamp(rig.state.altitude / 46, 0.42, 1.5);
        if (Math.abs(labelScale - lastLabelScale) > 0.01) {
          lastLabelScale = labelScale;
          for (var labelPlane of world.labels) {
            labelPlane.scaling.x = labelPlane.scaling.y = labelScale;
          }
        }
        if (motion && tramsMoving()) {
          clock += dt;
          updateTrams();
        }
        var level = rig.lod();
        world.groups.buildings.setEnabled(true);
        world.groups.cables.setEnabled(level !== LOD_LEVELS[0]);
        world.groups.details.setEnabled(level === LOD_LEVELS[2] || level === LOD_LEVELS[3]);
        if (level.name !== lastLevel) {
          lastLevel = level.name;
          if (global.RXFWorldHud) global.RXFWorldHud.lod(level);
        }
        scene.render();
        if (global.RXFWorldHud && global.RXFWorldHud.minimap && global.RXFWorldHud.minimap.view) {
          global.RXFWorldHud.minimap.view(rig.view(), selected);
        }
      });

      function tramsMoving() {
        return Boolean(world.tramMesh && world.trams && world.trams.length);
      }
      function updateTrams() {
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

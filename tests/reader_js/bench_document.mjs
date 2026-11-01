// Node bench for the world document passes (ROADMAP U5: the ~20 s load target).
//
// Same protocol as run.mjs — the reader file is imported by content, the image
// is read from disk — but instead of writing the document it times it: the open
// (the constructor's format layer) and document() per pass, plus, with --trace,
// inclusive times and call counts per accessor. With --hash it also prints the
// sha256 of JSON.stringify(document()) so a caching change can prove the output
// stayed byte-identical without a golden update.
//
// usage: node bench_document.mjs <path to rxf-reader.js> <path to image> [--trace] [--hash]

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const [readerPath, imagePath] = process.argv.slice(2);
const trace = process.argv.includes("--trace");
const hash = process.argv.includes("--hash");

const source = readFileSync(readerPath, "utf8");
const reader = await import(
    "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);
const bytes = new Uint8Array(readFileSync(imagePath));

// -- pass timings (always on; five wrappers are noise next to the passes) ----

const passes = [];
function wrap(prototype, name, sink) {
    if (prototype === undefined || !Object.prototype.hasOwnProperty.call(prototype, name)) return;
    const original = prototype[name];
    if (typeof original !== "function") return;
    prototype[name] = function (...args) {
        const started = process.hrtime.bigint();
        try {
            return original.apply(this, args);
        } finally {
            sink.push({ name, ms: Number(process.hrtime.bigint() - started) / 1e6 });
        }
    };
}

// -- accessor attribution (--trace) -----------------------------------------

const accessors = [];
function wrapAccessor(prototype, name) {
    if (prototype === undefined || !Object.prototype.hasOwnProperty.call(prototype, name)) return;
    const original = prototype[name];
    if (typeof original !== "function") return;
    const slot = { name, calls: 0, ms: 0 };
    accessors.push(slot);
    prototype[name] = function (...args) {
        slot.calls++;
        const started = process.hrtime.bigint();
        try {
            return original.apply(this, args);
        } finally {
            slot.ms += Number(process.hrtime.bigint() - started) / 1e6;
        }
    };
}

for (const name of ["basePass", "buildingsPass", "cablesPass", "cityPass", "routesPass"]) {
    wrap(reader.RxfReader.prototype, name, passes);
    wrap(reader.RxfArena.prototype, name, passes);
}
if (trace) {
    for (const name of [
        "open", "get", "cell", "name", "path", "slot", "live", "refsOf", "payloadBytes",
        "typeInfo", "type_name", "layout", "aliasUses", "writtenPaths", "collectAliases",
        "scopeOf", "isAlias", "isGround",
    ]) {
        wrapAccessor(reader.RxfReader.prototype, name);
        wrapAccessor(reader.RxfArena.prototype, name);
    }
}

// -- one arena, one document (the readWorld path) ----------------------------

const openStarted = process.hrtime.bigint();
const arena = bytes.length > 3 && bytes[0] === 0x52 && bytes[1] === 0x58
    && bytes[2] === 0x46 && bytes[3] === 0x41
    ? new reader.RxfArena(bytes) : new reader.RxfReader(bytes);
const openMs = Number(process.hrtime.bigint() - openStarted) / 1e6;

const documentStarted = process.hrtime.bigint();
const document = arena.document();
const documentMs = Number(process.hrtime.bigint() - documentStarted) / 1e6;

// -- the per-region paging (U5): what a viewport pulls instead of the whole layer -----
//
// The reader's per-layer reads answer one district (or a window) from its own record runs
// plus the shared tables; the whole layer pulls every run. The numbers below are the bytes a
// paged fetch of each answer carries (readLayer's `pull`) — the before/after of paging in one
// run. Each read gets its own fresh arena so the times are cold (the open is the shared cost
// the report carries once).

function coldLayer(name, options) {
    const fresh = bytes.length > 3 && bytes[0] === 0x52 && bytes[1] === 0x58
        && bytes[2] === 0x46 && bytes[3] === 0x41
        ? new reader.RxfArena(bytes) : new reader.RxfReader(bytes);
    fresh.open();
    const started = process.hrtime.bigint();
    const out = fresh.readLayer(name, options);
    return {
        regions: out.regions,
        items: out.items.length,
        pull_bytes: out.pull.bytes,
        pull_runs: out.pull.runs.length,
        ms: Math.round(Number(process.hrtime.bigint() - started) / 1e5) / 10,
    };
}
const canPage = typeof arena.readLayer === "function";
const districtIds = canPage ? arena.regions().map((region) => region.id) : [];
const windowStart = Math.max(0, (districtIds.length >> 1) - 5);
const paging = canPage
    ? {
        districts: districtIds.length,
        viewport_1: coldLayer("cells", { region: districtIds[districtIds.length >> 1] }),
        viewport_10: coldLayer("cells", {
            regions: districtIds.slice(windowStart, windowStart + 10),
        }),
        whole_layer: coldLayer("cells"),
        edges_viewport_1: coldLayer("edges", { region: districtIds[districtIds.length >> 1] }),
        edges_whole_layer: coldLayer("edges"),
    }
    : null;

const report = {
    image_bytes: bytes.length,
    reader: arena instanceof reader.RxfArena ? "RxfArena" : "RxfReader",
    open_ms: Math.round(openMs * 100) / 100,
    document_ms: Math.round(documentMs * 100) / 100,
    passes: passes.map((pass) => ({ name: pass.name, ms: Math.round(pass.ms * 100) / 100 })),
    cells: document.cells.length,
    edges: document.edges.length,
    paging,
};
{
    // how many cable runs the routing actually has to compute: the distinct
    // from/to pairs the per-pair route cache can serve (U5 residual evidence)
    const pairKeys = new Set();
    let graves = 0;
    for (const edge of document.edges) {
        if (edge.to === null) graves += 1;
        else pairKeys.add(edge.from + "|" + edge.to);
    }
    report.edge_pairs = pairKeys.size;
    report.edge_graves = graves;
    report.edge_cells = new Set(document.edges.flatMap((edge) => [edge.from, edge.to])).size;
    report.streets = document.streets.length;
    report.ring_points = document.ring.path.length;
}
if (trace) {
    report.accessors = accessors
        .map((slot) => ({ name: slot.name, calls: slot.calls, ms: Math.round(slot.ms * 100) / 100 }))
        .sort((a, b) => b.ms - a.ms);
}
if (hash) {
    report.document_sha256 = createHash("sha256").update(JSON.stringify(document)).digest("hex");
}
process.stdout.write(JSON.stringify(report) + "\n");

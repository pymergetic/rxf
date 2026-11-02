// The reader's per-region pulls: what one viewport fetches and reads (U5 paging).
//
// usage: node region_pulls.mjs <path to rxf-reader.js> <path to .rxfa>
//
// One JSON object per region id (``RxfArena.regions()``): the merged byte runs the
// region's per-layer reads decode (``readLayer('cells')`` / ``readLayer('edges')`` —
// their ``pull``, one fetch serves both layers) and ``parity``: whether the paged read
// of exactly those runs — the sparse face (``new RxfArena(bytes, runs)``) — answers the
// same items the whole-arena read answers. The Python twin's region index
// (``ArenaScene.stream_parts(region=...)``) answers exactly these runs; the fetch carries
// them and no others (tests/test_reader_js.py pins the two faces run for run).
import { readFileSync } from "node:fs";

const [readerPath, imagePath] = process.argv.slice(2);
const source = readFileSync(readerPath, "utf8");
const reader = await import(
    "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);
const bytes = new Uint8Array(readFileSync(imagePath));
const arena = new reader.RxfArena(bytes);

const merge = (spans) => {
    const sorted = [...spans].sort((a, b) => a[0] - b[0]);
    const out = [];
    for (const [lo, hi] of sorted) {
        if (out.length && out[out.length - 1][1] >= lo) {
            out[out.length - 1][1] = Math.max(out[out.length - 1][1], hi);
        } else {
            out.push([lo, hi]);
        }
    }
    return out;
};

const pulls = {};
for (const region of arena.regions()) {
    const cells = arena.readLayer("cells", { region: region.id });
    const edges = arena.readLayer("edges", { region: region.id });
    const runs = merge(cells.pull.runs);
    // the physical half of the law: fetch exactly the runs, read the region from them
    const sparse = new Uint8Array(bytes.length);
    for (const [lo, hi] of runs) sparse.set(bytes.subarray(lo, hi), lo);
    const paged = new reader.RxfArena(sparse, runs.map((run) => [...run]));
    const parity =
        JSON.stringify(paged.readLayer("cells", { region: region.id }).items) ===
            JSON.stringify(cells.items) &&
        JSON.stringify(paged.readLayer("edges", { region: region.id }).items) ===
            JSON.stringify(edges.items);
    pulls[region.id] = {
        runs,
        bytes: runs.reduce((n, [lo, hi]) => n + hi - lo, 0),
        cells: cells.items.length,
        edges: edges.items.length,
        parity,
    };
}
process.stdout.write(JSON.stringify(pulls));

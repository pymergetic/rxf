// rxf-reader.js — the rxfb reader: one arena's bytes, the world it carries.
//
// The scene of an image used to arrive as JSON megabytes computed by the host; this module
// reads the SAME arena binary the host keeps (``rxfb``, core/ground.h) and rebuilds the SAME
// scene document the Python exporter writes (world/export.py) — word for word, from the bytes
// alone. One format, two readers: the Python exporter stays the golden face (tests pin the
// document), this is the client face (the page loads one binary and sees the world). The node
// pin (tests/test_reader_js.py) runs this module against the exporter on the same image and
// fails on the first difference — the two readers never drift.
//
// What it reads, in the layers of the format:
//   * the arena: the 192-byte header, the cell stream, the handle index, the names;
//   * the object graph: parent chains (paths), reference rows, the self-describing Type/Field
//     payloads (the declared reference slots), and the Alias objects of a module;
//   * the world: the layout law of export.py — districts on the byte map, one building per
//     cell on its district's tile raster, the poison voids, the street network, the ring road
//     around it, and every reference a cable — routed over the streets.
//
// What it does NOT do: duct packing (``_bundles``). The packing is the host's trade — minutes
// on a real image, and the page draws cables and stubs without it. A reader document carries
// ``bundles: []`` and every edge's ``stubs: []``; the seams stay in the document, named and
// empty, so nothing pretends they were packed.
//
// Determinism is the same law as the exporter: pure bytes in, one document out — no clock, no
// randomness, no map iteration order.

/* eslint-disable no-bitwise */

// ---- the ground constants (core/ground.h, core/kinds, world/export.py) --------------------------------

const HANDLE_NONE = 0xffffffffffffffffn;

// pymergetic_rxf_arena_header: 192 bytes, these offsets are pinned by ground.h.
const HDR_MAGIC = 0;
const HDR_VERSION = 4;
const HDR_HEADER_SIZE = 8;
const HDR_FLAGS = 12;
const HDR_BODY_SIZE = 16;
const HDR_COMMITTED_SIZE = 24;
const HDR_LIMIT = 32;
const HDR_FRONTIER = 40;
const HDR_INDEX_OFF = 48;
const HDR_CELLS_OFF = 56;
const HDR_NAMES_OFF = 64;
const HDR_NAMES_SIZE = 72;
const HDR_INDEX_CHUNKS = 80;
const HDR_HANDLE_COUNT = 88;
const HDR_FREE_HEAD = 96;
const HDR_ENTRY = 104;
const HDR_ROOT = 112;
const HDR_CELL_ALIGN = 120;
const HDR_PAGE_SIZE = 124;

// pymergetic_rxf_cell: 64 bytes; pymergetic_rxf_ref: 24 bytes.
const CELL_BYTES = 64;
const CELL_ALIGN = 16;
const REF_BYTES = 24;
const INDEX_CHUNK_ENTRIES = 4096;
const INDEX_FREE = 1n;

const KIND_BLOCK = 255;
const KIND_DEAD = 254;
const FLAG_KIND_MASK = 0xff;
const FLAG_STATE_SHIFT = 8;
const FLAG_STATE_MASK = 0x3f;
const FLAG_OWNER_SHIFT = 14;

// Type and Field payloads: 48 bytes each, tags "TYPE" / "FLD ".
const TYPE_BYTES = 48;
const TYPE_TAG = 0x54595045;
const FIELD_TAG = 0x464c4420;
const FIELD_FLAG_OPTIONAL = 0x2;
const FIELD_FLAG_REF = 0x8;
const FIELD_FLAG_CHILDREN = 0x10;
const FIELD_FLAG_INVERSE = 0x20;
const TYPE_FLAG_VARIABLE = 0x1;
const TYPE_FLAG_BITSET = 0x8;
const FORM_STRUCT = 7;
const FORM_UNION = 8;
const FORM_ENUM = 9;
const FORM_COUNT = 18;

const NODE_ALIAS = 21;
const NODE_MODULE = 19;
const NODE_ENUMERATOR = 20;

// The words of the ground enumerations, in value order (core/kinds.pyi).
const NODE_KINDS = [
    "Root", "Group", "Namespace", "Card", "Type", "Field", "Fn", "Code", "Data",
    "9", "Section", "Limit", "Import", "Export", "View", "Tool", "Blob", "Channel",
    "Transaction", "Module", "Enumerator", "Alias",
];
const REF_KINDS = ["Call", "Data", "Entry", "Type", "Import", "Implementation", "Owner", "Doc"];
const BINDINGS = ["Mandatory", "Optional"];
const MOBILITIES = ["Movable", "Pinned", "Relocatable"];

// The world layout law (world/export.py).
const DISTRICT_SPAN = 4096;
const BYTE_SCALE = 4.5 / 1024.0;
const DISTRICT_DEPTH = 26.0;
const DISTRICT_GAP = 12.0;
const DISTRICT_COLS = 4;
const GRID_STEP = 2.2;
const GRID_MARGIN = 1.1;
const RING_MARGIN = 4.0;
const RING_WIDTH = 5.6;
const STREET_WIDTH = 3.2;
const LANE_WIDTH = 1.6;
const LANE_MIN = 0.8;
const CLEARANCE = 1.6;
const JUNCTION_SNAP = 0.25;
const RING_BEVEL = 2.0;
const FP_RATIO = 0.5;
const BLOCK_BIN = 2;
const GRAVE_REACH = 3.4;
const PIN_MARGIN = 1.1;
const MICRO_LEG = 0.9;
const PROJECT_BIN = 24.0;
const POISON_BYTE = 0xa5;
const POISON_RUN = 64;
const VOID_TILE = 512;

const SCHEMA = "rxf-world-3";

const HEADER_WORDS = [
    ["type", 8], ["parent", 8], ["payload_size", 8], ["generation", 8], ["name", 8],
    ["flags", 4], ["ref_count", 4], ["forward", 8], ["index", 4], ["reserved", 4],
];
const RUN_PAYLOAD_REGIONS = [["concurrent", 28], ["handoff", 36], ["program", 208], ["checksum", 8]];
const RUN_WORDS_BYTES = 280;
const RUN_STATES = ["FREE", "READY", "RUNNING", "PARKED", "TERMINAL"];
const ON_ROAD = ["READY", "RUNNING"];
const FNV_PRIME = 0x100000001b3n;
const FNV_BASIS = 0xcbf29ce484222325n;
const FAMILIES = ["tower", "monument", "block", "utility", "rubble"];
const HOWS = ["ref", "role", "field", "alias"];
const STREET_KINDS = ["avenue", "cross", "lane", "branch"];

// ---- the arithmetic of the exporter -----------------------------------------------------------------

// The exporter rounds world coordinates to three decimals — `round(value, 3)` in Python: the
// nearest milli, ties broken to EVEN. A tie is a double exactly halfway between two milli
// values, (2k+1)/2000 — a double only where 125 divides 2k+1, so exactly the values whose
// sixteenths (value*16) are odd integers (9.5625 is one: 153/16). Every other value is an
// unambiguous nearest rounding and `toFixed(3)` gives Python's answer.
function round3(value) {
    const sixteenths = value * 16;
    if (Number.isInteger(sixteenths) && Math.abs(sixteenths) % 2 === 1) {
        const lower = Math.floor(value * 1000 - 0.5); // the two milli neighbors: lower, lower+1
        return (lower % 2 === 0 ? lower : lower + 1) / 1000;
    }
    return Number(value.toFixed(3));
}

// `round(x)` with no digits in Python: ties to even. Only the routing fallback uses it.
function roundEven(value) {
    const floor = Math.floor(value);
    const half = value - floor;
    if (half > 0.5) return floor + 1;
    if (half < 0.5) return floor;
    return floor % 2 === 0 ? floor : floor + 1;
}

// The exporter's `math.hypot` / `math.dist` sit in diagnostics and in the pad placement; a
// last-ulp difference between arithmetic is fine at three decimals and pinned when it is not.
function hypot(x, z) {
    return Math.sqrt(x * x + z * z);
}

function dist(a, b) {
    return hypot(a[0] - b[0], a[1] - b[1]);
}

function align16(value) {
    return (value + 15) & ~15;
}

function word(words, value) {
    return value >= 0 && value < words.length ? words[value] : String(value);
}

function stateWord(flags) {
    const state = (flags >> FLAG_STATE_SHIFT) & FLAG_STATE_MASK;
    return {
        provenance: state & 0x1 ? "Image" : "Runtime",
        disposition: ["Retain", "Transient", "Tombstone"][(state >> 1) & 0x3],
        cleanliness: (state >> 3) & 0x1 ? "Dirty" : "Clean",
        mobility: word(MOBILITIES, (state >> 4) & 0x3),
    };
}

function fnv1a(bytes, from, to) {
    let word = FNV_BASIS;
    for (let at = from; at < to; at++) {
        word = ((word ^ BigInt(bytes[at])) * FNV_PRIME) & 0xffffffffffffffffn;
    }
    return word;
}

// ---- the arena: header, cell stream, handle index, names -------------------------------------------

export class RxfReader {
    constructor(bytes) {
        const buffer = bytes instanceof Uint8Array
            ? bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
            : bytes;
        this.bytes = new Uint8Array(buffer);
        this.view = new DataView(buffer);
        if (this.bytes.length < 192) throw new Error("BadImage: not an rxfb arena");
        const magic = this.bytes.subarray(HDR_MAGIC, HDR_MAGIC + 4);
        if (!(magic[0] === 0x52 && magic[1] === 0x58 && magic[2] === 0x46 && magic[3] === 0x42)) {
            throw new Error("BadImage: not an rxfb arena (magic is not RXFB)");
        }
        this.version = this.view.getUint32(HDR_VERSION, true);
        this.headerSize = this.view.getUint32(HDR_HEADER_SIZE, true);
        this.flags = this.view.getUint32(HDR_FLAGS, true);
        this.bodySize = Number(this.view.getBigUint64(HDR_BODY_SIZE, true));
        this.frontier = Number(this.view.getBigUint64(HDR_FRONTIER, true));
        this.indexOff = Number(this.view.getBigUint64(HDR_INDEX_OFF, true));
        this.cellsOff = Number(this.view.getBigUint64(HDR_CELLS_OFF, true));
        this.namesOff = Number(this.view.getBigUint64(HDR_NAMES_OFF, true));
        this.namesSize = Number(this.view.getBigUint64(HDR_NAMES_SIZE, true));
        this.indexChunks = Number(this.view.getBigUint64(HDR_INDEX_CHUNKS, true));
        this.handleCount = Number(this.view.getBigUint64(HDR_HANDLE_COUNT, true));
        this.entry = this.view.getBigUint64(HDR_ENTRY, true);
        this.root = this.view.getBigUint64(HDR_ROOT, true);
        this.stream = [];
        this.recordAt = new Map(); // arena offset -> record
        this.byHandle = new Map(); // handle -> record
        this.walk();
    }

    // -- the cell stream ---------------------------------------------------------------------------

    cellSizeAt(offset) {
        const payloadSize = Number(this.view.getBigUint64(offset + 16, true));
        const refCount = this.view.getUint32(offset + 44, true);
        return align16(CELL_BYTES + align16(payloadSize) + REF_BYTES * refCount);
    }

    walk() {
        const end = this.cellsOff + this.frontier;
        let at = this.cellsOff;
        while (at < end) {
            if (end - at < CELL_BYTES) throw new Error("BadImage: short cell header");
            const payloadSize = Number(this.view.getBigUint64(at + 16, true));
            const refCount = this.view.getUint32(at + 44, true);
            const reserved = this.view.getUint32(at + 60, true);
            const size = this.cellSizeAt(at);
            if (reserved !== 0 || size < CELL_BYTES || size > end - at) {
                throw new Error("BadImage: malformed cell at " + at);
            }
            const flags = this.view.getUint32(at + 40, true);
            const kind = flags & FLAG_KIND_MASK;
            const record = {
                offset: at,
                size,
                kind,
                blockKind: kind === KIND_BLOCK ? Number(this.view.getBigUint64(at, true)) : 0,
                handle: null,
                type: this.view.getBigUint64(at, true),
                parent: this.view.getBigUint64(at + 8, true),
                payloadSize,
                refCount,
                flags,
                generation: Number(this.view.getBigUint64(at + 24, true)),
                nameOff: Number(this.view.getBigUint64(at + 32, true)),
                forward: Number(this.view.getBigUint64(at + 48, true)),
                index: this.view.getUint32(at + 56, true),
            };
            this.stream.push(record);
            this.recordAt.set(at, record);
            at += size;
        }
        // The index says whose bytes each cell is: slot -> offset, the walk mapped the other way.
        for (let h = 0; h < this.handleCount; h++) {
            const entry = this.slot(BigInt(h));
            if ((entry & INDEX_FREE) === 0n) {
                const offset = Number(entry);
                const record = this.recordAt.get(offset);
                if (record !== undefined && record.handle === null) {
                    record.handle = BigInt(h);
                    this.byHandle.set(BigInt(h), record);
                }
            }
        }
    }

    // -- the handle index --------------------------------------------------------------------------

    slot(handle) {
        const chunk = Number(handle / BigInt(INDEX_CHUNK_ENTRIES));
        const chunkOff = Number(this.view.getBigUint64(this.indexOff + chunk * 8, true));
        const entry = chunkOff + CELL_BYTES + Number(handle % BigInt(INDEX_CHUNK_ENTRIES)) * 8;
        return this.view.getBigUint64(entry, true);
    }

    live(handle) {
        if (handle >= BigInt(this.handleCount)) return false;
        const entry = this.slot(handle);
        return (entry & INDEX_FREE) === 0n
            && Number(entry) >= this.cellsOff
            && Number(entry) < this.cellsOff + this.frontier;
    }

    cell(handle) {
        if (!this.live(handle)) return null;
        return this.recordAt.get(Number(this.slot(handle))) ?? null;
    }

    // -- names and paths ---------------------------------------------------------------------------

    name(handle) {
        const record = this.cell(handle);
        if (record === null || !record.nameOff) return "";
        return this.stringAt(record.nameOff);
    }

    stringAt(offset) {
        let end = offset;
        while (end < this.bytes.length && this.bytes[end] !== 0) end++;
        return new TextDecoder("utf-8").decode(this.bytes.subarray(offset, end));
    }

    path(handle) {
        const parts = [];
        for (let at = handle; at !== HANDLE_NONE && parts.length < 4096;) {
            const record = this.cell(at);
            if (record === null) return "";
            if (record.parent === HANDLE_NONE) break;
            parts.push(this.name(at));
            at = record.parent;
        }
        return parts.reverse().join(".");
    }

    // -- the object graph ---------------------------------------------------------------------------

    refsOf(record) {
        const out = [];
        const at = record.offset + CELL_BYTES + align16(record.payloadSize);
        for (let k = 0; k < record.refCount; k++) {
            const base = at + k * REF_BYTES;
            const role = this.view.getBigUint64(base + 8, true);
            // ground.h: kind u16@16, binding u16@18, index u32@20 (pinned).
            out.push({
                target: this.view.getBigUint64(base, true),
                role: role === HANDLE_NONE ? null : role,
                index: this.view.getUint32(base + 20, true),
                kind: this.view.getUint16(base + 16, true),
                binding: this.view.getUint16(base + 18, true),
            });
        }
        return out;
    }

    payloadBytes(record) {
        return this.bytes.subarray(record.offset + CELL_BYTES, record.offset + CELL_BYTES + record.payloadSize);
    }

    payloadUint(record, offset, size) {
        const base = record.offset + CELL_BYTES + offset;
        if (size === 8) return this.view.getBigUint64(base, true);
        if (size === 4) return BigInt(this.view.getUint32(base, true));
        if (size === 2) return BigInt(this.view.getUint16(base, true));
        return BigInt(this.bytes[base]);
    }

    get(handle) {
        const record = this.cell(handle);
        if (record === null) throw new Error("BadHandle: " + handle);
        return {
            handle,
            type: record.type,
            parent: record.parent,
            name: this.name(handle),
            path: this.path(handle),
            kind: record.flags & FLAG_KIND_MASK,
            state: (record.flags >> FLAG_STATE_SHIFT) & FLAG_STATE_MASK,
            ownerKind: (record.flags >> FLAG_OWNER_SHIFT) & 0xf,
            generation: record.generation,
            index: record.index,
            payload: this.payloadBytes(record),
            refs: this.refsOf(record),
        };
    }

    children(handle) {
        if (this._children === undefined) this._children = new Map();
        const cached = this._children.get(handle);
        if (cached !== undefined) return cached;
        const names = [];
        for (const record of this.stream) {
            if (record.handle !== null && record.parent === handle) {
                names.push([this.name(record.handle), record.handle]);
            }
        }
        names.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
        const out = names.map((entry) => entry[1]);
        this._children.set(handle, out);
        return out;
    }

    // -- the self-describing types (core/meta/types.cpp) ---------------------------------------------

    typeInfo(type) {
        if (this._types === undefined) this._types = new Map();
        const cached = this._types.get(type);
        if (cached !== undefined) return cached;
        const record = this.cell(type);
        if (record === null
            || record.type !== 1n // HANDLE_TYPE
            || record.payloadSize !== TYPE_BYTES
            || this.payloadUint(record, 0, 4) !== BigInt(TYPE_TAG)) {
            this._types.set(type, null);
            return null;
        }
        const fields = [];
        const members = [];
        for (const child of this.children(type)) {
            const f = this.cell(child);
            if (f === null || f.type !== 2n || f.payloadSize !== TYPE_BYTES) continue; // HANDLE_FIELD
            if (this.payloadUint(f, 0, 4) !== BigInt(FIELD_TAG)) continue;
            const flags = Number(this.payloadUint(f, 4, 4));
            const info = {
                id: child,
                name: this.name(child),
                valueType: this.payloadUint(f, 16, 8),
                offset: Number(this.payloadUint(f, 24, 8)),
                count: Number(this.payloadUint(f, 32, 8)),
                flags,
                via: this.payloadUint(f, 40, 8),
                member: (flags & (FIELD_FLAG_CHILDREN | FIELD_FLAG_INVERSE)) !== 0,
            };
            (info.member ? members : fields).push(info);
        }
        fields.sort((a, b) => (a.offset !== b.offset ? a.offset - b.offset : (a.name < b.name ? -1 : 1)));
        members.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
        const info = {
            id: type,
            name: this.name(type),
            form: Number(this.payloadUint(record, 4, 4)),
            size: Number(this.payloadUint(record, 8, 8)),
            inner: this.payloadUint(record, 16, 8),
            ret: this.payloadUint(record, 24, 8),
            align: Number(this.payloadUint(record, 32, 4)),
            flags: Number(this.payloadUint(record, 36, 4)),
            fieldCount: Number(this.payloadUint(record, 40, 4)),
            nodeKind: Number(this.payloadUint(record, 44, 4)),
            fields,
            members,
        };
        info.variable = (info.flags & TYPE_FLAG_VARIABLE) !== 0;
        info.bitset = (info.flags & TYPE_FLAG_BITSET) !== 0;
        this._types.set(type, info);
        return info;
    }

    // The kind word of an object ("Type", "Field", ...); what export.py calls `type_name`.
    type_name(handle) {
        const record = this.cell(handle);
        return record === null ? null : word(NODE_KINDS, record.flags & FLAG_KIND_MASK);
    }

    layout(type) {
        if (this._layouts === undefined) this._layouts = new Map();
        const cached = this._layouts.get(type);
        if (cached !== undefined) return cached;
        const info = this.typeInfo(type);
        const built = { leaves: [], raw: false, fixed: false, prefix: 0, error: "" };
        if (info === null) {
            built.error = "not a Type object";
            this._layouts.set(type, built);
            return built;
        }
        const fixedSize = info.size !== 0 && !info.variable;
        if (info.form === FORM_UNION || info.fields.length === 0) {
            built.raw = true;
            built.fixed = fixedSize;
            built.prefix = fixedSize ? info.size : 0;
            this._layouts.set(type, built);
            return built;
        }
        for (const field of info.fields) {
            if (!this.addLeaves(field, "", 0, built.leaves, built, 0, true, type)) {
                built.leaves = [];
                this._layouts.set(type, built);
                return built;
            }
        }
        built.leaves.sort((a, b) => a.offset - b.offset); // stable, like std::stable_sort
        let end = 0;
        for (const leaf of built.leaves) {
            if (leaf.offset < end) { // overlapping fields: a union in disguise, read as raw
                built.leaves = [];
                built.raw = true;
                built.fixed = fixedSize;
                built.prefix = fixedSize ? info.size : 0;
                this._layouts.set(type, built);
                return built;
            }
            end = leaf.offset + leaf.size;
        }
        built.prefix = Math.max(fixedSize ? info.size : 0, end);
        if (info.size !== 0 && end > info.size && !info.variable) {
            built.leaves = [];
            built.error = "fields exceed the size of the type";
            this._layouts.set(type, built);
            return built;
        }
        built.fixed = !info.variable;
        this._layouts.set(type, built);
        return built;
    }

    addLeaves(field, prefix, base, out, built, depth, topLevelOfType, owner) {
        const v = this.typeInfo(field.valueType);
        if (v === null) {
            built.error = "field " + prefix + field.name + " has an unknown value type";
            return false;
        }
        if (field.count === 0) {
            built.error = "field " + prefix + field.name + " has a zero count";
            return false;
        }
        if (field.offset > 0x7fffffff || field.count > 0x7fffffff || v.size > 0x7fffffff) {
            built.error = "field " + prefix + field.name + " is out of range";
            return false;
        }
        if (out.length >= 1 << 16) {
            built.error = "field " + prefix + field.name + ": the type has too many values";
            return false;
        }
        // Written by the machine, never by an author: the tags, the number of Field children,
        // and the owner of a Field (its parent).
        const derived = topLevelOfType
            && ((owner === 1n && (field.name === "field_count" || field.name === "descriptor_tag"))
                || (owner === 2n && (field.name === "descriptor_tag" || field.name === "owner_type")));
        if ((field.flags & FIELD_FLAG_REF) !== 0) {
            if (v.form !== 3 /* uint */ || v.size !== 8 || field.count !== 1) {
                built.error = "reference field " + prefix + field.name + " must be one uint64";
                return false;
            }
            out.push({
                name: prefix + field.name,
                offset: base + field.offset,
                size: 8,
                reference: true,
                optional: (field.flags & FIELD_FLAG_OPTIONAL) !== 0,
                derived,
                enumeration: null,
                def: 0,
            });
            return true;
        }
        const nested = v.form === FORM_STRUCT && v.fields.length !== 0;
        if (nested) {
            if (depth >= 6) {
                built.error = "field " + prefix + field.name + " nests too deeply";
                return false;
            }
            for (let i = 0; i < field.count; i++) {
                if (out.length >= 1 << 16) {
                    built.error = "field " + prefix + field.name + ": the type has too many values";
                    return false;
                }
                const name = prefix + field.name + (field.count > 1 ? "." + i : "") + ".";
                for (const g of v.fields) {
                    if (!this.addLeaves(g, name, base + field.offset + i * v.size, out, built, depth + 1, false, v.id)) {
                        return false;
                    }
                }
            }
            return true;
        }
        const kind = scalarKind(v);
        if (v.size === 0) {
            built.error = "field " + prefix + field.name + " embeds a variable-size type";
            return false;
        }
        if (kind === "float" && v.size !== 4 && v.size !== 8) {
            out.push({ name: prefix + field.name, offset: base + field.offset, size: v.size * field.count, reference: false, optional: false, derived, enumeration: null, def: 0 });
            return true;
        }
        if ((kind !== "hex" && ![1, 2, 4, 8].includes(v.size) && kind !== "float") || (kind === "bool" && v.size !== 1)) {
            out.push({ name: prefix + field.name, offset: base + field.offset, size: v.size * field.count, reference: false, optional: false, derived, enumeration: null, def: 0 });
            return true;
        }
        if (field.count > 1 && v.size === 1 && (kind === "uint" || kind === "hex")) {
            out.push({ name: prefix + field.name, offset: base + field.offset, size: field.count, reference: false, optional: false, derived, enumeration: null, def: 0 });
            return true;
        }
        if (kind === "hex") {
            out.push({ name: prefix + field.name, offset: base + field.offset, size: v.size * field.count, reference: false, optional: false, derived, enumeration: null, def: 0 });
            return true;
        }
        for (let i = 0; i < field.count; i++) {
            if (out.length >= 1 << 16) {
                built.error = "field " + prefix + field.name + ": the type has too many values";
                return false;
            }
            out.push({
                name: prefix + field.name + (field.count > 1 ? "." + i : ""),
                offset: base + field.offset + i * v.size,
                size: v.size,
                reference: false,
                optional: false,
                derived,
                enumeration: kind === "enum" ? v.id : null,
                def: topLevelOfType && owner === 2n && field.name === "count" ? 1 : 0,
            });
        }
        return true;
    }

    // -- aliases (core/meta/types.cpp) ---------------------------------------------------------------

    isAlias(handle) {
        const record = this.cell(handle);
        return record !== null && (record.flags & FLAG_KIND_MASK) === NODE_ALIAS;
    }

    isModule(handle) {
        const record = this.cell(handle);
        return record !== null && (record.flags & FLAG_KIND_MASK) === NODE_MODULE;
    }

    isGround(handle) {
        const record = this.cell(handle);
        if (record === null || handle === 0n) return false;
        if (handle <= 2n) return true; // the root's three fixed objects save the root itself
        let top = handle;
        while (true) {
            const c = this.cell(top);
            if (c === null) return false;
            if (c.parent === HANDLE_NONE) return false;
            if (c.parent === 0n) break;
            top = c.parent;
        }
        return top <= 2n || isGroundName(this.name(top));
    }

    scopeOf(handle) {
        for (let at = handle; at !== HANDLE_NONE;) {
            const record = this.cell(at);
            if (record === null) return null;
            if ((record.flags & FLAG_KIND_MASK) === NODE_MODULE) return at;
            at = record.parent;
        }
        return null;
    }

    aliasTarget(alias) {
        const record = this.cell(alias);
        if (record === null || record.type === HANDLE_NONE) return null;
        const layout = this.layout(record.type);
        if (layout.error || layout.raw) return null;
        for (const leaf of layout.leaves) {
            if (leaf.reference && leaf.name === "target" && leaf.offset + 8 <= record.payloadSize) {
                const h = this.payloadUint(record, leaf.offset, 8);
                return h !== HANDLE_NONE && this.cell(h) !== null ? h : null;
            }
        }
        return null;
    }

    collectAliases(module) {
        const names = [];
        const handles = [];
        for (const child of this.children(module)) {
            if (!this.isAlias(child)) continue;
            const target = this.aliasTarget(child);
            if (target === null) continue;
            names.push([this.name(child), this.path(target)]);
            handles.push(child);
        }
        return { names, handles };
    }

    // The paths an object writes through its payload references and reference rows.
    writtenPaths(handle) {
        const out = [];
        const record = this.cell(handle);
        if (record === null) return out;
        for (const attr of this.decodePaths(handle)) out.push(attr);
        for (const row of this.refsOf(record)) {
            if (row.target !== HANDLE_NONE && this.cell(row.target) !== null) out.push(this.path(row.target));
            if (row.role !== null && this.cell(row.role) !== null) out.push(this.path(row.role));
        }
        return out;
    }

    // The path-valued leaves of a payload (references and enumerators), as the codec spells
    // them — an empty list when the payload does not fit its type (the codec refuses it).
    decodePaths(handle) {
        const record = this.cell(handle);
        const out = [];
        if (record === null || record.type === HANDLE_NONE) return out;
        const layout = this.layout(record.type);
        if (layout.error) return out;
        if (layout.raw) return out; // one hex value, never a path
        if (layout.fixed ? record.payloadSize !== layout.prefix : record.payloadSize < layout.prefix) return out;
        const payload = this.payloadBytes(record);
        let at = 0;
        for (const leaf of layout.leaves) {
            for (let i = at; i < leaf.offset; i++) if (payload[i] !== 0) return out; // padding is not zero
            at = leaf.offset + leaf.size;
        }
        for (let i = at; i < layout.prefix; i++) if (payload[i] !== 0) return out;
        for (const leaf of layout.leaves) {
            if (leaf.derived) continue;
            if (leaf.reference) {
                const h = this.payloadUint(record, leaf.offset, 8);
                if (h === HANDLE_NONE) continue;
                if (this.cell(h) === null) return out; // a dead handle: the codec refuses it
                out.push(this.path(h));
            } else if (leaf.enumeration !== null) {
                const value = this.payloadUint(record, leaf.offset, leaf.size);
                if (value === 0n) continue;
                const named = this.enumerationName(leaf.enumeration, value, record.payloadSize, leaf);
                if (named === null) return out; // an unnamed value: the codec refuses it
                out.push(named);
            }
        }
        return out;
    }

    enumerationName(enumType, value) {
        const info = this.typeInfo(enumType);
        if (info === null || info.form !== FORM_ENUM) return null;
        const items = [];
        for (const child of this.children(enumType)) {
            const c = this.cell(child);
            if (c === null || (c.flags & FLAG_KIND_MASK) !== NODE_ENUMERATOR || c.payloadSize !== 8) continue;
            items.push([this.name(child), this.payloadUint(c, 0, 8)]);
        }
        items.sort((a, b) => (a[1] !== b[1] ? (a[1] < b[1] ? -1 : 1) : (a[0] < b[0] ? -1 : 1)));
        const head = this.path(enumType) + ".";
        if (!info.bitset) {
            for (const [name, item] of items) if (item === value) return head + name;
            return null;
        }
        let text = "";
        let rest = value;
        for (const [name, item] of items) {
            if (item === 0n || (item & rest) !== item) continue;
            text += text === "" ? head : "|";
            text += name;
            rest &= ~item & 0xffffffffffffffffn;
        }
        return rest === 0n ? text : null;
    }

    aliasFor(aliases, path) {
        let best = -1;
        for (let i = 0; i < aliases.length; i++) {
            const target = aliases[i][1];
            const inside = path === target
                || (path.length > target.length
                    && path.slice(0, target.length) === target
                    && path[target.length] === ".");
            if (inside && (best < 0 || target.length > aliases[best][1].length)) best = i;
        }
        if (best < 0) return -1;
        const alias = aliases[best];
        return 1 + alias[0].length + path.length - alias[1].length < path.length ? best : -1;
    }

    aliasUses() {
        const out = [];
        const scopes = new Map();
        for (let h = 1n; h < BigInt(this.handleCount); h++) {
            const record = this.cell(h);
            if (record === null || this.isAlias(h) || this.isGround(h)) continue;
            if (this.typeInfo(record.type) === null) continue;
            const scope = this.scopeOf(h);
            if (scope === null) continue;
            let aliases = scopes.get(scope);
            if (aliases === undefined) {
                aliases = this.collectAliases(scope);
                scopes.set(scope, aliases);
            }
            if (aliases.names.length === 0) continue;
            const seen = new Set();
            for (const path of this.writtenPaths(h)) {
                const i = this.aliasFor(aliases.names, path);
                if (i >= 0 && !seen.has(i)) {
                    seen.add(i);
                    out.push([aliases.handles[i], h]);
                }
            }
        }
        return out;
    }

    // ---- the world: export.py's layout law, from the bytes -------------------------------------------

    headerWords() {
        return {
            version: this.version,
            body_size: this.bodySize,
            cells_off: this.cellsOff,
            index_off: this.indexOff,
            frontier: this.frontier,
            handle_count: this.handleCount,
            index_chunks: this.indexChunks,
            names_size: this.namesSize,
            header_size: this.headerSize,
        };
    }

    document() {
        const base = this.basePass();
        const [cells] = this.buildingsPass(base);
        const city = this.cityPass(base, cells);
        const edges = this.cablesPass(base);
        this.routesPass(cells, edges, city.streets, city.ring_path);
        for (const edge of edges) edge.stubs = [];
        return {
            ...this.manifest(base),
            cells,
            blocks: this.blocksPass(cells, base.regions),
            voids: base.voids,
            edges,
            bundles: [],
            streets: city.streets,
            ring: city.ring,
            trams: city.trams,
            carriers: [],
            metrics: city.metrics,
            counts: this.counts(cells, base, edges),
        };
    }

    counts(cells, base, edges) {
        const family = (name) => cells.reduce((n, cell) => n + (cell.family === name ? 1 : 0), 0);
        return {
            cells: cells.length,
            live: base.byHandle.size,
            towers: family("tower"),
            monuments: family("monument"),
            blocks: family("block"),
            utilities: family("utility"),
            rubble: family("rubble"),
            voids: base.voids.length,
            edges: edges.length,
            stale_edges: edges.reduce((n, edge) => n + (edge.fresh ? 0 : 1), 0),
            bundles: 0,
        };
    }

    manifest(base) {
        return {
            schema: SCHEMA,
            arena: {
                version: this.version,
                body_size: this.bodySize,
                cells_off: this.cellsOff,
                frontier: this.frontier,
                handle_count: this.handleCount,
                index_chunks: this.indexChunks,
                names_size: this.namesSize,
            },
            grid: {
                step: round3(GRID_STEP),
                margin: round3(GRID_MARGIN),
                district_span: DISTRICT_SPAN,
                district_depth: round3(DISTRICT_DEPTH),
                district_gap: round3(DISTRICT_GAP),
                district_cols: districtCols(base.regions.length),
                byte_scale: round3(BYTE_SCALE),
                street_width: round3(STREET_WIDTH),
            },
            regions: base.regions,
        };
    }

    // -- the base pass: districts, the stream's buckets, poison, placements ---------------------------

    basePass() {
        const header = this.headerWords();
        const stream = this.stream;
        const regions = districts(header);
        const byHandle = new Map();
        for (const record of stream) {
            if (record.handle !== null) byHandle.set(record.handle, record);
        }
        const inRegion = new Map();
        for (const record of stream) {
            const region = regionOf(regions, record.offset);
            region.cell_count += 1;
            region.empty = false;
            const key = region.id;
            if (!inRegion.has(key)) inRegion.set(key, []);
            inRegion.get(key).push(record);
        }
        const poison_runs = poisonRuns(this.bytes, this.bodySize);
        const poisoned_at = poisonHosts(stream, poison_runs);
        const placements = new Map();
        for (const region of regions) {
            const here = (inRegion.get(region.id) ?? []).slice().sort((a, b) => a.offset - b.offset);
            const [raster, plots] = tiles(here.length, region.plot.w, region.plot.d);
            region.tiles = raster;
            for (let i = 0; i < plots.length; i++) {
                const [x, z, col, row] = plots[i];
                placements.set(here[i].offset, [
                    round3(region.plot.x + x),
                    round3(region.plot.z + z),
                    col,
                    row,
                ]);
            }
        }
        return {
            header,
            body: this.bodySize,
            stream,
            regions,
            byHandle,
            poison_runs,
            poisoned_at,
            placements,
        };
    }

    // -- the buildings pass: one building per cell, and the poison pits -------------------------------

    buildingsPass(base) {
        const regions = base.regions;
        const stream = base.stream;
        const cells = [];
        const cellId = new Map();
        for (const record of stream) {
            const offset = record.offset;
            const kind = record.kind;
            let handle = record.handle;
            let family;
            let name = null;
            let path = null;
            let state = null;
            let payload;
            let identifier;
            if (handle === null) {
                family = familyOf(kind, null, -1n);
                payload = new Uint8Array(0);
                identifier = "c" + offset;
            } else {
                const got = this.get(handle);
                family = familyOf(kind, handle, got.parent);
                name = got.name === "" ? null : got.name;
                path = got.path;
                state = stateWord(record.flags);
                payload = got.payload;
                identifier = "h" + handle;
                cellId.set(handle, identifier);
            }
            const [run_state, run_checksum] = runWords(family, payload);
            let wordName;
            if (kind === KIND_BLOCK) {
                wordName = record.blockKind === 1 ? "Index" : record.blockKind === 2 ? "Names" : "Block";
            } else if (kind === KIND_DEAD) {
                wordName = "Dead";
            } else {
                wordName = word(NODE_KINDS, kind);
            }
            const [x, z, col, row] = base.placements.get(offset);
            const [footprint, height] = bulk(family, record.payloadSize);
            const region = regionOf(regions, offset);
            const poisoned = base.poisoned_at.has(offset);
            cells.push({
                id: identifier,
                handle: handle === null ? null : Number(handle),
                path,
                name,
                depth: !path ? 0 : path.split(".").length,
                kind: wordName,
                family,
                offset,
                size: record.size,
                payload_size: record.payloadSize,
                generation: record.generation,
                region: region.id,
                plot: { x, z },
                tile: { x: col, z: row },
                footprint,
                height,
                state,
                run_state,
                claim: null,
                checksum: run_checksum,
                poisoned,
                forward: record.forward,
                index: record.index,
                ref_count: record.refCount,
                anatomy: anatomy(record.size, record.payloadSize, record.refCount, family, poisoned),
            });
        }
        const cellByOffset = new Map(cells.map((cell) => [cell.offset, cell]));
        const voids = [];
        for (const [start, length] of base.poison_runs) {
            let host = cellByOffset.get(start) ?? null;
            if (host === null) {
                // the cell the run falls into (the stream is offset order: one binary search)
                let low = 0;
                let high = cells.length;
                while (low < high) {
                    const mid = (low + high) >> 1;
                    if (cells[mid].offset <= start) low = mid + 1;
                    else high = mid;
                }
                const candidate = low > 0 ? cells[low - 1] : null;
                host = candidate !== null && start < candidate.offset + candidate.size ? candidate : null;
            }
            const region = regionOf(regions, start);
            let anchor;
            let hostId;
            if (host !== null) {
                anchor = host.plot;
                host.poisoned = true;
                hostId = host.id;
            } else {
                const plot = region.plot;
                anchor = {
                    x: round3(plot.x + plot.w / 2),
                    z: round3(plot.d / 2),
                };
                hostId = null;
            }
            const sizes = [];
            for (let at = 0; at < length; at += VOID_TILE) sizes.push(Math.min(VOID_TILE, length - at));
            const cols = Math.max(1, Math.ceil(Math.sqrt(sizes.length)));
            const rows = Math.ceil(sizes.length / cols);
            let seenBytes = 0;
            for (let number = 0; number < sizes.length; number++) {
                const chunk = sizes[number];
                const col = number % cols;
                const row = Math.trunc(number / cols);
                voids.push({
                    id: "v" + voids.length,
                    offset: start + seenBytes,
                    size: chunk,
                    cell: hostId,
                    region: region.id,
                    plot: {
                        x: round3(anchor.x + (col - (cols - 1) / 2) * 2.2),
                        z: round3(anchor.z + (row - (rows - 1) / 2) * 2.2),
                    },
                    footprint: round3(Math.min(10.0, Math.max(1.2, chunk / 96.0))),
                });
                seenBytes += chunk;
            }
        }
        base.voids = voids;
        return [cells, cellId, voids];
    }

    // -- the cables: every handle reference of the image ----------------------------------------------

    cablesPass(base) {
        return this.edgesPass(base.byHandle).map((edge) => {
            const source = edge.from;
            const target = edge.to;
            edge.from_handle = Number(source);
            edge.to_handle = Number(target);
            edge.from = this.cellIdOf(base, source);
            edge.to = edge.fresh ? this.cellIdOf(base, target) : null;
            return edge;
        });
    }

    cellIdOf(base, handle) {
        return base.byHandle.has(handle) ? "h" + handle : undefined;
    }

    edgesPass(byHandle) {
        const fresh = (target) => target < BigInt(this.handleCount) && this.live(target);
        const found = [];
        const handles = [...byHandle.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
        for (const handle of handles) {
            const got = this.get(handle);
            for (const row of got.refs) {
                if (row.target !== HANDLE_NONE) {
                    found.push({
                        from: handle, to: row.target, how: "ref",
                        kind: word(REF_KINDS, row.kind), binding: word(BINDINGS, row.binding),
                        index: row.index, field: null, fresh: fresh(row.target),
                    });
                }
                if (row.role !== null) {
                    found.push({
                        from: handle, to: row.role, how: "role",
                        kind: null, binding: null,
                        index: row.index, field: null, fresh: fresh(row.role),
                    });
                }
            }
            for (const leaf of referenceSlots(this, got.type)) {
                if (leaf.offset + 8 > got.payload.length) continue;
                const record = this.cell(handle);
                const target = this.view.getBigUint64(record.offset + CELL_BYTES + leaf.offset, true);
                if (target === HANDLE_NONE) continue;
                found.push({
                    from: handle, to: target, how: "field",
                    kind: null, binding: null,
                    index: null, field: leaf.name, fresh: fresh(target),
                });
            }
        }
        for (const [alias, user] of this.aliasUses()) {
            found.push({
                from: user, to: alias, how: "alias",
                kind: null, binding: null,
                index: null, field: null, fresh: fresh(alias),
            });
        }
        const kept = found.filter((edge) => edge.from !== edge.to);
        kept.sort((a, b) => {
            if (a.from !== b.from) return a.from < b.from ? -1 : 1;
            if (a.how !== b.how) return HOWS.indexOf(a.how) - HOWS.indexOf(b.how);
            if (a.to !== b.to) return a.to < b.to ? -1 : 1;
            const ai = a.index === null ? -1 : a.index;
            const bi = b.index === null ? -1 : b.index;
            if (ai !== bi) return ai - bi;
            return (a.field ?? "") < (b.field ?? "") ? -1 : (a.field ?? "") > (b.field ?? "") ? 1 : 0;
        });
        kept.forEach((edge, number) => { edge.id = "e" + number; });
        return kept;
    }

    // -- the city: ring road, streets, trams, metrics --------------------------------------------------

    cityPass(base, cells) {
        const regions = base.regions;
        const towers = cells.filter((cell) => cell.family === "tower");
        const spanX = Math.max(1.0, ...regions.map((r) => r.plot.x + r.plot.w));
        const spanZ = Math.max(1.0, ...regions.map((r) => r.plot.z + r.plot.d));
        const center = { x: round3(spanX / 2), z: round3(spanZ / 2) };
        const ringRect = [
            round3(Math.min(...regions.map((r) => r.plot.x)) - RING_MARGIN),
            round3(Math.min(...regions.map((r) => r.plot.z)) - RING_MARGIN),
            round3(Math.max(...regions.map((r) => r.plot.x + r.plot.w)) + RING_MARGIN),
            round3(Math.max(...regions.map((r) => r.plot.z + r.plot.d)) + RING_MARGIN),
        ];
        const ring_path = ringPath(regions, cells, ringRect);
        const streets = network(regions, cells, ringRect);
        const onRoad = towers
            .filter((cell) => ON_ROAD.includes(cell.run_state))
            .sort((a, b) => a.offset - b.offset);
        const trams = onRoad.map((cell, i) => ({
            id: "t" + i,
            run: cell.id,
            angle: round3(2 * Math.PI * (i + 0.5) / Math.max(1, onRoad.length)),
            state: cell.run_state,
        }));
        const carried = towers.filter((cell) => cell.run_state !== null).map((cell) => cell.run_state);
        const ringState = carried.length === 0 ? null
            : Object.fromEntries(RUN_STATES.map((wordName) => [wordName, carried.filter((s) => s === wordName).length]));
        const verdicts = cells.filter((cell) => cell.checksum !== null).map((cell) => cell.checksum);
        let checksumStatus = null;
        if (verdicts.length > 0) {
            const bad = verdicts.filter((v) => v === "bad").length;
            checksumStatus = (verdicts.length - bad) + " ok" + (bad ? " · " + bad + " bad" : "");
        }
        return {
            towers,
            streets,
            ring_path,
            ring: {
                center,
                radius: round3(0.5 * hypot(spanX, spanZ) + RING_MARGIN),
                lanes: 1,
                state: ringState,
                occupied: trams.length,
                path: ring_path,
            },
            trams,
            metrics: {
                runs: towers.length,
                carriers: null,
                steps_per_s: null,
                claim_spins: null,
                moved_cells: cells.reduce((n, cell) => n + (cell.forward ? 1 : 0), 0),
                refusals: null,
                checksum_status: checksumStatus,
            },
        };
    }

    // -- the cable runs over the streets ---------------------------------------------------------------

    routesPass(cells, edges, streets, ringPathPoints) {
        const byId = new Map(cells.map((cell) => [cell.id, cell]));
        const rails = railList(streets, ringPathPoints);
        // the run between two lots is the Manhattan path on the street grid (streetRoute
        // below): each end snaps to its nearest rail and the turn is a crossing of two
        // streets — so every leg lies on a street centerline, never across a block.
        const xStreets = [...new Set(rails.filter((r) => r[0] === "x").map((r) => r[1]))].sort((a, b) => a - b);
        const zStreets = [...new Set(rails.filter((r) => r[0] === "z").map((r) => r[1]))].sort((a, b) => a - b);
        const nearestStreet = (streets, toward) => {
            if (!streets.length) return null;
            let best = streets[0];
            for (const at of streets) {
                const ka = Math.abs(at - toward);
                const kb = Math.abs(best - toward);
                if (ka < kb || (ka === kb && at < best)) best = at;
            }
            return best;
        };
        const grid = railGrid(rails);
        const plugs = new Map();
        for (const cell of cells) {
            plugs.set(cell.id, projectRail(rails, cell.plot.x, cell.plot.z, grid));
        }

        const streetRoute = (source, target) => {
            const [sx, sz, sRail] = source;
            const [tx, tz, tRail] = target;
            const sAxis = sRail >= 0 && sRail < rails.length ? rails[sRail][0] : null;
            const tAxis = tRail >= 0 && tRail < rails.length ? rails[tRail][0] : null;
            if (sAxis === "x" && tAxis === "z") return [[tx, sz]];
            if (sAxis === "z" && tAxis === "x") return [[sx, tz]];
            // xStreets are the z-coordinates of the horizontal (x-running) streets; zStreets the
            // x-coordinates of the vertical (z-running) ones — a turn runs on the perpendicular kind.
            if (sAxis === "x" && tAxis === "x") {
                const xm = nearestStreet(zStreets, (sx + tx) / 2);
                return xm === null ? [] : [[xm, sz], [xm, tz]];
            }
            if (sAxis === "z" && tAxis === "z") {
                const zm = nearestStreet(xStreets, (sz + tz) / 2);
                return zm === null ? [] : [[sx, zm], [tx, zm]];
            }
            return [];
        };

        const grave = (source, handle) => {
            const mixed = Number((BigInt(Math.trunc(handle)) * 2654435761n) % 4294967296n);
            const angle = (mixed / 4294967296.0) * 2 * Math.PI;
            return [source.plot.x + Math.cos(angle) * GRAVE_REACH, source.plot.z + Math.sin(angle) * GRAVE_REACH];
        };

        const pin = (cell, toward) => {
            const centerX = cell.plot.x;
            const centerZ = cell.plot.z;
            const [dx, dz] = [toward[0] - centerX, toward[1] - centerZ];
            const reach = hypot(dx, dz);
            if (reach < 1e-9) return [centerX, centerZ];
            let radius = (cell.footprint ?? 0) / 2 + PIN_MARGIN;
            radius = Math.min(radius, reach * 0.45);
            return [centerX + dx / reach * radius, centerZ + dz / reach * radius];
        };

        /* A cable on the map runs the orthogonal street grid (a 45° corner bevel is cut after).
         * Any leg that drifts off-axis — a pin that missed the rail net, a run that slid — is bent
         * onto the grid, dominant axis first, never left as a slant across the blocks. */
        const ortho = (pts) => {
            const out = [pts[0]];
            for (let i = 1; i < pts.length; i++) {
                const a = out[out.length - 1];
                const b = pts[i];
                const dx = b[0] - a[0];
                const dz = b[1] - a[1];
                if (Math.abs(dx) > 1e-6 && Math.abs(dz) > 1e-6) {
                    out.push(Math.abs(dx) >= Math.abs(dz) ? [b[0], a[1]] : [a[0], b[1]]);
                }
                out.push(b);
            }
            return out;
        };

        const routed = new Map();
        for (const edge of edges) {
            const source = byId.get(edge.from);
            if (source === undefined) {
                edge.route = [];
                continue;
            }
            const target = edge.to ? byId.get(edge.to) : null;
            let path;
            let start;
            if (target !== null && target !== undefined) {
                const key = source.id + "|" + target.id;
                if (!routed.has(key)) {
                    routed.set(key, streetRoute(plugs.get(source.id), plugs.get(target.id)));
                }
                const mid = routed.get(key);
                const sp = plugs.get(source.id);
                const tp = plugs.get(target.id);
                start = pin(source, [sp[0], sp[1]]);
                const finish = pin(target, [tp[0], tp[1]]);
                path = [start, [sp[0], sp[1]], ...mid, [tp[0], tp[1]], finish];
            } else {
                const gravePoint = grave(source, edge.to_handle);
                start = pin(source, gravePoint);
                path = [start, gravePoint];
            }
            if (path.length < 3) {
                const x0 = start[0];
                const z0 = start[1];
                const x1 = path[path.length - 1][0];
                const z1 = path[path.length - 1][1];
                const middle = roundEven((x0 + x1) / 2 / GRID_STEP) * GRID_STEP;
                path = [start, [middle, z0], [middle, z1], path[path.length - 1]];
            }
            const straight = [path[0]];
            for (const point of path.slice(1)) {
                if (Math.abs(point[0] - straight[straight.length - 1][0])
                    + Math.abs(point[1] - straight[straight.length - 1][1]) > 1e-9) {
                    straight.push(point);
                }
            }
            const tidyRun = ortho(tidy(straight));
            edge.route = chamfer(tidyRun, 0.8).map(([x, z]) => ({ x: round3(x), z: round3(z) }));
        }
    }

    // -- the city blocks: the massing the wide views read ------------------------------------------------

    blocksPass(cells, regions) {
        const regionById = new Map(regions.map((region) => [region.id, region]));
        const slot = (regionId, bx, bz) => {
            const region = regionById.get(regionId);
            const raster = region.tiles;
            const cols = raster.cols;
            const rows = raster.rows;
            const stepX = raster.step_x;
            const stepZ = raster.step_z;
            const plot = region.plot;
            const x0 = plot.x + (plot.w - stepX * cols) / 2;
            const z0 = plot.z + (plot.d - stepZ * rows) / 2;
            return [
                x0 + bx * BLOCK_BIN * stepX,
                z0 + bz * BLOCK_BIN * stepZ,
                x0 + Math.min((bx + 1) * BLOCK_BIN, cols) * stepX,
                z0 + Math.min((bz + 1) * BLOCK_BIN, rows) * stepZ,
            ];
        };
        const bins = new Map();
        for (const cell of cells) {
            const key = [cell.region, Math.trunc(cell.tile.x / BLOCK_BIN), Math.trunc(cell.tile.z / BLOCK_BIN)];
            const mapKey = key.join("|");
            if (!bins.has(mapKey)) bins.set(mapKey, { key, members: [] });
            bins.get(mapKey).members.push(cell);
        }
        const out = [];
        const ordered = [...bins.values()].sort((a, b) => {
            const [ar, ax, az] = a.key;
            const [br, bx, bz] = b.key;
            return ar !== br ? (ar < br ? -1 : 1) : ax !== bx ? ax - bx : az - bz;
        });
        for (const { key, members } of ordered) {
            const [regionId, bx, bz] = key;
            const [x0, z0, x1, z1] = slot(regionId, bx, bz);
            const family = members
                .map((member) => member.family)
                .reduce((best, f) => (FAMILIES.indexOf(f) < FAMILIES.indexOf(best) ? f : best));
            out.push({
                id: "b" + out.length,
                region: regionId,
                plot: {
                    x: round3(x0),
                    z: round3(z0),
                    w: round3(x1 - x0),
                    d: round3(z1 - z0),
                },
                height: round3(Math.max(...members.map((member) => member.height))),
                count: members.length,
                family,
            });
        }
        return out;
    }
}

// ---- the layout law as pure functions (world/export.py) ------------------------------------------------

function districtCols(count) {
    if (count <= DISTRICT_COLS * DISTRICT_COLS) return DISTRICT_COLS;
    const spanX = DISTRICT_SPAN * BYTE_SCALE + DISTRICT_GAP;
    const spanZ = DISTRICT_DEPTH + DISTRICT_GAP;
    return Math.max(DISTRICT_COLS, Math.ceil(Math.sqrt(count * spanZ / spanX)));
}

function districts(header) {
    const body = header.body_size;
    const chunks = [
        ["header", "header", 0, header.header_size],
        ["index", "index", header.index_off, header.cells_off],
    ];
    let at = header.cells_off;
    let index = 0;
    while (at < body) {
        const end = Math.min(at + DISTRICT_SPAN, body);
        chunks.push(["cells", "cells " + index, at, end]);
        at = end;
        index += 1;
    }
    const cols = districtCols(chunks.length);
    const out = [];
    let z = 0.0;
    for (let rowStart = 0; rowStart < chunks.length; rowStart += cols) {
        const row = chunks.slice(rowStart, rowStart + cols);
        let x = 0.0;
        for (let i = 0; i < row.length; i++) {
            const [band, name, start, end] = row[i];
            const width = Math.max(6.0, (end - start) * BYTE_SCALE);
            out.push({
                id: "r" + (rowStart + i),
                name,
                band,
                start,
                end,
                plot: {
                    x: round3(x),
                    z: round3(z),
                    w: round3(width),
                    d: round3(DISTRICT_DEPTH),
                },
                cell_count: 0,
                empty: true,
            });
            x += width + DISTRICT_GAP;
        }
        z += DISTRICT_DEPTH + DISTRICT_GAP;
    }
    return out;
}

function regionOf(regions, offset) {
    let low = 0;
    let high = regions.length;
    while (low < high) {
        const mid = (low + high) >> 1;
        if (regions[mid].start <= offset) low = mid + 1;
        else high = mid;
    }
    const index = low - 1;
    if (index >= 0 && offset < regions[index].end) return regions[index];
    return regions[regions.length - 1];
}

function familyOf(kind, handle, parent) {
    if (kind === KIND_DEAD) return "rubble";
    if (kind === KIND_BLOCK) return "utility";
    const name = word(NODE_KINDS, kind);
    if (name === "Fn" || name === "Code") return "tower";
    if (handle === 0n || parent === 0n || name === "Module" || name === "Namespace") return "monument";
    return "block";
}

function mass(payloadSize) {
    return Math.min(4.0, payloadSize / 512.0);
}

function bulk(family, payloadSize) {
    const m = mass(payloadSize);
    const base = round3(FP_RATIO * GRID_STEP);
    if (family === "tower") return [base, round3(8.0 + 5.0 * m)];
    if (family === "monument") return [base, round3(4.0 + 3.0 * m)];
    if (family === "utility") return [base, 1.6];
    if (family === "rubble") return [base, round3(0.4 + 0.3 * m)];
    return [base, round3(1.8 + 5.0 * m)];
}

function runWords(family, payload) {
    if (family !== "tower" || payload.length !== RUN_WORDS_BYTES) return [null, null];
    const state = new DataView(payload.buffer, payload.byteOffset, payload.byteLength).getUint32(0, true);
    const runState = state < RUN_STATES.length ? RUN_STATES[state] : null;
    if (runState === null) return [null, null];
    const programAt = RUN_PAYLOAD_REGIONS[0][1] + RUN_PAYLOAD_REGIONS[1][1];
    const checksumAt = RUN_WORDS_BYTES - 8;
    const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
    const stored = view.getBigUint64(checksumAt, true);
    const verdict = stored === fnv1a(payload, programAt, checksumAt) ? "ok" : "bad";
    return [runState, verdict];
}

function zipOffsets(rows, start) {
    const out = [];
    let at = start;
    for (const [name, size] of rows) {
        out.push([name, size, at]);
        at += size;
    }
    return out;
}

function splitRegions(offset, size) {
    const weights = RUN_PAYLOAD_REGIONS.map(([, weight]) => weight);
    const total = weights.reduce((a, b) => a + b, 0);
    const exact = weights.map((weight) => size * weight / total);
    const parts = exact.map((part) => Math.trunc(part));
    let left = size - parts.reduce((a, b) => a + b, 0);
    const order = exact.map((part, i) => [-(part - parts[i]), i])
        .sort((a, b) => (a[0] !== b[0] ? a[0] - b[0] : a[1] - b[1]))
        .map((entry) => entry[1]);
    for (let k = 0; k < left; k++) parts[order[k]] += 1;
    return zipOffsets(RUN_PAYLOAD_REGIONS.map(([name], i) => [name, parts[i]]), offset)
        .filter(([, size]) => size > 0)
        .map(([role, size, at]) => ({ role, offset: at, size }));
}

function anatomy(cellSize, payloadSize, refCount, family, poisoned) {
    const payloadBand = CELL_ALIGN * Math.ceil(payloadSize / CELL_ALIGN);
    const payloadOffset = CELL_BYTES;
    const rows = zipOffsets(HEADER_WORDS, 0).map(([name, size, at]) => ({ role: name, offset: at, size }));
    let at = payloadOffset;
    if (poisoned) {
        rows.push({ role: "poison", offset: at, size: payloadSize });
    } else if (family === "tower" && payloadSize >= 32) {
        rows.push(...splitRegions(at, payloadSize));
    } else if (payloadSize) {
        rows.push({ role: "payload", offset: at, size: payloadSize });
    }
    at += payloadSize;
    if (payloadBand > payloadSize) {
        rows.push({ role: "pad", offset: at, size: payloadBand - payloadSize });
        at += payloadBand - payloadSize;
    }
    const refBytes = REF_BYTES * refCount;
    if (refBytes) {
        rows.push({ role: "refrows", offset: at, size: refBytes });
        at += refBytes;
    }
    const tail = CELL_ALIGN * Math.ceil(at / CELL_ALIGN) - at;
    if (tail) rows.push({ role: "pad", offset: at, size: tail });
    let end = rows[rows.length - 1].offset + rows[rows.length - 1].size;
    if (end < cellSize) {
        rows.push({ role: "pad", offset: end, size: cellSize - end });
    } else if (end > cellSize) {
        let overflow = end - cellSize;
        for (let i = rows.length - 1; i >= 0 && overflow > 0; i--) {
            const take = Math.min(rows[i].size, overflow);
            rows[i].size -= take;
            overflow -= take;
        }
        return rows.filter((band) => band.size > 0);
    }
    return rows;
}

function tiles(count, width, depth) {
    const cols = Math.max(1, Math.floor((width - 2 * GRID_MARGIN) / GRID_STEP));
    let colsNow = cols;
    let rowsNow = Math.max(1, Math.floor((depth - 2 * GRID_MARGIN) / GRID_STEP));
    while (colsNow * rowsNow < count) {
        if (colsNow <= rowsNow) colsNow += 1;
        else rowsNow += 1;
    }
    const raster = {
        cols: colsNow,
        rows: rowsNow,
        step_x: round3(GRID_STEP),
        step_z: round3(GRID_STEP),
    };
    if (count <= 0) return [raster, []];
    const spanX = GRID_STEP * colsNow;
    const spanZ = GRID_STEP * rowsNow;
    const usedCols = Math.min(colsNow, count);
    const usedRows = Math.ceil(count / usedCols);
    const startCol = Math.trunc((colsNow - usedCols) / 2);
    const startRow = Math.trunc((rowsNow - usedRows) / 2);
    const plots = [];
    for (let i = 0; i < count; i++) {
        const col = startCol + (i % usedCols);
        const row = startRow + Math.trunc(i / usedCols);
        plots.push([
            round3((width - spanX) / 2 + (col + 0.5) * GRID_STEP),
            round3((depth - spanZ) / 2 + (row + 0.5) * GRID_STEP),
            col,
            row,
        ]);
    }
    return [raster, plots];
}

function bandsOf(runs) {
    const sorted = [...runs].sort((a, b) => (a[0] !== b[0] ? a[0] - b[0] : a[1] - b[1]));
    const merged = [];
    for (const [low, high] of sorted) {
        if (merged.length && low <= merged[merged.length - 1][1] + 1e-6) {
            merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], high);
        } else {
            merged.push([low, high]);
        }
    }
    return merged;
}

function street(kind, axis, at, start, end, width) {
    return {
        kind,
        axis,
        at: round3(at),
        from: round3(Math.min(start, end)),
        to: round3(Math.max(start, end)),
        width: round3(width),
    };
}

function network(regions, cells, ringRect) {
    const [ringX0, ringZ0, ringX1, ringZ1] = ringRect;
    const width = (gap) => Math.min(2 * STREET_WIDTH, Math.max(1.8, gap * 0.7));
    const inRegion = new Map();
    for (const cell of cells) {
        if (!inRegion.has(cell.region)) inRegion.set(cell.region, []);
        inRegion.get(cell.region).push(cell);
    }
    const out = [];
    const zBands = bandsOf(regions.map((region) => [region.plot.z, region.plot.z + region.plot.d]));
    for (let i = 0; i + 1 < zBands.length; i++) {
        const [first, second] = [zBands[i], zBands[i + 1]];
        const gap = second[0] - first[1];
        if (gap >= 1.2) {
            out.push(street("avenue", "x", (first[1] + second[0]) / 2, ringX0, ringX1, width(gap)));
        }
    }
    for (let index = 0; index < zBands.length; index++) {
        const [low, high] = zBands[index];
        const here = regions.filter((region) => region.plot.z < high - 1e-6 && region.plot.z + region.plot.d > low + 1e-6);
        const zFrom = index === 0 ? ringZ0 : (zBands[index - 1][1] + low) / 2;
        const zTo = index === zBands.length - 1 ? ringZ1 : (high + zBands[index + 1][0]) / 2;
        const xBands = bandsOf(here.map((region) => [region.plot.x, region.plot.x + region.plot.w]));
        for (let i = 0; i + 1 < xBands.length; i++) {
            const [first, second] = [xBands[i], xBands[i + 1]];
            const gap = second[0] - first[1];
            if (gap >= 1.2) {
                out.push(street("cross", "z", (first[1] + second[0]) / 2, zFrom, zTo, width(gap)));
            }
        }
    }
    const avenues = out.filter((s) => s.kind === "avenue").map((s) => s.at).sort((a, b) => a - b);
    const crosses = out.filter((s) => s.kind === "cross").map((s) => s.at).sort((a, b) => a - b);
    for (const region of regions) {
        const here = inRegion.get(region.id) ?? [];
        if (here.length === 0) continue;
        const plot = region.plot;
        const [px, pz, pw, pd] = [plot.x, plot.z, plot.w, plot.d];
        const regionLanes = [];
        for (const axis of ["x", "z"]) {
            const [lo, span] = axis === "x" ? [pz, pd] : [px, pw];
            const runs = here.map((cell) => axis === "x"
                ? [cell.plot.z - cell.footprint / 2, cell.plot.z + cell.footprint / 2]
                : [cell.plot.x - cell.footprint / 2, cell.plot.x + cell.footprint / 2]);
            const bands = bandsOf(runs);
            const edges = [[lo, bands[0][0]]];
            for (let i = 0; i + 1 < bands.length; i++) edges.push([bands[i][1], bands[i + 1][0]]);
            edges.push([bands[bands.length - 1][1], lo + span]);
            for (const [low, high] of edges) {
                const gap = high - low;
                if (gap < LANE_MIN) continue;
                const at = (low + high) / 2;
                const laneWidth = Math.min(2 * LANE_WIDTH, Math.max(0.9, gap - 0.5));
                regionLanes.push(axis === "x"
                    ? street("lane", "x", at, px + 0.2, px + pw - 0.2, laneWidth)
                    : street("lane", "z", at, pz + 0.2, pz + pd - 0.2, laneWidth));
            }
        }
        out.push(...regionLanes);
        const xLanes = regionLanes.filter((s) => s.axis === "z").map((s) => s.at).sort((a, b) => a - b);
        const zLanes = regionLanes.filter((s) => s.axis === "x").map((s) => s.at).sort((a, b) => a - b);
        if (xLanes.length) {
            const at = xLanes.reduce((best, value) => (Math.abs(value - (px + pw / 2)) < Math.abs(best - (px + pw / 2)) ? value : best));
            const laneWidth = regionLanes.find((s) => s.axis === "z" && s.at === at).width;
            const north = avenues.find((value) => value > pz + pd) ?? ringZ1;
            const south = [...avenues].reverse().find((value) => value < pz) ?? ringZ0;
            out.push(street("branch", "z", at, pz + pd, north, laneWidth));
            out.push(street("branch", "z", at, south, pz, laneWidth));
        }
        if (zLanes.length) {
            const at = zLanes.reduce((best, value) => (Math.abs(value - (pz + pd / 2)) < Math.abs(best - (pz + pd / 2)) ? value : best));
            const laneWidth = regionLanes.find((s) => s.axis === "x" && s.at === at).width;
            const east = crosses.find((value) => value > px + pw) ?? ringX1;
            const west = [...crosses].reverse().find((value) => value < px) ?? ringX0;
            out.push(street("branch", "x", at, px + pw, east, laneWidth));
            out.push(street("branch", "x", at, west, px, laneWidth));
        }
    }
    return out.sort((a, b) => (a.kind !== b.kind ? (a.kind < b.kind ? -1 : 1)
        : a.axis !== b.axis ? (a.axis < b.axis ? -1 : 1) : a.at - b.at));
}

function poisonRuns(raw, body) {
    const runs = [];
    let at = 0;
    while (at < body) {
        if (raw[at] !== POISON_BYTE) {
            at += 1;
            continue;
        }
        let end = at + 1;
        while (end < body && raw[end] === POISON_BYTE) end += 1;
        if (end - at >= POISON_RUN) runs.push([at, end - at]);
        at = end;
    }
    return runs;
}

function poisonHosts(stream, runs) {
    const hosts = new Set();
    for (const [start] of runs) {
        let low = 0;
        let high = stream.length;
        while (low < high) {
            const mid = (low + high) >> 1;
            if (stream[mid].offset <= start) low = mid + 1;
            else high = mid;
        }
        const candidate = low > 0 ? stream[low - 1] : null;
        if (candidate !== null
            && (candidate.offset === start || start < candidate.offset + candidate.size)) {
            hosts.add(candidate.offset);
        }
    }
    return hosts;
}

function keepOut(cell) {
    const half = cell.footprint / 2;
    const [x, z] = [cell.plot.x, cell.plot.z];
    return [x - half - CLEARANCE, z - half - CLEARANCE, x + half + CLEARANCE, z + half + CLEARANCE];
}

function segmentHits(a, b, rect) {
    const [x0, z0, x1, z1] = rect;
    let t0 = 0.0;
    let t1 = 1.0;
    for (const [p, q, low, high] of [[a[0], b[0] - a[0], x0, x1], [a[1], b[1] - a[1], z0, z1]]) {
        if (Math.abs(q) < 1e-12) {
            if (p < low || p > high) return null;
            continue;
        }
        let [ta, tb] = [(low - p) / q, (high - p) / q];
        if (ta > tb) [ta, tb] = [tb, ta];
        t0 = Math.max(t0, ta);
        t1 = Math.min(t1, tb);
        if (t1 <= t0) return null;
    }
    return [t0, t1];
}

function mergeRects(rects, touch = 0.6) {
    let boxes = [...rects].map((rect) => [...rect]).sort((a, b) => (
        a[0] !== b[0] ? a[0] - b[0] : a[1] !== b[1] ? a[1] - b[1] : a[2] !== b[2] ? a[2] - b[2] : a[3] - b[3]
    ));
    let merged = true;
    while (merged) {
        merged = false;
        const out = [];
        const cell = Math.max(1.0, 2.0 * touch);
        const near = new Map();
        const cellsOf = (box) => {
            const keys = [];
            for (let gx = Math.floor((box[0] - touch) / cell); gx <= Math.floor((box[2] + touch) / cell); gx++) {
                for (let gz = Math.floor((box[1] - touch) / cell); gz <= Math.floor((box[3] + touch) / cell); gz++) {
                    keys.push(gx + "," + gz);
                }
            }
            return keys;
        };
        for (const box of boxes) {
            const candidates = [...new Set(cellsOf(box).flatMap((key) => near.get(key) ?? []))].sort((a, b) => a - b);
            let hit = false;
            for (const index of candidates) {
                const other = out[index];
                const apart = box[0] > other[2] + touch
                    || other[0] > box[2] + touch
                    || box[1] > other[3] + touch
                    || other[1] > box[3] + touch;
                if (!apart) {
                    other[0] = Math.min(other[0], box[0]);
                    other[1] = Math.min(other[1], box[1]);
                    other[2] = Math.max(other[2], box[2]);
                    other[3] = Math.max(other[3], box[3]);
                    merged = true;
                    hit = true;
                    for (const key of cellsOf(other)) {
                        if (!near.has(key)) near.set(key, []);
                        near.get(key).push(index);
                    }
                    break;
                }
            }
            if (!hit) {
                out.push(box);
                const index = out.length - 1;
                for (const key of cellsOf(box)) {
                    if (!near.has(key)) near.set(key, []);
                    near.get(key).push(index);
                }
            }
        }
        boxes = out;
    }
    return boxes;
}

// Python's % on floats (the sign of the divisor); the detour math reads perimeters that way.
function pythonMod(a, b) {
    return ((a % b) + b) % b;
}

function detour(loop, rects, center, roadHalf) {
    void center;
    let points = [...loop];
    const sortedRects = [...rects].sort((a, b) => (
        a[0] !== b[0] ? a[0] - b[0] : a[1] !== b[1] ? a[1] - b[1] : a[2] !== b[2] ? a[2] - b[2] : a[3] - b[3]
    ));
    for (const rect of sortedRects) {
        const corridor = [
            rect[0] - roadHalf - RING_BEVEL - 0.3,
            rect[1] - roadHalf - RING_BEVEL - 0.3,
            rect[2] + roadHalf + RING_BEVEL + 0.3,
            rect[3] + roadHalf + RING_BEVEL + 0.3,
        ];
        const detect = [corridor[0] + 0.2, corridor[1] + 0.2, corridor[2] - 0.2, corridor[3] - 0.2];
        for (let pass = 0; pass < 2; pass++) {
            const outside = (p) => !(detect[0] <= p[0] && p[0] <= detect[2] && detect[1] <= p[1] && p[1] <= detect[3]);
            const start = points.findIndex(outside);
            if (start < 0 || points.length > 1200) break;
            points = points.slice(start).concat(points.slice(0, start));
            const hits = [];
            for (let i = 0; i < points.length; i++) {
                const a = points[i];
                const b = points[(i + 1) % points.length];
                const span = hypot(b[0] - a[0], b[1] - a[1]);
                const hit = span > 1e-9 ? segmentHits(a, b, detect) : null;
                if (hit) {
                    hits.push([i, hit[0], true]);
                    hits.push([i, hit[1], false]);
                }
            }
            if (hits.length < 2 || hits.length % 2) break;
            for (let pair = Math.trunc(hits.length / 2) - 1; pair >= 0; pair--) {
                const [enterI, enterT] = [hits[2 * pair][0], hits[2 * pair][1]];
                const [exitI, exitT] = [hits[2 * pair + 1][0], hits[2 * pair + 1][1]];
                if (exitI < enterI || (exitI === enterI && exitT <= enterT)) continue;
                const lerp = (i, t) => {
                    const a = points[i];
                    const b = points[(i + 1) % points.length];
                    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
                };
                const ex = lerp(enterI, enterT);
                const xi = lerp(exitI, exitT);
                const width = corridor[2] - corridor[0];
                const depth = corridor[3] - corridor[1];
                const half = 2 * (width + depth);
                const corners = [
                    [[corridor[0], corridor[1]], 0.0],
                    [[corridor[2], corridor[1]], width],
                    [[corridor[2], corridor[3]], width + depth],
                    [[corridor[0], corridor[3]], 2 * width + depth],
                ];
                const perimeter = (point) => Math.min(
                    [Math.abs(point[1] - corridor[1]), point[0] - corridor[0]],
                    [Math.abs(point[0] - corridor[2]), width + point[1] - corridor[1]],
                    [Math.abs(point[1] - corridor[3]), width + depth + corridor[2] - point[0]],
                    [Math.abs(point[0] - corridor[0]), 2 * width + depth + corridor[3] - point[1]],
                )[1];
                const sEx = pythonMod(perimeter(ex), half);
                const sXi = pythonMod(perimeter(xi), half);
                const forward = [];
                const backward = [];
                for (let k = 0; k < corners.length; k++) {
                    const s = corners[k][1];
                    const inForward = pythonMod(s - sEx, half) < pythonMod(sXi - sEx, half) - 1e-9;
                    (inForward ? forward : backward).push(k);
                }
                let arc;
                if (forward.length <= backward.length) {
                    forward.sort((a, b) => pythonMod(corners[a][1] - sEx, half) - pythonMod(corners[b][1] - sEx, half));
                    arc = forward.map((k) => corners[k][0]);
                } else {
                    backward.sort((a, b) => pythonMod(sEx - corners[a][1], half) - pythonMod(sEx - corners[b][1], half));
                    arc = backward.map((k) => corners[k][0]);
                }
                const tail = exitT >= 1 - 1e-9 ? [] : [xi];
                const head = enterT <= 1e-9 ? [] : [ex];
                points = points.slice(0, enterI + 1).concat(head, arc, tail, points.slice(exitI + 1));
            }
        }
    }
    return points;
}

function lean(points) {
    let pts = [];
    for (const point of points) {
        if (pts.length === 0 || hypot(point[0] - pts[pts.length - 1][0], point[1] - pts[pts.length - 1][1]) > 0.05) {
            pts.push(point);
        }
    }
    for (let pass = 0; pass < 8; pass++) {
        const count = pts.length;
        if (count <= 4) break;
        let dropped = false;
        const kept = [];
        for (let index = 0; index < count; index++) {
            const previous = pts[index - 1 < 0 ? count - 1 : index - 1];
            const point = pts[index];
            const following = pts[(index + 1) % count];
            const leg = Math.min(
                hypot(point[0] - previous[0], point[1] - previous[1]),
                hypot(following[0] - point[0], following[1] - point[1]),
            );
            const segment = [following[0] - previous[0], following[1] - previous[1]];
            const length = hypot(segment[0], segment[1]) || 1.0;
            const off = Math.abs(segment[0] * (point[1] - previous[1]) - segment[1] * (point[0] - previous[0])) / length;
            if (leg < 0.6 || off < 0.08) {
                dropped = true;
                continue;
            }
            kept.push(point);
        }
        if (!dropped || kept.length < 4) break;
        pts = kept;
    }
    return pts;
}

function chamferClosed(points, radius) {
    const pts = lean(points);
    const out = [];
    const count = pts.length;
    for (let index = 0; index < count; index++) {
        const previous = pts[(index - 1 + count) % count];
        const point = pts[index];
        const following = pts[(index + 1) % count];
        const [ux, uz] = [point[0] - previous[0], point[1] - previous[1]];
        const [vx, vz] = [following[0] - point[0], following[1] - point[1]];
        const uLength = hypot(ux, uz) || 1.0;
        const vLength = hypot(vx, vz) || 1.0;
        if ((ux * vx + uz * vz) / (uLength * vLength) > Math.cos(12 * Math.PI / 180)) continue;
        const r = Math.min(radius, 0.45 * uLength, 0.45 * vLength);
        out.push([point[0] - (ux / uLength) * r, point[1] - (uz / uLength) * r]);
        out.push([point[0] + (vx / vLength) * r, point[1] + (vz / vLength) * r]);
    }
    return out.length >= 4 ? out : pts;
}

function cornerCut(cells, ringRect) {
    const [x0, z0, x1, z1] = ringRect;
    const reach = RING_WIDTH / 2 + CLEARANCE;
    let cut = Math.min(18.0, 0.34 * Math.min(x1 - x0, z1 - z0));
    for (const [cx, cz] of [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]) {
        for (const cell of cells) {
            const u = Math.abs(cell.plot.x - cx);
            const v = Math.abs(cell.plot.z - cz);
            cut = Math.min(cut, u + v - Math.sqrt(2) * (cell.footprint / 2 + reach));
        }
    }
    return Math.max(1.5, cut);
}

function ringPath(regions, cells, ringRect) {
    const [x0, z0, x1, z1] = ringRect;
    const corner = cornerCut(cells, ringRect);
    const basis = [
        [x0 + corner, z0],
        [x1 - corner, z0],
        [x1, z0 + corner],
        [x1, z1 - corner],
        [x1 - corner, z1],
        [x0 + corner, z1],
        [x0, z1 - corner],
        [x0, z0 + corner],
    ];
    const rects = mergeRects(cells.map(keepOut), 1.2);
    const center = [(x0 + x1) / 2, (z0 + z1) / 2];
    let routed = basis;
    for (let pass = 0; pass < 5; pass++) {
        const clear = rects.every((rect) => routed.every((first, i) => segmentHits(first, routed[(i + 1) % routed.length], rect) === null));
        if (clear) break;
        routed = detour(routed, rects, center, RING_WIDTH / 2);
    }
    return chamferClosed(routed, RING_BEVEL).map(([x, z]) => ({ x: round3(x), z: round3(z) }));
}

function referenceSlots(reader, typeHandle) {
    if (typeHandle === HANDLE_NONE || reader.type_name(typeHandle) !== "Type") return [];
    return reader.layout(typeHandle).leaves.filter((leaf) => leaf.reference);
}

function chamfer(points, radius) {
    if (points.length < 3) return [...points];
    const out = [points[0]];
    for (let i = 1; i < points.length - 1; i++) {
        const previous = points[i - 1];
        const point = points[i];
        const following = points[i + 1];
        const [ux, uz] = [point[0] - previous[0], point[1] - previous[1]];
        const [vx, vz] = [following[0] - point[0], following[1] - point[1]];
        const uLength = hypot(ux, uz) || 1.0;
        const vLength = hypot(vx, vz) || 1.0;
        const r = Math.min(radius, uLength / 2, vLength / 2);
        out.push([point[0] - (ux / uLength) * r, point[1] - (uz / uLength) * r]);
        out.push([point[0] + (vx / vLength) * r, point[1] + (vz / vLength) * r]);
    }
    out.push(points[points.length - 1]);
    return out;
}

function tidy(points) {
    let out = [];
    for (const point of points) {
        const last = out[out.length - 1];
        if (out.length === 0 || Math.abs(point[0] - last[0]) + Math.abs(point[1] - last[1]) > 1e-9) {
            out.push([point[0], point[1]]);
        }
    }
    let changed = true;
    while (changed && out.length > 2) {
        changed = false;
        const merged = [out[0]];
        for (let index = 1; index < out.length - 1; index++) {
            const previous = merged[merged.length - 1];
            const point = out[index];
            const following = out[index + 1];
            const [ux, uz] = [point[0] - previous[0], point[1] - previous[1]];
            const [vx, vz] = [following[0] - point[0], following[1] - point[1]];
            const uLength = hypot(ux, uz);
            const vLength = hypot(vx, vz);
            if (uLength < 1e-9) continue;
            const cross = Math.abs(ux * vz - uz * vx);
            if (cross <= 1e-9 * Math.max(1.0, uLength * vLength) && ux * vx + uz * vz > 0) continue;
            if (Math.min(uLength, vLength) < MICRO_LEG) continue;
            merged.push(point);
        }
        merged.push(out[out.length - 1]);
        if (merged.length !== out.length) changed = true;
        out = merged;
    }
    return out;
}

function railList(streets, ringPathPoints) {
    const rails = streets.map((s) => [s.axis, s.at, s.from, s.to]);
    for (let i = 0; i < ringPathPoints.length; i++) {
        const a = ringPathPoints[i];
        const b = ringPathPoints[(i + 1) % ringPathPoints.length];
        if (Math.abs(a.x - b.x) < 1e-6) {
            rails.push(["z", a.x, Math.min(a.z, b.z), Math.max(a.z, b.z)]);
        } else if (Math.abs(a.z - b.z) < 1e-6) {
            rails.push(["x", a.z, Math.min(a.x, b.x), Math.max(a.x, b.x)]);
        }
    }
    const xLo = Math.min(...rails.filter((r) => r[0] === "x").map((r) => Math.min(r[2], r[3])));
    const xHi = Math.max(...rails.filter((r) => r[0] === "x").map((r) => Math.max(r[2], r[3])));
    const zLo = Math.min(...rails.filter((r) => r[0] === "z").map((r) => Math.min(r[2], r[3])));
    const zHi = Math.max(...rails.filter((r) => r[0] === "z").map((r) => Math.max(r[2], r[3])));
    const clipped = [];
    for (const [axis, at, low, high] of rails) {
        const [lo, hi] = axis === "z" ? [zLo, zHi] : [xLo, xHi];
        const clip = [axis, at, Math.max(low, lo), Math.min(high, hi)];
        if (clip[2] < clip[3]) clipped.push(clip);
    }
    return clipped;
}

function projectRail(rails, x, z, grid = null) {
    if (rails.length === 0) return [x, z, -1];
    const scan = grid === null ? rails.map((_, i) => i) : nearRails(rails, grid, x, z);
    let best = [x, z, -1];
    let bestSquared = Infinity;
    for (const r of scan) {
        const [axis, at, low, high] = rails[r];
        const [px, pz] = axis === "x" ? [Math.min(Math.max(x, low), high), at] : [at, Math.min(Math.max(z, low), high)];
        const squared = (px - x) ** 2 + (pz - z) ** 2;
        if (squared < bestSquared - 1e-12) {
            bestSquared = squared;
            best = [px, pz, r];
        }
    }
    return best;
}

function railGrid(rails) {
    const bins = new Map();
    for (let r = 0; r < rails.length; r++) {
        const [axis, at, low, high] = rails[r];
        const [x0, x1] = axis === "x" ? [low, high] : [at, at];
        const [z0, z1] = axis === "x" ? [at, at] : [low, high];
        for (let gx = Math.floor(x0 / PROJECT_BIN); gx <= Math.floor(x1 / PROJECT_BIN); gx++) {
            for (let gz = Math.floor(z0 / PROJECT_BIN); gz <= Math.floor(z1 / PROJECT_BIN); gz++) {
                const key = gx + "," + gz;
                if (!bins.has(key)) bins.set(key, []);
                bins.get(key).push(r);
            }
        }
    }
    return bins;
}

function nearRails(rails, grid, x, z) {
    const home = [Math.floor(x / PROJECT_BIN), Math.floor(z / PROJECT_BIN)];
    const found = new Set();
    const out = [];
    let bestSquared = Infinity;
    for (let ring = 0; ; ring++) {
        for (let gx = home[0] - ring; gx <= home[0] + ring; gx++) {
            for (let gz = home[1] - ring; gz <= home[1] + ring; gz++) {
                if (ring && Math.max(Math.abs(gx - home[0]), Math.abs(gz - home[1])) !== ring) continue;
                for (const r of grid.get(gx + "," + gz) ?? []) {
                    if (found.has(r)) continue;
                    found.add(r);
                    out.push(r);
                    const [axis, at, low, high] = rails[r];
                    const [px, pz] = axis === "x" ? [Math.min(Math.max(x, low), high), at] : [at, Math.min(Math.max(z, low), high)];
                    bestSquared = Math.min(bestSquared, (px - x) ** 2 + (pz - z) ** 2);
                }
            }
        }
        if (out.length && (Math.max(0, ring - 1) * PROJECT_BIN) ** 2 > bestSquared + 1e-9) break;
    }
    return out;
}

// ---- the one face the page calls ---------------------------------------------------------------------

// The world of one arena's bytes: the same scene document `export_world` writes — minus the
// duct packing the reader never does (``bundles: []``, every edge's ``stubs: []``).
export function readWorld(bytes) {
    return new RxfReader(bytes).document();
}


function scalarKind(info) {
    switch (info.form) {
        case FORM_ENUM: return "enum";
        case 3: return "uint";
        case 4: return "sint";
        case 2: return "bool";
        case 5: return "float";
        default: return "hex";
    }
}

function isGroundName(name) {
    return name === "uint32_t" || name === "uint64_t" || name === "Enumerator" || name === "Doc"
        || ["Form", "TypeFlags", "FieldFlags", "NodeKind", "RefKind", "Binding", "OwnerKind",
            "Mobility", "DocKind", "DocStatus", "DocCodec", "DocRole"].includes(name);
}

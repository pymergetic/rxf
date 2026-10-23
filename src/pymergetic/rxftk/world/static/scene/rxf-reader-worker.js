// The reader in its own thread: the world laid out from raw .rxfb bytes while the page keeps
// breathing — the loading face repaints and the clock ticks through the read instead of the
// main thread freezing in the dark. One message in (the arena bytes), one message out (the
// scene document rxf-reader.js computes — the exporter's client face, pinned by test_reader_js).
import { readWorld } from "./rxf-reader.js";

self.onmessage = (event) => {
    try {
        self.postMessage({ ok: true, doc: readWorld(new Uint8Array(event.data)) });
    } catch (error) {
        self.postMessage({ ok: false, error: String((error && error.stack) || error) });
    }
};

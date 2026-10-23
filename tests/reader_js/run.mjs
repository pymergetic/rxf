// Read a world image the way the browser does and print its scene document.
//
// usage: node run.mjs <path to rxf-reader.js> <path to .rxfb> [out.json]
//
// This is the client half of test_reader_js: the Python test writes the demo
// bytes, this reads them back with the same reader file the web page loads.
// The reader is a self-contained ES module, so it is imported by content, not
// by file extension (a plain .js is a CommonJS module to Node).
import { readFileSync, writeFileSync } from "node:fs";

const [readerPath, imagePath, outPath] = process.argv.slice(2);
const source = readFileSync(readerPath, "utf8");
const reader = await import(
    "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);
const bytes = new Uint8Array(readFileSync(imagePath));
const document = reader.readWorld(bytes);
if (outPath) {
    writeFileSync(outPath, JSON.stringify(document, null, 1) + "\n");
} else {
    process.stdout.write(JSON.stringify(document));
}

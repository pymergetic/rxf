#ifndef PYMERGETIC_RXF_LIVE_HPP
#define PYMERGETIC_RXF_LIVE_HPP

/*
 * The live layer: the part of RXF that runs inside a running image and lets the image be read,
 * checked and changed from the inside (docs/19). It is freestanding like the arena: no heap, no
 * exceptions, no C library, no recursion, no function pointers. The same source runs in the host
 * module (parity tests against object::Image) and is compiled to the native leaf `rxf_live_pump`
 * of the linked image on x86_64, aarch64 and wasm32.
 *
 * One call serves the requests that wait in the rx ring (up to a limit) and writes the answers
 * to the tx ring. `context` is the runtime context of the linked image (or null): when it is there,
 * every change of the image ends with the image linking itself again. Nothing is kept between two calls except what the live area holds.
 */

#include <pymergetic/rxftk/core/live/live.h>

#include <stdint.h>

namespace pymergetic::rxf::core::live {

/* Serves up to `limit` frames; returns how many it served. Frames of a damaged stream are
 * skipped byte by byte and count as nothing. When the tx ring has no room for an answer, the
 * request stays where it is. */
uint32_t pump(pymergetic_rxf_live_area *area, uint32_t limit, void *context = nullptr, bool relocatable = false);

/* CRC-32 (IEEE 802.3, reflected, init and final xor 0xFFFFFFFF). */
uint32_t crc32(const void *data, uint64_t size);

} // namespace pymergetic::rxf::core::live

#endif

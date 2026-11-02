/* arch/cc.h -- the compiler/platform glue of the lwIP port (docs/23 4.4).
 *
 * LP64 little endian, freestanding: lwIP's own types come from <stdint.h>/<stddef.h>
 * (compiler headers, no libc), the diagnostic macros are empty (the card has no console
 * of its own: a diagnostic is a fact in the image, not a printf), and an assertion is the
 * unreachable of the importer's panic-path idiom.
 */
#ifndef RXF_LWIP_ARCH_CC_H
#define RXF_LWIP_ARCH_CC_H

#include <stdint.h>

#define LWIP_NO_INTTYPES_H 1
#define LWIP_NO_LIMITS_H 0
#define LWIP_NO_CTYPE_H 1

#define BYTE_ORDER LITTLE_ENDIAN

#define X8_F "02x"
#define U16_F "u"
#define S16_F "d"
#define X16_F "x"
#define U32_F "u"
#define S32_F "d"
#define X32_F "x"
#define SZT_F "zu"

#define LWIP_PLATFORM_DIAG(x) ((void)0)
#define LWIP_PLATFORM_ASSERT(x) __builtin_trap()

#define PACK_STRUCT_FIELD(x) x
#define PACK_STRUCT_STRUCT __attribute__((packed))
#define PACK_STRUCT_BEGIN
#define PACK_STRUCT_END

#endif /* RXF_LWIP_ARCH_CC_H */

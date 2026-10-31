/* core_slice.c -- the lwIP core of the first slice as ONE translation unit (docs/23 4.4).
 *
 * The importer lowers a module at a time, and a module is one translation unit: the
 * globals of lwIP (``ip_data``, ``netif_list``, ``ip_addr_any``, the pbuf pools) are defined
 * in one .c file and used in the next, so the card is the union of the files below with
 * their globals defined in the one module -- exactly what a link of them would have, and
 * nothing more. The upstream files are included verbatim from third_party/lwip (never
 * copied, never edited); this file is only the list.
 *
 * Slice 1 (NO_SYS=1, IPv4 + UDP + ICMP over a SLIP netif, no TCP, no sockets):
 *   core:   init, def, mem, memp, pbuf, netif, ip, inet_chksum, timeouts, sys, stats, udp
 *   ipv4:   ip4, ip4_addr, icmp
 *   netif:  slipif
 *   port:   sys_arch (the clock), slip_line_face (the sio face over the line cell)
 */
#include "../../third_party/lwip/src/core/def.c"
#include "../../third_party/lwip/src/core/inet_chksum.c"
#include "../../third_party/lwip/src/core/init.c"
#include "../../third_party/lwip/src/core/ip.c"
#include "../../third_party/lwip/src/core/ipv4/icmp.c"
#include "../../third_party/lwip/src/core/ipv4/ip4.c"
#include "../../third_party/lwip/src/core/ipv4/ip4_addr.c"
#include "../../third_party/lwip/src/core/mem.c"
#include "../../third_party/lwip/src/core/memp.c"
#include "../../third_party/lwip/src/core/netif.c"
#include "../../third_party/lwip/src/core/pbuf.c"
#include "../../third_party/lwip/src/core/stats.c"
#include "../../third_party/lwip/src/core/sys.c"
#include "../../third_party/lwip/src/core/timeouts.c"
#include "../../third_party/lwip/src/core/udp.c"
#include "../../third_party/lwip/src/netif/slipif.c"
#include "slip_line_face.c"
#include "sys_arch.c"

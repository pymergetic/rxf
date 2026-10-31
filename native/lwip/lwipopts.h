/* lwipopts.h -- the options of the lwIP card, first slice (docs/23 4.4).
 *
 * The smallest honest core: NO_SYS=1 (the run-cell machine is the only scheduler -- there is
 * no tcpip thread, no mailbox, no semaphore), IPv4 + UDP + ICMP over one SLIP netif, and
 * nothing above it: no sockets API, no netconn, no TCP yet (the next package), no ARP/Ethernet
 * (a SLIP line has no link layer address), no IPv6, no DHCP/DNS/IGMP/AUTOIP.
 *
 * Every option below is a choice the card needs; everything not named here keeps upstream's
 * default from third_party/lwip/src/include/lwip/opt.h. The port's own types and compiler
 * glue are in arch/cc.h, the time source is native/lwip/sys_arch.c.
 */
#ifndef RXF_LWIPOPTS_H
#define RXF_LWIPOPTS_H

/* ---- the system layer: no OS, no threads, a poll loop ---- */
#define NO_SYS 1
#define LWIP_SOCKET 0
#define LWIP_NETCONN 0
#define LWIP_NETIF_API 0
#define SYS_LIGHTWEIGHT_PROT 0
#define LWIP_TIMERS 1
#define LWIP_TIMERS_CUSTOM 0

/* ---- memory: LP64 words, the heap and pools are static arrays (cells of the image) ---- */
#define MEM_ALIGNMENT 8
#define MEM_LIBC_MALLOC 0
#define MEMP_MEM_MALLOC 0
#define MEM_SIZE 8192
#define MEMP_NUM_PBUF 8
#define MEMP_NUM_UDP_PCB 4
#define MEMP_NUM_SYS_TIMEOUT 8
#define PBUF_POOL_SIZE 8
#define PBUF_POOL_BUFSIZE 1024

/* ---- the protocols of this slice ---- */
#define LWIP_IPV4 1
#define LWIP_IPV6 0
#define LWIP_ARP 0
#define LWIP_ETHERNET 0
#define LWIP_ICMP 1
#define LWIP_UDP 1
#define LWIP_TCP 0
#define LWIP_RAW 0
#define LWIP_DHCP 0
#define LWIP_AUTOIP 0
#define LWIP_ACD 0
#define LWIP_IGMP 0
#define LWIP_DNS 0
#define LWIP_SNMP 0
#define LWIP_MDNS_RESPONDER 0
#define LWIP_NETIF_LOOPBACK 0
#define LWIP_HAVE_LOOPIF 0
#define IP_FORWARD 0
#define IP_REASSEMBLY 0
#define IP_FRAG 0
#define LWIP_STATS 0
#define LWIP_SINGLE_NETIF 0

/* ---- the SLIP line (RFC 1055; VJ header compression stays off, docs/25 13) ---- */
#define LWIP_HAVE_SLIPIF 1
#define SLIP_USE_RX_THREAD 0
#define SLIP_RX_FROM_ISR 0
#define SLIP_RX_QUEUE 0

/* ---- checksums stay on in both directions: the datagram is a byte contract ---- */
#define CHECKSUM_GEN_IP 1
#define CHECKSUM_GEN_UDP 1
#define CHECKSUM_GEN_ICMP 1
#define CHECKSUM_CHECK_IP 1
#define CHECKSUM_CHECK_UDP 1
#define CHECKSUM_CHECK_ICMP 1
#define LWIP_CHECKSUM_ON_COPY 0

/* ---- diagnostics: LWIP_DEBUG stays UNDEFINED (lwIP tests it with defined(), so even a 0
 * would arm the debug strings of every memp pool); asserts are compiled out ---- */
#define LWIP_NOASSERT 1

#endif /* RXF_LWIPOPTS_H */

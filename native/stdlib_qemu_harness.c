#include "basics.h"
extern rxf_u32 stdlib_blob_hash_bytes(const rxf_u8 *, rxf_u64, rxf_u64, rxf_u64 *);
extern rxf_u32 stdlib_blob_utf8_validate(const rxf_u8 *, rxf_u64, rxf_u64 *);
extern rxf_u32 stdlib_blob_memory_fill(rxf_u8 *, rxf_u64, rxf_u8, rxf_u64);
extern rxf_u32 stdlib_blob_load_u32(const rxf_u8 *, rxf_u64, rxf_u64, rxf_u32, rxf_u32, rxf_u32 *);
static void puts_uart(const char *s) { volatile rxf_u32 *u=(volatile rxf_u32 *)0x09000000; while(*s)*u=(rxf_u32)*s++; }
static void exit_qemu(rxf_u32 ok) {
#if defined(__aarch64__)
    static rxf_u64 b[2];
    b[0] = ok ? 0x20026 : 0x20023;
    register rxf_u64 semihost_op __asm__("x0") = 0x20;
    register void *semihost_arg __asm__("x1") = b;
    __asm__ volatile("hlt #0xf000" : : "r"(semihost_op), "r"(semihost_arg) : "memory");
    for (;;) __asm__ volatile("wfe");
#else
    (void)ok;
    __builtin_trap();
#endif
}
#define REQUIRE(x) do { if(!(x)){ puts_uart("RXF-STDLIB-QEMU-FAIL\n"); exit_qemu(0); } } while(0)
int rxf_qemu_main(void) {
 rxf_u8 data[8]={1,2,3,0,0,0,0,0}; rxf_u64 out=99;
 REQUIRE(stdlib_blob_hash_bytes(data,3,0,&out)==0 && out==15035938162879559083ULL);
 static const rxf_u8 utf8[]={0x41,0xe2,0x82,0xac,0xf0,0x9f,0x98,0x80}; out=99; REQUIRE(stdlib_blob_utf8_validate(utf8,8,&out)==0 && out==3);
 out=99; static const rxf_u8 bad[]={0xc0,0x80}; REQUIRE(stdlib_blob_utf8_validate(bad,2,&out)==8 && out==99);
 REQUIRE(stdlib_blob_memory_fill(data,8,0xa5,4)==0 && data[0]==0xa5 && data[3]==0xa5);
 rxf_u32 word=0xdeadbeefU; REQUIRE(stdlib_blob_load_u32(data,3,0,4,1,&word)==6 && word==0xdeadbeefU);
 puts_uart("RXF-STDLIB-QEMU-PASS\n"); exit_qemu(1); return 0;
}

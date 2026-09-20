#include <stdint.h>
typedef uint32_t u32; extern u32 control_if(void*,u32*);
static void puts(const char*s){volatile u32*u=(volatile u32*)0x09000000;while(*s)*u=(u32)*s++;}static void finish(u32 ok){static uint64_t b[2];b[0]=ok?0x20026:0x20023;register uint64_t x0 __asm__("x0")=0x20;register void*x1 __asm__("x1")=b;__asm__ volatile("hlt #0xf000"::"r"(x0),"r"(x1):"memory");for(;;)__asm__ volatile("wfe");}
int rxf_qemu_main(void){u32 out=99,s=control_if(0,&out);if(!s&&out==1){puts("RXF-CFG-IF-PASS\n");finish(1);}puts("RXF-CFG-IF-FAIL\n");finish(0);return 0;}

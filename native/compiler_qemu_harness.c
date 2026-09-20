#include <stdint.h>
#include "stdlib_runtime.h"
#include "../generated/compiler_qemu_bindings.h"
typedef uint32_t u32; typedef uint64_t u64;
extern u32 composed_main(rxf_runtime_context *, u32 *);
extern u32 composed_checkout(rxf_runtime_context *, u32 *);
extern u32 blob_checked_add_uint32_t(u32,u32,u32*), blob_checked_subtract_uint32_t(u32,u32,u32*), blob_checked_multiply_uint32_t(u32,u32,u32*), blob_divide_uint32_t(u32,u32,u32*);
static void puts(const char *s){volatile u32 *u=(volatile u32*)0x09000000;while(*s)*u=(u32)*s++;}
static void finish(u32 ok){static u64 b[2];b[0]=ok?0x20026:0x20023;b[1]=0;register u64 x0 __asm__("x0")=0x20;register void*x1 __asm__("x1")=b;__asm__ volatile("hlt #0xf000"::"r"(x0),"r"(x1):"memory");for(;;)__asm__ volatile("wfe");}
static u32 values[6]={125,4,50,25,20,100};
static u32 add(u32 a,u32 b,u32*out){return blob_checked_add_uint32_t(a,b,out);}
static u32 sub(u32 a,u32 b,u32*out){return blob_checked_subtract_uint32_t(a,b,out);}
static u32 mul(u32 a,u32 b,u32*out){return blob_checked_multiply_uint32_t(a,b,out);}
static u32 divide(u32 a,u32 b,u32*out){return blob_divide_uint32_t(a,b,out);}
static rxf_native_slot functions[5]={{RXF_ADD_FUNCTION_ID,1,(u64)(uintptr_t)add},{RXF_SUB_FUNCTION_ID,1,(u64)(uintptr_t)sub},{RXF_MUL_FUNCTION_ID,1,(u64)(uintptr_t)mul},{RXF_DIV_FUNCTION_ID,1,(u64)(uintptr_t)divide},{RXF_CHECKOUT_FUNCTION_ID,1,(u64)(uintptr_t)composed_checkout}};
static rxf_object_entry objects[6]={
 {RXF_OBJECT_0_ID,1,7,(u64)(uintptr_t)&values[0],sizeof(u32),sizeof(u32),RXF_OBJECT_LIVE,0,0,0},
 {RXF_OBJECT_1_ID,1,7,(u64)(uintptr_t)&values[1],sizeof(u32),sizeof(u32),RXF_OBJECT_LIVE,0,0,0},
 {RXF_OBJECT_2_ID,1,7,(u64)(uintptr_t)&values[2],sizeof(u32),sizeof(u32),RXF_OBJECT_LIVE,0,0,0},
 {RXF_OBJECT_3_ID,1,7,(u64)(uintptr_t)&values[3],sizeof(u32),sizeof(u32),RXF_OBJECT_LIVE,0,0,0},
 {RXF_OBJECT_4_ID,1,7,(u64)(uintptr_t)&values[4],sizeof(u32),sizeof(u32),RXF_OBJECT_LIVE,0,0,0},
 {RXF_OBJECT_5_ID,1,7,(u64)(uintptr_t)&values[5],sizeof(u32),sizeof(u32),RXF_OBJECT_LIVE,0,0,0}};
static rxf_runtime_context context(void){rxf_runtime_context c={0};c.version=RXF_RUNTIME_ABI_VERSION;c.size=sizeof(c);c.function_count=5;c.functions=(u64)(uintptr_t)functions;c.object_count=6;c.objects=(u64)(uintptr_t)objects;return c;}
int rxf_qemu_main(void){puts("RXF-COMPOSED-START\n");rxf_runtime_context c=context();u32 out=0xa5a5a5a5;puts("RXF-COMPOSED-BEFORE-CHECKOUT\n");u32 s=composed_checkout(&c,&out);puts("RXF-COMPOSED-AFTER-CHECKOUT\n");if(s==0&&out==570){puts("RXF-COMPOSED-PASS 570\n");finish(1);}if(s)puts("RXF-COMPOSED-STATUS-FAIL\n");else puts("RXF-COMPOSED-OUTPUT-FAIL\n");finish(0);return 0;}

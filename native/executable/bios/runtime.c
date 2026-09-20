typedef unsigned char u8;
typedef unsigned int u32;
typedef unsigned long long u64;
#define MANIFEST ((const u8 *)0x9000)
/* The real-mode EDD reader stages the disk payload above all mapped segments. */
#define PAYLOAD ((const u8 *)0x04000000)
#define OUT 0x3f8
static void out(u8 c){__asm__ volatile("outb %0,%1"::"a"(c),"Nd"((unsigned short)OUT));}
static void text(const char*s){while(*s)out((u8)*s++);}
static void finish(u8 code){__asm__ volatile("outb %0,%1"::"a"(code),"Nd"((unsigned short)0xf4));for(;;)__asm__ volatile("cli; hlt");}
static u32 le32(const u8*p){return (u32)p[0]|(u32)p[1]<<8|(u32)p[2]<<16|(u32)p[3]<<24;}
static u64 le64(const u8*p){return (u64)le32(p)|(u64)le32(p+4)<<32;}
static u32 crc(const u8*p,u32 n){u32 c=~0u;while(n--){c^=*p++;for(u32 k=0;k<8;k++)c=(c>>1)^((0u-(c&1))&0xedb88320u);}return ~c;}
struct runtime_context {
 u64 version; u64 size;
 u64 function_count; u64 functions;
 u64 object_count; u64 objects;
 u64 capability_count; u64 capabilities;
 u64 heap_base; u64 heap_committed; u64 heap_limit; u64 heap_frontier;
 u64 journal_count; u64 journal_capacity; u64 journal;
 u64 cleanup_count; u64 cleanup_capacity; u64 cleanup;
};
typedef char runtime_context_size_must_be_144[(sizeof(struct runtime_context)==144)?1:-1];
void bios_runtime(void){
 if(MANIFEST[0]!='R'||MANIFEST[1]!='X'||MANIFEST[2]!='F'||MANIFEST[3]!='B'||le32(MANIFEST+8)!=0x003c0001){text("RXF-BIOS-REFUSED manifest\n");finish(0x11);}
 u32 count=le32(MANIFEST+32), poff=le32(MANIFEST+40), psz=le32(MANIFEST+44), want=le32(MANIFEST+48);
 if(poff!=0x2200||!count||count>100||psz>0x00ffde00||crc(PAYLOAD,psz)!=want){text("RXF-BIOS-REFUSED checksum\n");finish(0x11);}
 const u8*d=MANIFEST+60;struct runtime_context*ctx=0;
 for(u32 i=0;i<count;i++,d+=40){u64 va=le64(d),ms=le64(d+8),fs=le64(d+16);u32 off=le32(d+24),kind=le32(d+32);if(va<0x100000||va>=0x04000000||ms>0x04000000-va||fs>ms||off>psz||fs>psz-off){text("RXF-BIOS-REFUSED segment\n");finish(0x11);}u8*to=(u8*)(unsigned long)va;for(u64 j=0;j<fs;j++)to[j]=PAYLOAD[off+j];for(u64 j=fs;j<ms;j++)to[j]=0;if(kind==10)ctx=(struct runtime_context*)to;}
 /* Disk staging is transport scratch, never a second runtime graph. */
 for(u32 i=0;i<psz;i++)((volatile u8*)PAYLOAD)[i]=0;
 if(!ctx||ctx->version!=2||ctx->size!=sizeof(struct runtime_context)){text("RXF-BIOS-REFUSED context\n");finish(0x11);}u32 value=0xa5a5a5a5;u32 (*entry)(struct runtime_context*,u32*)=(void*)(unsigned long)le64(MANIFEST+16);u32 status=entry(ctx,&value);
 if(status){text("RXF-BIOS-REFUSED payload\n");finish(0x11);} if(value==570){text("RXF-BIOS-PASS 570\n");finish(0x10);} text("RXF-BIOS-REFUSED result\n");finish(0x11);
}

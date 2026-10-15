/* The value-width policy's 32-bit half (docs/23 6 q1): the same shape as
   global_reloc.c, compiled for a 32-bit data model (clang -m32 -- the datalayout of the
   IR states p:32:32, so one word of this module's flat memory is four bytes).

   The widths move with the model and the types never do: @door is one i32 tag and one
   function pointer -- a cell of 8 bytes here (the tag at byte 0, the pointer's 4-byte
   word at byte 4), where the LP64 build of this same source gives it 16 (the pointer's
   8-byte word at byte 8). Its initializer is the relocation of the function pointer
   (the static initializer the importer wires: __wires_boot stores enter's word at the
   slot), and use_gate dispatches through the word the slot carries the way the
   runtime-dispatch model does -- every word of the slot is the model's word (32 bits
   here), and wide payloads still honor WIDE. */

#include <stdint.h>

struct gate {
    uint32_t tag;
    uint64_t (*enter)(uint32_t);
};

static uint64_t enter(uint32_t x) {
    return (uint64_t)x + 1;
}

static struct gate volatile door = {7, enter};

uint64_t use_gate(uint32_t x) {
    return door.enter(x);
}

uint32_t gate_tag(void) {
    return door.tag;
}

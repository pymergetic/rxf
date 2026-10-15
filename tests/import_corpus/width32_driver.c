/* The ground truth of width32.c as the host C build of its own data model: a 32-bit
   driver (clang -m32 width32.c width32_driver.c) whose loader places the relocation of
   @door the native way, then answers the cases tests/test_import_width.py compares the
   imported graphs against. A 64-bit process cannot dlopen a 32-bit library (the ELF
   class differs), so the ILP32 differential runs this executable and reads its words --
   the same answers the LP64 library of the same source gives through ctypes. */

#include <stdint.h>
#include <stdio.h>

uint64_t use_gate(uint32_t x);
uint32_t gate_tag(void);

int main(void) {
    printf(
        "%llu %llu %llu %u\n",
        (unsigned long long)use_gate(5),
        (unsigned long long)use_gate(7),
        (unsigned long long)use_gate(2),
        gate_tag()
    );
    return 0;
}

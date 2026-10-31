/* slip_line.c -- the corpus of the SLIP line contract suite (docs/25 13).
 *
 * The face of the line (native/lwip/slip_line_face.c) on its own: the bodies lwIP's slipif
 * calls, imported next to nothing else. The contract suite (tests/test_lwip_slip.py) drives
 * them through a seat's fill and checks the same observable behaviour on the imported graphs
 * and on the same C compiled natively (the test reference).
 */
#include "../../native/lwip/slip_line_face.c"

/* The qstr core of MicroPython (py/qstr.c) over a bounded-arena port, as the corpus of
   the importer: every body below the scaffolding is the verbatim body of py/qstr.c, and
   the scaffolding is what a port supplies (py/mpconfigport.h's face).  The differential
   compiles this file twice: tools/native/rximport.py's compile_c defines
   RXF_IMPORT_FACES (the libc faces of qstr_faces.c are the IR bodies the module gets),
   and tests/test_import.py links the same source without it, so the native side binds
   strlen/strncmp/memcmp to libc -- the imported graphs answer against the real thing.

   The scaffolding, by the piece it stands in for:

     config       MICROPY_QSTR_BYTES_IN_HASH 1, _BYTES_IN_LEN 1 (py/mpconfig.h's
                  CORE_FEATURES level), MICROPY_ALLOC_QSTR_ENTRIES_INIT 10 and
                  MICROPY_ALLOC_QSTR_CHUNK_INIT 128, QSTR_ENTER/QSTR_EXIT nothing
                  (MICROPY_PY_THREAD off)
     VM state     MP_STATE_VM(x) is (x): the four fields the core touches are module
                  globals -- arena cells the run keeps across calls, the C statics the
                  native shared library keeps between its ctypes calls
     the heap     m_malloc_maybe/m_renew_maybe/m_malloc_fail over a bounded arena of
                  one global buffer (2 KiB: the arena is a cell of the image and the
                  world's payload area is 3840 bytes): a port's allocator (m_new_maybe/
                  m_renew_maybe are the macros of py/misc.h).  It grows the top block in
                  place, moves only where allow_move says so, and answers NULL when
                  full -- the same answers on both sides because both run this file
     the raise    mp_raise_msg/mp_type_RuntimeError/MP_ERROR_TEXT of py/runtime.h: the
                  call before the unreachable is what the subset lowers (the panic-path
                  idiom), so the body only has to not return natively (abort)
     the faces    strlen/strncmp/memcmp of the bodies below map onto qstr_faces.c under
                  RXF_IMPORT_FACES (the only #define of the mapping lives here)
     the pool     the pools of py/qstr.c are static const initializers whose pointer
                  fields are link-time relocations; the flat profile places a cell's
                  payload as bytes and refuses a relocation it cannot know
                  (global_reloc.c), so this port builds its pools the way a boot
                  sequence does: the data arrays and the strings are cells of literal
                  bytes, and qstr_port_boot stores their addresses into the pools'
                  pointer fields before qstr_init() runs.  The pool header is py/qstr.h's
                  with its two size_t bitfields as two plain words -- a layout the
                  bodies never see, kept plain so each read is one load

   What is not here, by choice: qstr_from_strn_static (MICROPY_VFS_ROM off),
   qstr_pool_info/qstr_dump_data (diagnostics over printf), mp_decompress_rom_string
   (MICROPY_ROM_TEXT_COMPRESSION off), and the generated pools of genhdr: the corpus
   pool below is its own small stand-in for makeqstrdata.py's output, two pools the way
   py/qstr.c keeps them (an unsorted static one and a sorted one chained to it), so the
   sequential search, the binary search and the prev walk all run.  The tests check
   every stored hash against qstr_compute_hash, which is the makeqstrdata contract. */

#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* ---- the faces of the import compile ---- */

#ifdef RXF_IMPORT_FACES
#define strlen qstr_face_strlen
#define strncmp qstr_face_strncmp
#define memcmp qstr_face_memcmp
#include "qstr_faces.c"
#endif

/* ---- the port's config (py/mpconfig.h's CORE_FEATURES level) ---- */

#define MICROPY_QSTR_BYTES_IN_HASH (1)
#define MICROPY_QSTR_BYTES_IN_LEN (1)
#define MICROPY_ALLOC_QSTR_ENTRIES_INIT (10)
#define MICROPY_ALLOC_QSTR_CHUNK_INIT (128)

#define DEBUG_printf(...) (void)0

#if MICROPY_QSTR_BYTES_IN_HASH
#define Q_HASH_MASK ((1 << (8 * MICROPY_QSTR_BYTES_IN_HASH)) - 1)
#else
#define Q_HASH_MASK (0xffff)
#endif

#define QSTR_ENTER()
#define QSTR_EXIT()

typedef unsigned char byte;
typedef size_t mp_uint_t;
typedef size_t qstr;

#if MICROPY_QSTR_BYTES_IN_HASH == 1
typedef uint8_t qstr_hash_t;
#elif MICROPY_QSTR_BYTES_IN_HASH == 2
typedef uint16_t qstr_hash_t;
#endif

#if MICROPY_QSTR_BYTES_IN_LEN == 1
typedef uint8_t qstr_len_t;
#elif MICROPY_QSTR_BYTES_IN_LEN == 2
typedef uint16_t qstr_len_t;
#endif

/* the pool of py/qstr.h, its two size_t bitfields as two plain words */
typedef struct _qstr_pool_t {
    const struct _qstr_pool_t *prev;
    size_t total_prev_len;
    size_t is_sorted;
    size_t alloc;
    size_t len;
    #if MICROPY_QSTR_BYTES_IN_HASH
    qstr_hash_t *hashes;
    #endif
    qstr_len_t *lengths;
    const char *qstrs[];
} qstr_pool_t;

/* ---- the VM state the core reads: its fields, as the port's globals ---- */

#define MP_STATE_VM(x) (x)
static qstr_pool_t *last_pool;
static char *qstr_last_chunk;
static size_t qstr_last_used;
static size_t qstr_last_alloc;

/* ---- the port's heap: one bounded arena, the m_* of py/misc.h over it ---- */

#define ARENA_BYTES 2048
static unsigned char m_arena[ARENA_BYTES];
static size_t m_arena_used;      /* the bump: everything below is handed out */
static size_t m_arena_top;       /* where the last allocation starts */
static size_t m_arena_top_size;  /* its size, rounded up */

void *m_malloc_maybe(size_t n) {
    size_t want = (n + 15) & ~(size_t)15;
    if (want > ARENA_BYTES - m_arena_used) {
        return NULL;
    }
    void *p = &m_arena[m_arena_used];
    m_arena_top = m_arena_used;
    m_arena_top_size = want;
    m_arena_used += want;
    return p;
}

void *m_renew_maybe_impl(void *p, size_t old_n, size_t new_n, bool allow_move) {
    size_t want = (new_n + 15) & ~(size_t)15;
    bool is_top = p == (void *)&m_arena[m_arena_top];
    if (want <= m_arena_top_size && is_top) {
        /* the block is the top of the arena: shrink or grow it in place */
        m_arena_used = m_arena_top + want;
        m_arena_top_size = want;
        return p;
    }
    if (new_n <= old_n) {
        return p;  /* a shrink of an interior block always fits */
    }
    if (!allow_move) {
        return NULL;
    }
    void *q = m_malloc_maybe(new_n);
    if (q != NULL) {
        memcpy(q, p, old_n);
    }
    return q;
}

__attribute__((noinline)) _Noreturn void m_malloc_fail(size_t n) {
    (void)n;
    abort();
}

#define m_new_maybe(type, n) ((type *)m_malloc_maybe(sizeof(type) * (n)))
#define m_renew_maybe(type, ptr, old_n, new_n, allow_move) \
    ((type *)m_renew_maybe_impl((ptr), sizeof(type) * (old_n), sizeof(type) * (new_n), (allow_move)))

/* ---- the raise of py/runtime.h: the call before the unreachable is the face ---- */

static const int mp_type_RuntimeError = 0;
#define MP_ERROR_TEXT(s) (s)

__attribute__((noinline)) _Noreturn void mp_raise_msg(const void *type, const char *msg) {
    (void)type;
    (void)msg;
    abort();
}

/* ---- the pool: what genhdr/qstrdefs.generated.h makes, two pools of its own ---- */

enum {
    MP_QSTRnull = 0,
    MP_QSTR_ = 1,
    MP_QSTR_a = 2,
    MP_QSTR_ab = 3,
    MP_QSTR_print = 4,
    MP_QSTR_len = 5,
    MP_QSTR_hello = 6,
    MP_QSTR_import = 7,
    MP_QSTR_micropython = 8,
    MP_QSTR_qstr = 9,
};

/* the string data and the tables: cells of literal bytes (the makeqstrdata contract is
   that every hash is what qstr_compute_hash answers for the string; the tests check) */
static const char qstr_str_null[] = "";
static const char qstr_str_a[] = "a";
static const char qstr_str_ab[] = "ab";
static const char qstr_str_print[] = "print";
static const char qstr_str_len[] = "len";
static const char qstr_str_hello[] = "hello";
static const char qstr_str_import[] = "import";
static const char qstr_str_micropython[] = "micropython";
static const char qstr_str_qstr[] = "qstr";

static const qstr_hash_t mp_qstr_const_hashes_static[] = {0, 5, 196, 38, 84, 98};
static const qstr_len_t mp_qstr_const_lengths_static[] = {0, 0, 1, 2, 5, 3};

qstr_pool_t mp_qstr_const_pool_static = {
    NULL,               // no previous pool
    0,                  // no previous pool
    false,              // is_sorted
    6,                  // alloc: no room left (py/qstr.c's const pool is never written)
    6,                  // len
    NULL,               // hashes: qstr_port_boot stores the addresses below
    NULL,
    {NULL, NULL, NULL, NULL, NULL, NULL},
};

static const qstr_hash_t mp_qstr_const_hashes[] = {231, 120, 11, 161};
static const qstr_len_t mp_qstr_const_lengths[] = {5, 6, 11, 4};

qstr_pool_t mp_qstr_const_pool = {
    NULL,               // prev: qstr_port_boot chains it to the static pool
    6,
    true,               // is_sorted
    4,                  // alloc: no room left
    4,                  // len
    NULL,
    NULL,
    {NULL, NULL, NULL, NULL},
};

#define CONST_POOL mp_qstr_const_pool

/* the port's boot: the addresses the static const initializers of py/qstr.c would carry
   as relocations, stored into the pools before qstr_init() runs */
void qstr_port_boot(void) {
    mp_qstr_const_pool_static.hashes = (qstr_hash_t *)mp_qstr_const_hashes_static;
    mp_qstr_const_pool_static.lengths = (qstr_len_t *)mp_qstr_const_lengths_static;
    mp_qstr_const_pool_static.qstrs[0] = qstr_str_null;
    mp_qstr_const_pool_static.qstrs[1] = qstr_str_null;
    mp_qstr_const_pool_static.qstrs[2] = qstr_str_a;
    mp_qstr_const_pool_static.qstrs[3] = qstr_str_ab;
    mp_qstr_const_pool_static.qstrs[4] = qstr_str_print;
    mp_qstr_const_pool_static.qstrs[5] = qstr_str_len;
    mp_qstr_const_pool.prev = &mp_qstr_const_pool_static;
    mp_qstr_const_pool.hashes = (qstr_hash_t *)mp_qstr_const_hashes;
    mp_qstr_const_pool.lengths = (qstr_len_t *)mp_qstr_const_lengths;
    mp_qstr_const_pool.qstrs[0] = qstr_str_hello;
    mp_qstr_const_pool.qstrs[1] = qstr_str_import;
    mp_qstr_const_pool.qstrs[2] = qstr_str_micropython;
    mp_qstr_const_pool.qstrs[3] = qstr_str_qstr;
}

/* ---- the qstr core of py/qstr.c, verbatim (the declarations of py/qstr.h first) ---- */

void qstr_init(void);
size_t qstr_compute_hash(const byte *data, size_t len);
qstr qstr_find_strn(const char *str, size_t str_len); // returns MP_QSTRnull if not found
qstr qstr_from_str(const char *str);
qstr qstr_from_strn(const char *str, size_t len);
mp_uint_t qstr_hash(qstr q);
size_t qstr_len(qstr q);
const char *qstr_str(qstr q);
const byte *qstr_data(qstr q, size_t *len);

size_t qstr_compute_hash(const byte *data, size_t len) {
    // djb2 algorithm; see http://www.cse.yorku.ca/~oz/hash.html
    size_t hash = 5381;
    for (const byte *top = data + len; data < top; data++) {
        hash = ((hash << 5) + hash) ^ (*data); // hash * 33 ^ data
    }
    hash &= Q_HASH_MASK;
    // Make sure that valid hash is never zero, zero means "hash not computed"
    if (hash == 0) {
        hash++;
    }
    return hash;
}

void qstr_init(void) {
    MP_STATE_VM(last_pool) = (qstr_pool_t *)&CONST_POOL; // we won't modify the const_pool since it has no allocated room left
    MP_STATE_VM(qstr_last_chunk) = NULL;
}

static const qstr_pool_t *find_qstr(qstr *q) {
    // search pool for this qstr
    // total_prev_len==0 in the final pool, so the loop will always terminate
    const qstr_pool_t *pool = MP_STATE_VM(last_pool);
    while (*q < pool->total_prev_len) {
        pool = pool->prev;
    }
    *q -= pool->total_prev_len;
    assert(*q < pool->len);
    return pool;
}

// qstr_mutex must be taken while in this function
static qstr qstr_add(mp_uint_t len, const char *q_ptr) {
    #if MICROPY_QSTR_BYTES_IN_HASH
    mp_uint_t hash = qstr_compute_hash((const byte *)q_ptr, len);
    DEBUG_printf("QSTR: add hash=%d len=%d data=%.*s\n", hash, len, len, q_ptr);
    #else
    DEBUG_printf("QSTR: add len=%d data=%.*s\n", len, len, q_ptr);
    #endif

    // make sure we have room in the pool for a new qstr
    if (MP_STATE_VM(last_pool)->len >= MP_STATE_VM(last_pool)->alloc) {
        size_t new_alloc = MP_STATE_VM(last_pool)->alloc * 2;
        mp_uint_t pool_size = sizeof(qstr_pool_t)
            + (sizeof(const char *)
                #if MICROPY_QSTR_BYTES_IN_HASH
                + sizeof(qstr_hash_t)
                #endif
                + sizeof(qstr_len_t)) * new_alloc;
        qstr_pool_t *pool = (qstr_pool_t *)m_malloc_maybe(pool_size);
        if (pool == NULL) {
            MP_STATE_VM(qstr_last_chunk) = NULL;
            QSTR_EXIT();
            m_malloc_fail(new_alloc);
        }
        #if MICROPY_QSTR_BYTES_IN_HASH
        pool->hashes = (qstr_hash_t *)(pool->qstrs + new_alloc);
        pool->lengths = (qstr_len_t *)(pool->hashes + new_alloc);
        #else
        pool->lengths = (qstr_len_t *)(pool->qstrs + new_alloc);
        #endif
        pool->prev = MP_STATE_VM(last_pool);
        pool->total_prev_len = MP_STATE_VM(last_pool)->total_prev_len + MP_STATE_VM(last_pool)->len;
        // Shares a word with total_prev_len, so it carries whatever the
        // allocator handed back until set; a stale 1 makes qstr_find_strn
        // binary-search this unsorted pool and miss every runtime qstr.
        pool->is_sorted = false;
        pool->alloc = new_alloc;
        pool->len = 0;
        MP_STATE_VM(last_pool) = pool;
        DEBUG_printf("QSTR: allocate new pool of size %d\n", MP_STATE_VM(last_pool)->alloc);
    }

    // add the new qstr
    mp_uint_t at = MP_STATE_VM(last_pool)->len;
    #if MICROPY_QSTR_BYTES_IN_HASH
    MP_STATE_VM(last_pool)->hashes[at] = hash;
    #endif
    MP_STATE_VM(last_pool)->lengths[at] = len;
    MP_STATE_VM(last_pool)->qstrs[at] = q_ptr;
    MP_STATE_VM(last_pool)->len++;

    // return id for the newly-added qstr
    return MP_STATE_VM(last_pool)->total_prev_len + at;
}

qstr qstr_find_strn(const char *str, size_t str_len) {
    if (str_len == 0) {
        // strncmp behaviour is undefined for str==NULL.
        return MP_QSTR_;
    }

    #if MICROPY_QSTR_BYTES_IN_HASH
    // work out hash of str
    size_t str_hash = qstr_compute_hash((const byte *)str, str_len);
    #endif

    // search pools for the data
    for (const qstr_pool_t *pool = MP_STATE_VM(last_pool); pool != NULL; pool = pool->prev) {
        size_t low = 0;
        size_t high = pool->len - 1;

        // binary search inside the pool
        if (pool->is_sorted) {
            while (high - low > 1) {
                size_t mid = (low + high) / 2;
                int cmp = strncmp(str, pool->qstrs[mid], str_len);
                if (cmp <= 0) {
                    high = mid;
                } else {
                    low = mid;
                }
            }
        }

        // sequential search for the remaining strings
        for (mp_uint_t at = low; at < high + 1; at++) {
            if (
                #if MICROPY_QSTR_BYTES_IN_HASH
                pool->hashes[at] == str_hash &&
                #endif
                pool->lengths[at] == str_len
                && memcmp(pool->qstrs[at], str, str_len) == 0) {
                return pool->total_prev_len + at;
            }
        }
    }

    // not found; return null qstr
    return MP_QSTRnull;
}

qstr qstr_from_str(const char *str) {
    return qstr_from_strn(str, strlen(str));
}

static qstr qstr_from_strn_helper(const char *str, size_t len, bool data_is_static) {
    QSTR_ENTER();
    qstr q = qstr_find_strn(str, len);
    if (q == 0) {
        // qstr does not exist in interned pool so need to add it

        // check that len is not too big
        if (len >= (1 << (8 * MICROPY_QSTR_BYTES_IN_LEN))) {
            QSTR_EXIT();
            mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("name too long"));
        }

        if (data_is_static) {
            // Given string data will be forever available so use it directly.
            assert(str[len] == '\0');
            goto add;
        }

        // compute number of bytes needed to intern this string
        size_t n_bytes = len + 1;

        if (MP_STATE_VM(qstr_last_chunk) != NULL && MP_STATE_VM(qstr_last_used) + n_bytes > MP_STATE_VM(qstr_last_alloc)) {
            // not enough room at end of previously interned string so try to grow
            char *new_p = m_renew_maybe(char, MP_STATE_VM(qstr_last_chunk), MP_STATE_VM(qstr_last_alloc), MP_STATE_VM(qstr_last_alloc) + n_bytes, false);
            if (new_p == NULL) {
                // could not grow existing memory; shrink it to fit previous
                (void)m_renew_maybe(char, MP_STATE_VM(qstr_last_chunk), MP_STATE_VM(qstr_last_alloc), MP_STATE_VM(qstr_last_used), false);
                MP_STATE_VM(qstr_last_chunk) = NULL;
            } else {
                // could grow existing memory
                MP_STATE_VM(qstr_last_alloc) += n_bytes;
            }
        }

        if (MP_STATE_VM(qstr_last_chunk) == NULL) {
            // no existing memory for the interned string so allocate a new chunk
            size_t al = n_bytes;
            if (al < MICROPY_ALLOC_QSTR_CHUNK_INIT) {
                al = MICROPY_ALLOC_QSTR_CHUNK_INIT;
            }
            MP_STATE_VM(qstr_last_chunk) = m_new_maybe(char, al);
            if (MP_STATE_VM(qstr_last_chunk) == NULL) {
                // failed to allocate a large chunk so try with exact size
                MP_STATE_VM(qstr_last_chunk) = m_new_maybe(char, n_bytes);
                if (MP_STATE_VM(qstr_last_chunk) == NULL) {
                    QSTR_EXIT();
                    m_malloc_fail(n_bytes);
                }
                al = n_bytes;
            }
            MP_STATE_VM(qstr_last_alloc) = al;
            MP_STATE_VM(qstr_last_used) = 0;
        }

        // allocate memory from the chunk for this new interned string's data
        char *q_ptr = MP_STATE_VM(qstr_last_chunk) + MP_STATE_VM(qstr_last_used);
        MP_STATE_VM(qstr_last_used) += n_bytes;

        // store the interned strings' data
        memcpy(q_ptr, str, len);
        q_ptr[len] = '\0';
        str = q_ptr;

    add:
        q = qstr_add(len, str);
    }
    QSTR_EXIT();
    return q;
}

qstr qstr_from_strn(const char *str, size_t len) {
    return qstr_from_strn_helper(str, len, false);
}

mp_uint_t qstr_hash(qstr q) {
    const qstr_pool_t *pool = find_qstr(&q);
    #if MICROPY_QSTR_BYTES_IN_HASH
    return pool->hashes[q];
    #else
    return qstr_compute_hash((byte *)pool->qstrs[q], pool->lengths[q]);
    #endif
}

size_t qstr_len(qstr q) {
    const qstr_pool_t *pool = find_qstr(&q);
    return pool->lengths[q];
}

const char *qstr_str(qstr q) {
    const qstr_pool_t *pool = find_qstr(&q);
    return pool->qstrs[q];
}

const byte *qstr_data(qstr q, size_t *len) {
    const qstr_pool_t *pool = find_qstr(&q);
    *len = pool->lengths[q];
    return (byte *)pool->qstrs[q];
}

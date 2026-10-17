/*
 * The activation seam (native/wrap/wrap.h): the check an image passes before it is
 * entered -- 'ok means it is written for THIS image'.
 *
 * The trust anchor (wrap_trust.S: the deployment's words compiled into this wrapper,
 * never into the image) names the voices this wrapper admits and revokes and the
 * policy words of docs/11: now, max_age, skew, quorum. The cert block of the package's
 * tail carries one fixed record per statement (the layout
 * binary.package.CERT_RECORD_BYTES pins; the meaning of one record is one
 * certify.Statement of the Python seat). This reads exactly those bytes and asks the
 * one question a boot can answer honestly: the digest of the block it is about to run
 * (rxf_wrap_sha256 over what was loaded) against the block word the voices signed
 * (rxf_wrap_ed25519_verify over the canonical bytes of the statement, the six lines
 * certify.schema.canonical pins).
 *
 * What is checked, cheap words first (the order of certify.verify): the key is
 * admitted and not revoked, the stamp is within the policy words, the record speaks
 * for these bytes and agrees with the voices already credited on what it says about
 * the image, and only then the signature. One key counts once however often it
 * speaks (docs/11: distinct voices); the quorum is over those.
 *
 * The policy is typed metadata, never inferred (docs/11): the check reads no clock
 * and no machine. `now` is a word of the anchor like the rest, and when there is no
 * anchor voice and no cert block the image passes -- a deployment that wants
 * enforcement admits its voices into the anchor and signs its packages.
 */
#include "wrap.h"
#include "wrap_crypto.h"
#include "wrap_trust.h"

/* The record of the cert block: the words binary.package.CERT_RECORD_BYTES pins. */
#define ACTIVATE_ID_BYTES 64
#define ACTIVATE_RECORD_BYTES 232
#define ACTIVATE_TEXT_BYTES 384

static rxf_u64 word_of(const rxf_u8 *at) {
    rxf_u64 word = 0;
    for (rxf_u32 index = 0; index < 8; ++index)
        word |= (rxf_u64)at[index] << (index * 8);
    return word;
}

static rxf_u32 count_of(const rxf_u8 *at) {
    rxf_u32 word = 0;
    for (rxf_u32 index = 0; index < 4; ++index)
        word |= (rxf_u32)at[index] << (index * 8);
    return word;
}

static void put_hex(char *out, const rxf_u8 *word) {
    static const char digits[] = "0123456789abcdef";
    for (rxf_u32 index = 0; index < 32; ++index) {
        out[index * 2] = digits[word[index] >> 4];
        out[index * 2 + 1] = digits[word[index] & 15];
    }
}

static rxf_u64 put_dec(char *out, rxf_u64 value) {
    char stack[20];
    rxf_u64 held = 0;
    do {
        stack[held++] = (char)('0' + value % 10);
        value /= 10;
    } while (value);
    for (rxf_u64 index = 0; index < held; ++index)
        out[index] = stack[held - 1 - index];
    return held;
}

static rxf_u64 put(char *out, const char *text, rxf_u64 bytes) {
    for (rxf_u64 index = 0; index < bytes; ++index)
        out[index] = text[index];
    return bytes;
}

/* The same bytes in two places: the word compare of the check. */
static rxf_u32 same(const void *left, const void *right, rxf_u64 bytes) {
    const rxf_u8 *a = (const rxf_u8 *)left;
    const rxf_u8 *b = (const rxf_u8 *)right;
    while (bytes--)
        if (*a++ != *b++)
            return 0;
    return 1;
}

/* The length of a key id: the bytes up to its zero padding, at most the field. */
static rxf_u64 id_length(const rxf_u8 *id) {
    rxf_u64 length = 0;
    while (length < ACTIVATE_ID_BYTES && id[length])
        ++length;
    return length;
}

/* The same id in two fields: the bytes up to the zero padding, and the length. */
static rxf_u32 same_id(const rxf_u8 *left, rxf_u64 left_bytes, const rxf_u8 *right) {
    if (left_bytes != id_length(right))
        return 0;
    for (rxf_u64 index = 0; index < left_bytes; ++index)
        if (left[index] != right[index])
            return 0;
    return 1;
}

/*
 * The canonical bytes of one record: the six lines certify.schema.canonical pins,
 * exactly what the voice signed. Answers their count; `out` takes
 * ACTIVATE_TEXT_BYTES.
 */
static rxf_u64 statement_text(char *out, const rxf_u8 *record) {
    rxf_u64 at = 0;
    at += put(out + at, "rxf-certify-statement-v2\n", 25);
    at += put(out + at, "image ", 6);
    put_hex(out + at, record);
    at += 64;
    at += put(out + at, "\ntext ", 6);
    put_hex(out + at, record + 32);
    at += 64;
    at += put(out + at, "\nblock ", 7);
    put_hex(out + at, record + 64);
    at += 64;
    at += put(out + at, "\nkey ", 5);
    for (rxf_u64 index = 0; index < ACTIVATE_ID_BYTES && record[96 + index]; ++index)
        out[at++] = (char)record[96 + index];
    at += put(out + at, "\nissued ", 8);
    at += put_dec(out + at, word_of(record + 160));
    out[at++] = '\n';
    return at;
}

rxf_u32 rxf_activate_check(const void *block, rxf_u64 block_bytes, const void *cert,
                           rxf_u64 cert_bytes) {
    const rxf_u8 *trust = rxf_wrap_trust_start;
    rxf_u64 trust_bytes = (rxf_u64)(rxf_wrap_trust_end - rxf_wrap_trust_start);
    const rxf_u8 *keys, *revoked, *records = (const rxf_u8 *)cert;
    rxf_u64 key_count, revoked_count, now, max_age, skew, voices = 0;
    rxf_u32 quorum, decided = 0;
    rxf_u8 digest[32], words[64];
    char text[ACTIVATE_TEXT_BYTES];

    /* The anchor: the layout of wrap_trust.h, whole and usable. */
    if (trust_bytes < RXF_WRAP_TRUST_BYTES ||
        !same(RXF_WRAP_TRUST_MAGIC, trust, 4) || count_of(trust + 4) != RXF_WRAP_TRUST_VERSION)
        return RXF_WRAP_ACTIVATE_BAD_ANCHOR;
    now = word_of(trust + 8);
    max_age = word_of(trust + 16);
    skew = word_of(trust + 24);
    quorum = count_of(trust + 32);
    key_count = count_of(trust + 36);
    revoked_count = count_of(trust + 40);
    if (quorum == 0 || count_of(trust + 44) != 0)
        return RXF_WRAP_ACTIVATE_BAD_ANCHOR;
    keys = trust + RXF_WRAP_TRUST_BYTES;
    revoked = keys + key_count * RXF_WRAP_TRUST_KEY_BYTES;
    if (trust_bytes < RXF_WRAP_TRUST_BYTES + key_count * RXF_WRAP_TRUST_KEY_BYTES +
                          revoked_count * RXF_WRAP_TRUST_ID_BYTES)
        return RXF_WRAP_ACTIVATE_BAD_ANCHOR;

    /* No cert block: only a wrapper that admits no voice passes what is unsigned. */
    if (cert_bytes == 0)
        return key_count == 0 ? RXF_WRAP_ACTIVATE_OK : RXF_WRAP_ACTIVATE_NO_CERT;
    if (cert_bytes % ACTIVATE_RECORD_BYTES)
        return RXF_WRAP_ACTIVATE_BAD_CERT;

    /* The one word this check owns: the digest of the bytes it is about to run. */
    rxf_wrap_sha256(block, block_bytes, digest);

    /*
     * The voices, one pass per admitted key: the first record of a key that says
     * everything right is that key's one voice (docs/11: one key counts once however
     * often it speaks), and the words the voices agree on are the ones the first that
     * verified spoke for. The cheap words refuse first -- fresh, speaking for these
     * bytes, agreeing with the decision -- and the signature, the only expensive word,
     * is checked last (certify.verify's order).
     */
    for (rxf_u64 key_at = 0; key_at < key_count; ++key_at) {
        const rxf_u8 *id = keys + key_at * RXF_WRAP_TRUST_KEY_BYTES;
        const rxf_u8 *voice = id + ACTIVATE_ID_BYTES;
        rxf_u64 length = id_length(id), text_bytes;
        rxf_u32 is_revoked = 0;

        if (length == 0 || length == ACTIVATE_ID_BYTES)
            continue;
        for (rxf_u64 index = 0; index < revoked_count; ++index)
            if (same_id(id, length, revoked + index * RXF_WRAP_TRUST_ID_BYTES))
                is_revoked = 1;
        if (is_revoked)
            continue;
        for (rxf_u64 record_at = 0; record_at < cert_bytes; record_at += ACTIVATE_RECORD_BYTES) {
            const rxf_u8 *record = records + record_at;
            rxf_u64 issued = word_of(record + 160);

            if (!same_id(id, length, record + 96))
                continue;
            if (issued > now && issued - now > skew)  /* NotYetValid, beyond the skew */
                continue;
            if (now > issued && now - issued > max_age)  /* Expired, older than allowed */
                continue;
            if (!same(record + 64, digest, 32))  /* WrongDigest: not these bytes */
                continue;
            if (decided && !same(record, words, 64))
                continue;
            text_bytes = statement_text(text, record);
            if (rxf_wrap_ed25519_verify(voice, text, text_bytes, record + 168))
                continue;
            if (!decided) {
                for (rxf_u64 index = 0; index < 64; ++index)
                    words[index] = record[index];
                decided = 1;
            }
            voices += 1;
            break;
        }
    }
    if (voices == 0)
        return RXF_WRAP_ACTIVATE_NO_VOICE;
    if (voices < quorum)
        return RXF_WRAP_ACTIVATE_NO_QUORUM;
    return RXF_WRAP_ACTIVATE_OK;
}

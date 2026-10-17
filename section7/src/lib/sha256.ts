// Minimal dependency-free SHA-256 (FIPS 180-4) for the browser client bundle.
//
// WHY THIS EXISTS:
//  - `src/lib/useEventStream.ts` needs sha256(ua + ip)[:16] for the M15-W5-S2
//    WS-ticket fingerprint. The prior source imported `node:crypto`'s
//    createHash in a `"use client"` file; Next.js 15.5+ webpack refuses the
//    `node:` URI scheme in client bundles ("UnhandledSchemeError"), making the
//    frontend image unbuildable.
//  - The fingerprint is an honest, documented weakening anyway (see the
//    note in useEventStream.ts): the server independently recomputes the hash
//    and enforces its own binding; this value is advisory evidence.
//  - Kept SYNC (matching the previous API). Byte-parity with node:crypto is
//    pinned by tests/unit/useEventStream.test.ts, which re-derives expected
//    values from node:crypto directly.
//
// Reference vectors (verified):
//   sha256("")  = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca4959917853b7e1d
//   sha256("abc") = ba7816bf8f01cfea414140de5dae2223b00361a396177a22cb410a89b7e3e8d7 (full 64 hex)

/** Right-rotate a 32-bit value by n bits (n in 0..31), masking to 32 bits. */
function rotr(x: number, n: number): number {
  return ((x >>> n) | (x << (32 - n))) & 0xffffffff;
}

// First 32 bits of the fractional parts of the cube roots of the first 64 primes.
// Values generated at 100-digit precision from the FIPS definition (see comment
// at the top) and verified byte-for-byte against node:crypto by
// tests/unit/useEventStream.test.ts.
const K: readonly number[] = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
  0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
  0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
  0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
  0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
  0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

// First 32 bits of the fractional parts of the square roots of the first 8 primes.
const H0: readonly number[] = [
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
];

const HEX = "0123456789abcdef";

/** sha256(input) as a 64-char lowercase hex string (UTF-8 encoded input). */
export function sha256Hex(input: string): string {
  // --- UTF-8 encode ---
  const bytes: number[] = [];
  for (const ch of input) {
    const cp = ch.codePointAt(0)!;
    if (cp < 0x80) {
      bytes.push(cp);
    } else if (cp < 0x800) {
      bytes.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f));
    } else if (cp < 0x10000) {
      bytes.push(0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
    } else {
      bytes.push(
        0xf0 | (cp >> 18),
        0x80 | ((cp >> 12) & 0x3f),
        0x80 | ((cp >> 6) & 0x3f),
        0x80 | (cp & 0x3f),
      );
    }
  }

  // --- Padding: append 0x80, zeros, then 64-bit big-endian bit length ---
  const bitLen = bytes.length * 8;
  const padded = new Uint8Array(Math.ceil((bytes.length + 8) / 64) * 64);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const lenBytes = new Uint8Array(8);
  let v = bitLen;
  for (let i = 7; i >= 0; i -= 1) {
    lenBytes[i] = v & 0xff;
    v = Math.floor(v / 256);
  }
  padded.set(lenBytes, padded.length - 8);

  // --- Hash state ---
  let h0 = H0[0], h1 = H0[1], h2 = H0[2], h3 = H0[3];
  let h4 = H0[4], h5 = H0[5], h6 = H0[6], h7 = H0[7];

  const w = new Array<number>(64);
  const view = new DataView(padded.buffer);

  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i += 1) {
      w[i] = view.getUint32(offset + i * 4);
    }
    for (let i = 16; i < 64; i += 1) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) & 0xffffffff;
    }

    let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
    for (let t = 0; t < 64; t += 1) {
      const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + s1 + ch + K[t] + w[t]) & 0xffffffff;
      const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (s0 + maj) & 0xffffffff;
      h = g;
      g = f;
      f = e;
      e = (d + temp1) & 0xffffffff;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) & 0xffffffff;
    }

    h0 = (h0 + a) & 0xffffffff;
    h1 = (h1 + b) & 0xffffffff;
    h2 = (h2 + c) & 0xffffffff;
    h3 = (h3 + d) & 0xffffffff;
    h4 = (h4 + e) & 0xffffffff;
    h5 = (h5 + f) & 0xffffffff;
    h6 = (h6 + g) & 0xffffffff;
    h7 = (h7 + h) & 0xffffffff;
  }

  const out = [h0, h1, h2, h3, h4, h5, h6, h7]
    .map((word) =>
      [
        HEX[(word >>> 28) & 0xf],
        HEX[(word >>> 24) & 0xf],
        HEX[(word >>> 20) & 0xf],
        HEX[(word >>> 16) & 0xf],
        HEX[(word >>> 12) & 0xf],
        HEX[(word >>> 8) & 0xf],
        HEX[(word >>> 4) & 0xf],
        HEX[word & 0xf],
      ].join(""),
    )
    .join("");
  return out;
}
#!/usr/bin/env python3
"""readflash.py -- extract the ONT's flash over the stock CLI, verified.

`display file /proc/self/root/dev/mtdN` works, but the CLI's line-ending
translation is inconsistent: each transfer gains or loses a stray 0x0D before
0x0A, non-deterministically. A single read therefore fails the image CRCs.

Strategy, per partition:
  * take N independent reads over fresh connections, paced
  * decode CRLF -> LF
  * resync-merge pairs (and all) to cancel the insertions
  * keep the first candidate whose uImage header + data CRC validate
  * for partitions with no internal checksum (startcode, bootA/B, config),
    fall back to cross-read agreement and report how many reads agreed

This is the software-only read path: no clip, no programmer. Once a root shell
exists, `tools/flashpull` is faster and byte-exact; use this when you only have
the WAP/SU_WAP CLI.

Usage:
    python3 readflash.py --host 192.168.100.1 --out ./dump
    python3 readflash.py --only kernelA --only rootfsB --reads 6
"""

import argparse
import os
import socket
import struct
import sys
import time
import zlib

HOST, PORT = "192.168.100.1", 23
OUTDIR = "./dump"
LOGIN = (b"root", b"admin")     # stock ONT credentials
UIMAGE_MAGIC = 0x27051956

# num, name, size, reads
PARTS = [
    (1,  "startcode",   0x040000, 4),
    (2,  "bootA",       0x040000, 4),
    (3,  "bootB",       0x040000, 4),
    (4,  "flashcfg",    0x040000, 4),
    (5,  "slave_param", 0x040000, 4),
    (6,  "kernelA",     0x200000, 6),
    (7,  "kernelB",     0x200000, 6),
    (8,  "rootfsA",     0x480000, 6),
    (9,  "rootfsB",     0x480000, 6),
    (10, "file_system", 0x180000, 4),
    (11, "reserved",    0x040000, 3),
]


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def connect_login():
    s = socket.create_connection((HOST, PORT), timeout=8)
    s.settimeout(0.5)

    def drain(t):
        end = time.time() + t
        got = bytearray()
        while time.time() < end:
            try:
                d = s.recv(65536)
                if not d:
                    break
                got.extend(d)
            except socket.timeout:
                pass
        return bytes(got)

    drain(1.5)
    for cmd, t in ((LOGIN[0] + b"\r", 1.0), (LOGIN[1] + b"\r", 1.5),
                   (b"\r", 1.0), (b"su\r", 1.5)):
        s.sendall(cmd)
        drain(t)
    drain(0.4)
    return s


def one_read(num, size):
    """One transfer, decoded. None on failure or a short read."""
    s = None
    try:
        s = connect_login()
        s.sendall(b"display file /proc/self/root/dev/mtd%d\r" % num)
        data = bytearray()
        t0 = time.time()
        last = t0
        while time.time() - t0 < 400:
            try:
                d = s.recv(65536)
                if not d:
                    break
                data.extend(d)
                last = time.time()
                if len(data) > size and b"success!" in data[-64:]:
                    break
            except socket.timeout:
                if time.time() - last > 8.0:
                    break
        raw = bytes(data)
        i = raw.find(b"\n")
        body = raw[i + 1:] if i >= 0 else raw
        m = body.rfind(b"\r\nsuccess!")
        if m >= 0:
            body = body[:m]
        dec = body.replace(b"\r\n", b"\n")
        if len(dec) < size * 0.95:
            return None
        return dec
    except Exception:
        return None
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass


def uimage_status(d):
    """(magic_ok, header_crc_ok, data_crc_ok) for a uImage at 0x54."""
    if len(d) < 0x54 + 64:
        return (False, False, False)
    magic, hcrc, _t, size, _l, _e, dcrc = struct.unpack_from(">7I", d, 0x54)
    if magic != UIMAGE_MAGIC:
        return (False, False, False)
    hdr = bytearray(d[0x54:0x54 + 64])
    hdr[4:8] = b"\0\0\0\0"
    hok = (zlib.crc32(bytes(hdr)) & 0xFFFFFFFF) == hcrc
    dok = False
    if size and 0x94 + size <= len(d):
        dok = (zlib.crc32(d[0x94:0x94 + size]) & 0xFFFFFFFF) == dcrc
    return (True, hok, dok)


def merge2(a, b, look=24):
    """Resync-merge two sequences, preferring agreement."""
    i = j = 0
    na, nb = len(a), len(b)
    out = bytearray()
    while i < na and j < nb:
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
            continue
        moved = False
        for d in range(1, look + 1):
            if i + d < na and a[i + d] == b[j]:
                i += d
                moved = True
                break
            if j + d < nb and a[i] == b[j + d]:
                j += d
                moved = True
                break
        if not moved:
            out.append(a[i])
            i += 1
            j += 1
    return bytes(out)


def candidates(reads, size):
    """Yield (label, bytes) candidates, trimmed to the partition size."""
    seen = set()

    def add(label, b):
        if len(b) > size:
            b = b[:size]
        if len(b) != size:
            return None
        h = zlib.crc32(b)
        if h in seen:
            return None
        seen.add(h)
        return (label, b)

    for k, r in enumerate(reads):
        c = add("read%d" % k, r)
        if c:
            yield c
    for k in range(len(reads)):
        for m in range(k + 1, len(reads)):
            c = add("merge%d+%d" % (k, m), merge2(reads[k], reads[m]))
            if c:
                yield c
    acc = reads[0]
    for r in reads[1:]:
        acc = merge2(acc, r)
    c = add("merge-all", acc)
    if c:
        yield c


def process(num, name, size, nreads):
    log("mtd%d %s: collecting %d reads (%d bytes each)" % (num, name, nreads, size))
    reads = []
    attempts = 0
    while len(reads) < nreads and attempts < nreads * 3:
        attempts += 1
        r = one_read(num, size)
        if r is None:
            log("  attempt %d: FAILED/short" % attempts)
            time.sleep(4.0)
            continue
        reads.append(r)
        log("  read %d: %d bytes (over by %+d)" % (len(reads), len(r), len(r) - size))
        time.sleep(3.0)

    if not reads:
        return None, "no reads"

    best = None
    best_info = None
    for label, cand in candidates(reads, size):
        magic, hok, dok = uimage_status(cand)
        if dok:
            best, best_info = cand, "%s: uImage CRC OK" % label
            break
        if magic and hok and best is None:
            best, best_info = cand, "%s: header OK, data CRC bad" % label
        elif best is None:
            best, best_info = cand, label

    if best is None:
        return None, "no candidate matched partition size"

    uniq = len({zlib.crc32(r[:size]) for r in reads if len(r) >= size})
    log("  -> %s | distinct reads: %d" % (best_info, uniq))

    fn = os.path.join(OUTDIR, "%02d-%s.bin" % (num, name))
    open(fn, "wb").write(best)
    return fn, best_info


def main():
    global HOST, PORT, OUTDIR
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--out", default=OUTDIR)
    ap.add_argument("--reads", type=int, default=None,
                    help="override reads per partition")
    ap.add_argument("--only", action="append", default=None,
                    help="partition name (repeatable)")
    args = ap.parse_args()

    HOST, PORT, OUTDIR = args.host, args.port, args.out
    os.makedirs(OUTDIR, exist_ok=True)

    todo = [p for p in PARTS if not args.only or p[1] in args.only]
    if not todo:
        log("no matching partitions; known: %s" % ", ".join(p[1] for p in PARTS))
        return 2

    results = {}
    for num, name, size, nr in todo:
        fn, info = process(num, name, size, args.reads or nr)
        results[name] = info
        log("  saved: %s" % (fn or "FAILED"))
    log("=== summary ===")
    for k, v in results.items():
        log("%-12s %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())

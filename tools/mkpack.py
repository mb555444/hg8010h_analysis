#!/usr/bin/env python3
"""mkpack.py -- build/parse Huawei HWNP ONT firmware packages.

Format reverse-engineered from the HG8012H package and cross-checked against
LeeXiaolan/hwfw-tool (GPLv2), which implements the same container:

  header (struct '<4s I i I i I 3H 6s', 0x24 bytes)
    0x00  magic              "HWNP"
    0x04  fileLength  BE     header + table + payload - 0x4C
    0x08  fileCrc     LE     crc32(header[0x0C:] + extra + items + data)
    0x0C  headerSize  LE     size of header + extra + item table
    0x10  headerCrc   LE     crc32(header[0x14:] + extra + items)
    0x14  itemCount   LE
    0x18  3H                 dummy, extraHeaderLength, itemSize
    0x1E  6s                 padding
  extra header (extraHeaderLength bytes)  -- product/variant id string
  items, itemSize bytes each, struct '<I i I I 256s 80s 2I'
    seq, crc32(payload), start offset, size, name[256], type[80], policy, unknown
  payloads

The -0x4C bias on fileLength is a genuine quirk of Huawei's packer; hwfw-tool
carries it as "FIXME: Can not find where does this bias come from" and it is
required for the device's length check.
"""

import struct
import sys
import zlib

HEADER_FMT = struct.Struct("<4sIiIiI3H6s")     # 0x24 (fileLength stored big-endian)
ITEM_FMT = struct.Struct("<IiII256s80s2I")      # 0x168
BIAS = 0x4C


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


def crc32(b):
    return zlib.crc32(b) & 0xFFFFFFFF


def cstr(b):
    return b.split(b"\0")[0].decode("latin1", errors="replace")


class Pack(object):
    def __init__(self):
        self.magic = b"HWNP"
        self.file_length = 0
        self.file_crc = 0
        self.header_size = 0
        self.header_crc = 0
        self.extra = b""
        self.items = []          # dicts: seq, crc, start, size, name, type, policy, unknown, data

    @classmethod
    def parse(cls, path):
        d = open(path, "rb").read()
        (magic, flen, fcrc, hsize, hcrc, count, _dummy, extra_len,
         item_size, _pad) = HEADER_FMT.unpack_from(d, 0)
        p = cls()
        p.magic = magic
        p.file_length = flen
        p.file_crc = fcrc
        p.header_size = hsize
        p.header_crc = hcrc
        p.extra = d[HEADER_FMT.size:HEADER_FMT.size + extra_len]
        base = HEADER_FMT.size + extra_len
        for i in range(count):
            off = base + i * item_size
            seq, crc, start, size, name, typ, policy, unk = ITEM_FMT.unpack_from(d, off)
            p.items.append({
                "seq": seq, "crc": crc, "start": start, "size": size,
                "name": cstr(name), "type": cstr(typ), "policy": policy,
                "unknown": unk, "data": d[start:start + size],
                "name_raw": name, "type_raw": typ,
            })
        return p

    def build(self):
        item_size = ITEM_FMT.size
        header_size = HEADER_FMT.size + len(self.extra) + len(self.items) * item_size

        # lay out payloads
        off = header_size
        for it in self.items:
            it["start"] = off
            it["size"] = len(it["data"])
            it["crc"] = crc32(it["data"])
            off += it["size"]
        payload_len = off - header_size

        # fileLength: header+table+payload, minus Huawei's 0x4C bias, big-endian
        flen = header_size - BIAS + payload_len

        def header_bytes(file_len, file_crc, hdr_crc, hdr_size, count):
            return (self.magic
                    + struct.pack(">I", file_len & 0xFFFFFFFF)
                    + struct.pack("<i", s32(file_crc))
                    + struct.pack("<I", hdr_size)
                    + struct.pack("<i", s32(hdr_crc))
                    + struct.pack("<I", count)
                    + struct.pack("<3H", 0, len(self.extra), item_size)
                    + b"\0" * 6)

        items_blob = b"".join(ITEM_FMT.pack(
            it["seq"], s32(it["crc"]), it["start"], it["size"],
            it["name_raw"], it["type_raw"], it["policy"], it["unknown"])
            for it in self.items)

        # headerCrc covers header from 0x14 + extra + item table
        h0 = header_bytes(0, 0, 0, header_size, len(self.items))
        hdr_crc = crc32(h0[0x14:] + self.extra + items_blob)
        h1 = header_bytes(0, 0, hdr_crc, header_size, len(self.items))

        # fileCrc covers header from 0x0C + extra + item table + payload
        file_crc = crc32(h1[0x0C:] + self.extra + items_blob +
                         b"".join(it["data"] for it in self.items))
        h2 = header_bytes(flen, file_crc, hdr_crc, header_size, len(self.items))

        return h2 + self.extra + items_blob + b"".join(it["data"] for it in self.items)

    def describe(self):
        print("fileLength 0x%x (bias 0x4C)  headerSize 0x%x  items %d  extra %d"
              % (self.file_length, self.header_size, len(self.items), len(self.extra)))
        print("%-3s %-34s %-12s %-10s %-10s %s" %
              ("seq", "name", "type", "offset", "size", "crc"))
        for it in self.items:
            print("%-3d %-34s %-12s 0x%08x 0x%08x %08x" %
                  (it["seq"], it["name"], it["type"], it["start"], it["size"], it["crc"]))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "info":
        Pack.parse(argv[1]).describe()
        return 0
    if cmd == "roundtrip":
        p = Pack.parse(argv[1])
        out = p.build()
        orig = open(argv[1], "rb").read()
        ok = out == orig
        print("rebuilt from scratch: %d bytes, byte-identical to input: %s"
              % (len(out), ok))
        if not ok:
            for i in range(min(len(out), len(orig))):
                if out[i] != orig[i]:
                    print("first difference at 0x%x: %02x vs %02x"
                          % (i, out[i], orig[i]))
                    break
        open(argv[2], "wb").write(out)
        return 0 if ok else 1
    if cmd == "replace":
        # replace <pack> <out> <item-name> <file>
        p = Pack.parse(argv[1])
        tgt = next((it for it in p.items if it["name"] == argv[3]), None)
        if not tgt:
            raise SystemExit("no item %r" % argv[3])
        data = open(argv[4], "rb").read()
        tgt["data"] = data
        tgt["name_raw"] = tgt["name"].encode().ljust(256, b"\0")[:256]
        tgt["type_raw"] = tgt["type"].encode().ljust(80, b"\0")[:80]
        out = p.build()
        open(argv[2], "wb").write(out)
        print("replaced %s with %d bytes -> %s (%d bytes total)"
              % (argv[3], len(data), argv[2], len(out)))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

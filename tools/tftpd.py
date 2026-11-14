#!/usr/bin/env python3
"""tftpd.py -- minimal read-only TFTP server (stdlib only).

Serves files from a directory so the ONT can do:
    load pack by tftp svrip <this host> remotefile <name>

Handles RRQ, OACK for blksize/timeout negotiation, per-block ACKs and
retransmission. Read-only by design: no WRQ path exists.
"""

import argparse
import os
import socket
import struct
import sys
import time

OP_RRQ, OP_DATA, OP_ACK, OP_ERROR, OP_OACK = 1, 3, 4, 5, 6


def log(msg):
    sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


def parse_rrq(data):
    parts = data[2:].split(b"\0")
    if len(parts) < 2:
        return None, None, {}
    name = parts[0].decode(errors="replace")
    mode = parts[1].decode(errors="replace").lower()
    opts = {}
    rest = parts[2:]
    for i in range(0, len(rest) - 1, 2):
        k = rest[i].decode(errors="replace").lower()
        v = rest[i + 1].decode(errors="replace")
        if k:
            opts[k] = v
    return name, mode, opts


def serve_file(clsock, addr, path, opts):
    blksize = 512
    timeout = 3.0
    oack = {}
    if "blksize" in opts:
        try:
            blksize = max(64, min(1468, int(opts["blksize"])))
            oack["blksize"] = str(blksize)
        except ValueError:
            pass
    if "timeout" in opts:
        try:
            timeout = max(1.0, min(30.0, float(opts["timeout"])))
            oack["timeout"] = str(int(timeout))
        except ValueError:
            pass

    with open(path, "rb") as f:
        payload = f.read()
    nblocks = (len(payload) + blksize - 1) // blksize or 1

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    sock.bind(("", 0))

    if oack:
        items = sorted(oack.items())
        pkt = struct.pack("!H", OP_OACK) + b"".join(
            k.encode() + b"\0" + v.encode() + b"\0" for k, v in items)
        expect_block = 0
        sock.sendto(pkt, addr)
    else:
        expect_block = 1   # first DATA is block 1, ack 0 not needed

    block = 1
    sent = 0
    t0 = time.time()
    while block <= nblocks:
        chunk = payload[(block - 1) * blksize: block * blksize]
        pkt = struct.pack("!HH", OP_DATA, block & 0xFFFF) + chunk
        for attempt in range(5):
            sock.sendto(pkt, addr)
            try:
                resp, peer = sock.recvfrom(512)
            except socket.timeout:
                continue
            if len(resp) < 4:
                continue
            op, num = struct.unpack("!HH", resp[:4])
            if op == OP_ERROR:
                log("peer sent ERROR on block %d" % block)
                sock.close()
                return False
            if op == OP_ACK and (num & 0xFFFF) == (block & 0xFFFF):
                break
        else:
            log("timeout on block %d, aborting" % block)
            sock.close()
            return False
        sent += len(chunk)
        block += 1
        if block % 500 == 0:
            log("  sent block %d/%d (%.1f%%)" % (block, nblocks, 100.0 * sent / len(payload)))

    sock.close()
    log("done: %s -> %s:%d, %d bytes, %d blocks, %.1fs"
        % (os.path.basename(path), addr[0], addr[1], sent, nblocks, time.time() - t0))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-d", "--dir", default=".")
    ap.add_argument("-p", "--port", type=int, default=69)
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", args.port))
    log("tftpd listening on udp/%d serving %s" % (args.port, args.dir))

    while True:
        data, addr = s.recvfrom(2048)
        if len(data) < 2:
            continue
        op = struct.unpack("!H", data[:2])[0]
        if op != OP_RRQ:
            s.sendto(struct.pack("!HH", OP_ERROR, 4) + b"only RRQ\0", addr)
            continue
        name, mode, opts = parse_rrq(data)
        safe = os.path.basename(name or "")
        path = os.path.join(args.dir, safe)
        log("RRQ %r from %s:%d mode=%s opts=%s" % (name, addr[0], addr[1], mode, opts))
        if not safe or not os.path.isfile(path):
            log("  not found: %s" % path)
            s.sendto(struct.pack("!HH", OP_ERROR, 1) + b"not found\0", addr)
            continue
        try:
            serve_file(s, addr, path, opts)
        except Exception as e:
            log("  transfer error: %r" % e)


if __name__ == "__main__":
    main()

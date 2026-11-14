# Tools

Everything needed to reproduce the work on an HG8010H. `make` rebuilds both
binaries byte-identically to the ones shipped here (needs `arm-none-eabi-gcc`).

| File | What it does |
|---|---|
| `shellsvc.c` / `shellsvc` | root shell service on TCP **2323** (pty, foreground pgrp, SIGINT reset) |
| `flashpull.c` / `flashpull` | streams `/dev/mtd0` (16 MB) to `192.168.100.10:7000` |
| `mkpack.py` | parse / rebuild Huawei `HWNP` firmware packages |
| `tftpd.py` | minimal read-only TFTP server, for `load pack by tftp` |
| `readflash.py` | dump flash over the stock CLI: multi-read + resync-merge + CRC validation |

## Root shell

```sh
# on the device (root shell not yet available: use the CLI bypass first)
tftp -g -l /tmp/shellsvc -r shellsvc 192.168.100.10
chmod +x /tmp/shellsvc && /tmp/shellsvc &
# or persist it: install to /bin and start from /etc/rc.d/rc.start/1.sdk_init.sh

telnet <ont> 2323
```

## Pulling the flash

```sh
# host
python3 /path/to/listen.py full.bin 7000      # see ../src/listen.py
# device
tftp -g -l /tmp/flashpull -r flashpull 192.168.100.10
chmod +x /tmp/flashpull && /tmp/flashpull
```

`flashpull` has the host and port compiled in (`192.168.100.10:7000`); edit the
two constants at the top of `_start()` if yours differ.

## Reading the flash without a root shell

Only the `WAP>` / `SU_WAP>` CLI is needed — no shell, no clip, no programmer.

```sh
python3 readflash.py --host 192.168.100.1 --out ./dump
python3 readflash.py --only kernelA --only rootfsB --reads 6
```

`display file` corrupts binary (a stray `0x0D` appears before `0x0A`, and it
varies per transfer), so each partition is read several times and the variants
are resync-merged. The winning candidate is the one whose uImage header and
data CRC validate; partitions without a checksum (startcode, bootA/B, config)
report how many reads agreed instead. Expect ~30 s per 256 KiB partition.

Once a root shell is available, `flashpull` is much faster and byte-exact.

## Packing firmware

```sh
python3 mkpack.py info hg8012h.bin                  # header + item table
python3 mkpack.py roundtrip hg8012h.bin rebuilt.bin # must be byte-identical
```

`roundtrip` is the correctness test: a correct implementation reproduces the
reference package exactly, including the `fileLength` big-endian value with its
`-0x4C` bias.

Serve it and flash:

```sh
python3 tftpd.py -d . -p 69
# on the ONT:
#   set flashlock status 0
#   load pack by tftp svrip <host> remotefile <pack>
#   display progress load
```

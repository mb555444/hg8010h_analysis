# Firmware

`shell-pack.pack` — the final build. Adds a persistent root shell on TCP **2323**
to a stock HG8010H.

| | |
|---|---|
| File | `shell-pack.pack` |
| Size | 5,517,285 bytes |
| md5 | `382cd91c20386962a0e4785cdc4958e6` |
| Item count | 7 — no `flash:uboot`, no `startcode` |

## What it changes

Only the userspace, inside `rootfsA`/`rootfsB` (squashfs). The kernel and the
entire bootloader are untouched.

```
/bin/shellsvc                     new: 68 KB static ARM binary, pty shell server
/bin/getcustominfo.sh             replaced: exec /bin/sh -i
/etc/rc.d/rc.start/1.sdk_init.sh  +2 lines: start /bin/shellsvc at boot
```

`shellsvc` listens on 2323, allocates a pty per connection, sets itself as the
terminal's foreground process group, resets `SIGINT`/`SIGQUIT` to `SIG_DFL`
(init starts it with `&`, which would otherwise make everything ignore Ctrl-C),
and relays socket ↔ pty. Result: echo, line editing, job control, working
`Ctrl-C`, and concurrent sessions. Sessions are torn down when the client
disconnects (SIGHUP to the session, children reaped), so nothing accumulates.

## Flash it

Requirements: the ONT at `192.168.100.1`, your host at `192.168.100.10`
(other addresses work — change `svrip`).

```sh
# host: serve the pack
python3 tools/tftpd.py -d firmware -p 69

# ONT: telnet 192.168.100.1, login root/admin
su
set flashlock status 0
load pack by tftp svrip 192.168.100.10 remotefile shell-pack.pack
display progress load
```

Success looks like:

```
Rate of process:100%
Software Operation Successful!RetCode=0x0!
```

Then **power-cycle** — the new rootfs is not active until reboot.

## Use it

```sh
telnet 192.168.100.1 2323      # root shell, no login
```

Check it landed:

```sh
ls -l /bin/shellsvc            # expect 68636
ps | grep shellsvc
```

## Recovery

The pack writes the *active* rootfs bank. If it goes wrong the unit will not
boot, so keep one of these before flashing:

| Recovery | Notes |
|---|---|
| `../packs/stock-replica.pack` | same container, original kernel + rootfs, no shell. Flash the same way if the device still boots to the CLI. |
| `../dumps/fresh-dump/full.bin` | complete 16 MB image, all CRCs OK. Needs a working `load pack` or a programmer to restore. |

Nothing here can recover a bad `startcode`/`bootA`/`bootB` — this pack never
touches them, and neither should you.

## Rebuild it

```sh
# unpack the shell rootfs, patch, repack as root (keeps the 66 /dev nodes),
# then wrap it back into a package
python3 tools/mkpack.py roundtrip ../refs/hg8012h.bin /tmp/check.bin   # sanity
```

See `../docs/INDEX.md` and the sources in `../src/` for the full recipe
(`mkroot.sh`, `build_patched_pack.py`).

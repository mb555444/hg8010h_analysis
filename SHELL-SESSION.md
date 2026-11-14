# Demo: getting a root shell

Two steps. The first is the stock CLI; the second is the patched firmware from
[`FIRMWARE.md`](FIRMWARE.md).

## 1. Stock CLI

```console
$ telnet 192.168.100.1
Welcome Visiting Huawei Home Gateway
Copyright by Huawei Technologies Co., Ltd.

Login:root
Password:
Password is default value, please modify it!
WAP>su
success!
SU_WAP>
```

`SU_WAP>` is Huawei's management shell. The Linux underneath is reachable, but
`shell` is a dead end — it is a real shell with `stdin` at EOF:

```console
SU_WAP>shell
profile close core dump
WAP(Dopra Linux) #
WAP(Dopra Linux) # ls
ERROR::Command is not existed
```

`display file` is where the CLI becomes useful: it reads any path under `/proc`,
and `/proc/self/root` resolves to the real filesystem.

```console
SU_WAP>display file /proc/mtd
dev:    size   erasesize  name
mtd0: 01000000 00040000 "hi_sfc"
mtd1: 00040000 00040000 "startcode"
...
SU_WAP>display file /proc/self/root/dev/mtd1
<raw flash bytes>
```

## 2. Patched firmware: root shell on 2323

After flashing `firmware/shell-pack.pack` and rebooting:

```console
$ telnet 192.168.100.1 2323
/ # cat /proc/version
Linux version 3.10.53-HULK2 (ci@SZX1000044416) (gcc version 4.7.1 (SDK V100R005C00SPC030B050) ) #1 Fri Oct 20 00:54:31 CST 2017
```

No login, no restrictions. The flash is a normal set of block devices:

```console
/ # cat /proc/mtd
dev:    size   erasesize  name
mtd0: 01000000 00040000 "hi_sfc"
mtd1: 00040000 00040000 "startcode"
mtd2: 00040000 00040000 "bootA"
mtd3: 00040000 00040000 "bootB"
mtd4: 00040000 00040000 "flashcfg"
mtd5: 00040000 00040000 "slave_param"
mtd6: 00200000 00040000 "kernelA"
mtd7: 00200000 00040000 "kernelB"
mtd8: 00480000 00040000 "rootfsA"
mtd9: 00480000 00040000 "rootfsB"
mtd10: 00180000 00040000 "file_system"
mtd11: 00040000 00040000 "reserved"
```

`mtd0` is the whole 16 MiB chip; the rest are partitions. Reading them is just
`cat`:

```console
/ # hexdump -C -n 32 /dev/mtdblock1
00000000  14 00 00 ea 14 f0 9f e5  14 f0 9f e5 14 f0 9f e5  |................|
00000010  14 f0 9f e5 14 f0 9f e5  14 f0 9f e5 14 f0 9f e5  |................|
```

`14 00 00 ea` is `EA000014`, an ARM branch — followed by the exception vector
table. That is `startcode`, the first stage, healthy and readable.

The service is running and the session accounting is clean:

```console
/ # ls -l /bin/shellsvc
-rwxr-xr-x    1     68636 /bin/shellsvc

/ # ps | grep shellsvc
  237 root       0:00 /bin/shellsvc      <- listener
  414 root       0:00 /bin/shellsvc      <- relay for this session
  415 root       0:00 /bin/shellsvc      <- relay for this session
  420 root       0:00 grep shellsvc
```

Nothing accumulates: a session's relays and shell are torn down when the client
disconnects.

Networking and memory are ordinary Linux:

```console
/ # ifconfig br0
br0       Link encap:Ethernet  HWaddr E8:4D:D0:E5:62:C3
          inet addr:192.168.100.1  Bcast:192.168.100.255  Mask:255.255.255.0
          UP BROADCAST RUNNING MULTICAST  MTU:1500  Metric:1

/ # free
             total         used         free       shared      buffers
Mem:        118108        18468        99640            0          768
-/+ buffers:              17700       100408
Swap:            0            0            0
```

## Environment notes

- busybox is minimal: no `dd`, `md5sum`, `id`, `uname`, `tty`, `head`. Use
  `cat /dev/mtdblockN`, and ship a static binary when you need more —
  `tools/flashpull` streams the whole chip to a host over TCP.
- The rootfs is read-only squashfs; `/var` is tmpfs; only `/mnt/jffs2` and the
  flash itself survive a reboot.
- Sessions are independent — a hung program in one does not block another, and
  `Ctrl-C` works.

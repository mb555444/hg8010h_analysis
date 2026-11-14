/*
 * flashpull.c -- stream the whole SPI flash to a host over TCP, no libc.
 *
 * Reads /dev/mtd0 (the 16 MiB master MTD) and writes it verbatim to
 * 192.168.100.10:7000. No arguments: keeps the syscall surface tiny.
 *
 * Build:
 *   arm-none-eabi-gcc -O2 -nostdlib -nostartfiles -static -marm \
 *       -mcpu=arm926ej-s -Wl,-Ttext=0x20000 -o flashpull flashpull.c -Wl,-e,_start
 */

#define SYS_exit    1
#define SYS_read    3
#define SYS_write   4
#define SYS_open    5
#define SYS_close   6
#define SYS_socket  281
#define SYS_connect 283

static inline long sys6(long n, long a, long b, long c, long d, long e, long f)
{
    register long r0 __asm__("r0") = a;
    register long r1 __asm__("r1") = b;
    register long r2 __asm__("r2") = c;
    register long r3 __asm__("r3") = d;
    register long r4 __asm__("r4") = e;
    register long r5 __asm__("r5") = f;
    register long r7 __asm__("r7") = n;
    __asm__ volatile("svc #0"
                     : "+r"(r0)
                     : "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5), "r"(r7)
                     : "memory");
    return r0;
}

#define sys1(n, a)        sys6(n, (long)(a), 0, 0, 0, 0, 0)
#define sys2(n, a, b)     sys6(n, (long)(a), (long)(b), 0, 0, 0, 0)
#define sys3(n, a, b, c)  sys6(n, (long)(a), (long)(b), (long)(c), 0, 0, 0)

struct sockaddr_in {
    unsigned short sin_family;
    unsigned short sin_port;   /* network byte order */
    unsigned int   sin_addr;   /* network byte order */
    char           pad[8];
};

void _start(void)
{
    struct sockaddr_in sa;
    static char buf[65536];
    long fd, s, n;

    sa.sin_family = 2;              /* AF_INET */
    sa.sin_port = 0x581B;           /* htons(7000) */
    sa.sin_addr = 0x0A64A8C0;       /* htonl(192.168.100.10) */
    for (int i = 0; i < 8; i++) sa.pad[i] = 0;

    fd = sys3(SYS_open, "/dev/mtd0", 0, 0);   /* O_RDONLY */
    if (fd < 0) sys1(SYS_exit, 1);

    s = sys3(SYS_socket, 2, 1, 0);            /* AF_INET, SOCK_STREAM */
    if (s < 0) sys1(SYS_exit, 2);

    if (sys3(SYS_connect, s, &sa, sizeof(sa)) < 0) sys1(SYS_exit, 3);

    for (;;) {
        n = sys3(SYS_read, fd, buf, sizeof(buf));
        if (n <= 0) break;
        long off = 0;
        while (off < n) {
            long w = sys3(SYS_write, s, buf + off, n - off);
            if (w <= 0) sys1(SYS_exit, 4);
            off += w;
        }
    }
    sys1(SYS_close, s);
    sys1(SYS_close, fd);
    sys1(SYS_exit, 0);
}

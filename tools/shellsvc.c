/*
 * shellsvc.c -- minimal ARM Linux TCP shell service with a pty, no libc.
 *
 * A plain socket gives the shell no tty: no echo, and CRLF input arrives as
 * "cmd\r" so busybox tries to run "ls\r". Allocating a pty fixes both - the
 * line discipline echoes, translates CR to NL, and gives job control.
 *
 * Build:
 *   arm-none-eabi-gcc -O2 -nostdlib -nostartfiles -static -marm \
 *       -mcpu=arm926ej-s -Wl,-Ttext=0x20000 -o shellsvc shellsvc.c -Wl,-e,_start
 */

#define SYS_exit    1
#define SYS_fork    2
#define SYS_read    3
#define SYS_write   4
#define SYS_open    5
#define SYS_close   6
#define SYS_execve  11
#define SYS_ioctl   54
#define SYS_dup2    63
#define SYS_setsid  66
#define SYS_setpgid 57
#define SYS_getpid  20
#define SYS_rt_sigaction 67
#define SYS_kill     37
#define SYS_wait4    114
#define SYS_setsockopt 294
#define SYS_socket  281
#define SYS_bind    282
#define SYS_listen  284
#define SYS_accept  285

#define O_RDWR      2
#define O_NOCTTY    0x100

#define TIOCSCTTY   0x540E
#define TIOCSPGRP   0x5410
#define TIOCSPTLCK  0x40045431
#define TIOCGPTN    0x80045430

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
    unsigned short sin_port;
    unsigned int   sin_addr;
    char           pad[8];
};


/* init starts us with `&`, so SIGINT/SIGQUIT are inherited as SIG_IGN, and
 * ignored signals survive exec(). Reset them to SIG_DFL in the child or every
 * shell and program we spawn will ignore Ctrl-C. */
struct ksa { unsigned long handler, flags, restorer, mask; };

static void reset_sig(long sig)
{
    struct ksa sa;
    sa.handler = 0;   /* SIG_DFL */
    sa.flags = 0;
    sa.restorer = 0;
    sa.mask = 0;
    sys6(SYS_rt_sigaction, sig, (long)&sa, 0, 8, 0, 0);
}

static unsigned short htons(unsigned short v)
{
    return (unsigned short)((v << 8) | (v >> 8));
}

static long g_shell_pid;   /* shell to hang up when the client goes away */

static const char shell_path[] = "/bin/sh";
static char *argv[] = { (char *)shell_path, (char *)"-i", 0 };
static char *envp[] = {
    (char *)"PATH=/bin:/usr/bin:/sbin:/usr/sbin:/bin/exbin",
    (char *)"HOME=/",
    (char *)"TERM=vt100",
    (char *)"LD_LIBRARY_PATH=/lib:/lib/exlib:/lib/omci_module:/lib/oam_module",
    0
};

/* open a pty pair; returns master fd, writes slave path into buf */
static long open_pty(char *buf)
{
    long m = sys3(SYS_open, "/dev/ptmx", O_RDWR | O_NOCTTY, 0);
    int zero = 0;
    int n = 0;
    int i = 9;

    if (m < 0) {
        return -1;
    }
    if (sys3(SYS_ioctl, m, TIOCSPTLCK, &zero) < 0) {
        sys1(SYS_close, m);
        return -1;
    }
    if (sys3(SYS_ioctl, m, TIOCGPTN, &n) < 0) {
        sys1(SYS_close, m);
        return -1;
    }
    /* "/dev/pts/" + decimal n */
    buf[0] = '/'; buf[1] = 'd'; buf[2] = 'e'; buf[3] = 'v';
    buf[4] = '/'; buf[5] = 'p'; buf[6] = 't'; buf[7] = 's'; buf[8] = '/';
    if (n >= 100) {
        buf[i++] = (char)('0' + (n / 100) % 10);
    }
    if (n >= 10) {
        buf[i++] = (char)('0' + (n / 10) % 10);
    }
    buf[i++] = (char)('0' + n % 10);
    buf[i] = 0;
    return m;
}

static void relay(long from, long to)
{
    char b[512];
    for (;;) {
        long r = sys3(SYS_read, from, b, sizeof(b));
        if (r == 0) {
            break;                      /* real EOF */
        }
        if (r < 0) {
            /* the accept socket's SO_RCVTIMEO is inherited, so EAGAIN/AINT
             * must not be treated as EOF or every session dies after 2 s */
            if (r == -11 /*EAGAIN*/ || r == -4 /*EINTR*/) {
                continue;
            }
            break;
        }
        long off = 0;
        while (off < r) {
            long w = sys3(SYS_write, to, b + off, r - off);
            if (w < 0 && (w == -11 || w == -4)) {
                continue;
            }
            if (w <= 0) {
                sys1(SYS_exit, 0);
            }
            off += w;
        }
    }
    /* client gone: hang up the shell so it does not linger as an orphan */
    if (g_shell_pid > 0) {
        sys2(SYS_kill, g_shell_pid, 1);   /* SIGHUP */
    }
    sys1(SYS_exit, 0);
}

void _start(void)
{
    struct sockaddr_in sa;
    char ptspath[32];
    long s, c, m, sl;

    sa.sin_family = 2;
    sa.sin_port = htons(2323);
    sa.sin_addr = 0;
    sa.pad[0] = sa.pad[1] = sa.pad[2] = sa.pad[3] = 0;
    sa.pad[4] = sa.pad[5] = sa.pad[6] = sa.pad[7] = 0;

    s = sys3(SYS_socket, 2, 1, 0);
    if (s < 0) {
        sys1(SYS_exit, 1);
    }
    if (sys3(SYS_bind, s, &sa, sizeof(sa)) < 0) {
        sys1(SYS_exit, 2);
    }
    if (sys3(SYS_listen, s, 4, 0) < 0) {
        sys1(SYS_exit, 3);
    }
    /* wake the accept loop periodically so finished children get reaped */
    {
        int tv[2];
        tv[0] = 2;   /* seconds */
        tv[1] = 0;
        sys6(SYS_setsockopt, s, 1 /*SOL_SOCKET*/, 20 /*SO_RCVTIMEO*/,
             (long)tv, 8, 0);
    }

    for (;;) {
        /* reap finished shells/relays: SIGHUP kills them, this frees the PID */
        while (sys6(SYS_wait4, -1, 0, 1 /*WNOHANG*/, 0, 0, 0) > 0) {
        }
        c = sys3(SYS_accept, s, 0, 0);
        if (c < 0) {
            continue;
        }
        m = open_pty(ptspath);
        if (m < 0) {
            sys1(SYS_close, c);
            continue;
        }
        sl = sys3(SYS_open, ptspath, O_RDWR | O_NOCTTY, 0);
        if (sl < 0) {
            sys1(SYS_close, m);
            sys1(SYS_close, c);
            continue;
        }
        long shell_pid = sys1(SYS_fork, 0);
        if (shell_pid == 0) {
            sys1(SYS_setsid, 0);
            sys3(SYS_ioctl, sl, TIOCSCTTY, 0);
            /* make the shell the foreground process group of the pty, otherwise
             * tcsetattr() gets SIGTTOU (top stays in canonical mode: needs Enter)
             * and Ctrl-C is never delivered to its children. */
            sys2(SYS_setpgid, 0, 0);
            {
                long pg = sys1(SYS_getpid, 0);
                sys3(SYS_ioctl, sl, TIOCSPGRP, &pg);
            }
            reset_sig(2);   /* SIGINT  */
            reset_sig(3);   /* SIGQUIT */
            sys2(SYS_dup2, sl, 0);
            sys2(SYS_dup2, sl, 1);
            sys2(SYS_dup2, sl, 2);
            sys1(SYS_close, sl);
            sys1(SYS_close, m);
            sys3(SYS_execve, shell_path, argv, envp);
            sys1(SYS_exit, 127);
        }
        sys1(SYS_close, sl);
        g_shell_pid = shell_pid;
        if (sys1(SYS_fork, 0) == 0) {
            relay(m, c);          /* pty -> socket */
        }
        if (sys1(SYS_fork, 0) == 0) {
            relay(c, m);          /* socket -> pty */
        }
        /* parent keeps accepting: each session now has its own relay forks */
        sys1(SYS_close, c);
        sys1(SYS_close, m);
    }
}

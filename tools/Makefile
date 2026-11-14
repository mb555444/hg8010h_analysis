CROSS   ?= arm-none-eabi-
CC      := $(CROSS)gcc
CFLAGS  ?= -O2 -nostdlib -nostartfiles -static -marm \
           -mcpu=arm926ej-s -Wl,-Ttext=0x20000
LDFLAGS := -Wl,-e,_start

BINS := shellsvc flashpull

all: $(BINS)

shellsvc: shellsvc.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

flashpull: flashpull.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(BINS)

.PHONY: all clean

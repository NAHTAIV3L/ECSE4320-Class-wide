CC ?= gcc
CFLAGS ?= -O3 -march=native -std=c11 -Wall -Wextra -Wno-unused-parameter
LDFLAGS ?= -lm
BIN = membench

all: $(BIN)

$(BIN): src/membench.c
	$(CC) $(CFLAGS) $< -o $@ $(LDFLAGS)

clean:
	rm -f $(BIN)

.PHONY: all clean

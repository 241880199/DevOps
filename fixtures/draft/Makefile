CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra
TARGET  := hello

all: $(TARGET)

$(TARGET): main.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f $(TARGET)

.PHONY: all clean

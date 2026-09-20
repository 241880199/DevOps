CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra
TARGET  := app

all: $(TARGET)

$(TARGET): main.o
	$(CC) $(CFLAGS) -o $@ $^
main.o: main.c unused.h
	$(CC) $(CFLAGS) -c main.c -o main.o

clean:
	rm -f $(TARGET) main.o

.PHONY: all clean

CC ?= cc
CFLAGS = -O0
all: app
app: main.o
	$(CC) $(CFLAGS) main.o -o app
main.o: main.c config.h
	$(CC) $(CFLAGS) -c main.c -o main.o
clean:
	rm -f main.o app
.PHONY: all clean

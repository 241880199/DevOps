#include <stdio.h>
#include "config.h"
#ifndef MODE
#define MODE 0
#endif
int main(void) { printf("%d\n", BASE + MODE); return 0; }

#include <stdio.h>
#include "config.h"
#include "feature.h"
#ifndef MODE
#define MODE 0
#endif
int main(void) { printf("%d\n", BASE + FEATURE + MODE); return 0; }

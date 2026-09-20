/* fixtures/mdfixer 的主源文件。
 *
 * 本文件读取 config.h 与 feature.h；Makefile 的 main.o 规则却只声明了
 * main.c 与 unused.h。于是同一个目标上同时存在两类缺陷：
 *
 *   MISSING   : config.h、feature.h 被读取但未声明
 *   REDUNDANT : unused.h 被声明但从未读取
 *
 * 预期程序输出：BASE + FEATURE = 12
 */

#include <stdio.h>

#include "config.h"
#include "feature.h"

int main(void)
{
    printf("%d\n", BASE + FEATURE);
    return 0;
}

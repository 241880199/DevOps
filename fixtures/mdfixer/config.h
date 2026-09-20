#ifndef CONFIG_H
#define CONFIG_H

/* 被 main.c 读取，但 Makefile 的 main.o 规则未声明 —— MISSING 发现之一。
 *
 * 后果：只改本文件不会触发 main.o 重建，增量构建会输出旧值。
 * 有了这条声明后，改本文件应当触发 cc -c main.c -o main.o 并重新链接。
 */
#define BASE 10

#endif

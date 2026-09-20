#ifndef UNUSED_H
#define UNUSED_H

/* 被 Makefile 声明为 main.o 的依赖，但没有任何源文件 include —— REDUNDANT 发现。
 *
 * 后果：只改本文件（哪怕只改注释）也会触发一次多余的重新编译与链接。
 *
 * 参考补丁**不得**删除这条声明：修复只针对 MISSING，冗余声明的去留由
 * 项目维护者决定。validate.py 检查 11 会核验补丁确实没有碰它。
 */
#define UNUSED 0

#endif

#ifndef FEATURE_H
#define FEATURE_H

/* 被 main.c 读取，但 Makefile 的 main.o 规则未声明 —— MISSING 发现之二。
 *
 * 与 config.h 同类，且在参考补丁里一并补上：一个目标的依赖列表缺了多项时，
 * 补丁必须全部补齐，只补一项会让复检仍报出剩余缺失。
 */
#define FEATURE 2

#endif

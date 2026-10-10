# E3 A 组 MD/RD 人工基线

来源：课程 E4 实验包的 A-buildchecker/fixtures/md-rd；用于 E3 行为对照和 E4 冒烟。

范围为 `main.o` 的项目头文件：`main.c` 使用 `config.h`，规则却只声明 `main.c unused.h`。
`oracle.json` 是根据源码人工核对的预期答案，来源标记 MANUAL，不能计入检测器准确率。

Linux 中执行 `make && ./app`，预期输出 `1`。将 config.h 的 VALUE 改成 2，再执行
`make && ./app`，预期仍为 `1`；执行 `make clean && make && ./app` 后预期为 `2`。
只修改 unused.h 的注释应触发多余的编译，程序输出保持 `2`。

仓库根目录的 `python3 scripts/run_e3.py --student-id 241880444` 自动在 work 下的副本
执行上述步骤，保存命令、日志、退出码、文件摘要和 Linux 原始跟踪，不改本目录。

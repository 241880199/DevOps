# E3 A 组连续版本

这里保存三份源码快照。`run_e3.py` 将它们变成一次运行中的独立 Git 样例仓库，
创建真实的 C0 → C1 → C2 连续提交和同名标签，并导出 `fixture-history.bundle`。
它们是样例仓库的版本，完整 SHA 和父子关系记录在 `commits.json`，不是主仓库的标签。

- C0 声明正确，默认 MODE=0，输出 10。
- C1 只增加 main.c 中对 feature.h 的使用及 feature.h 文件，保持 Makefile 不变，输出 12。
- C2 源码与 C1 完全一致，只改 Makefile 的 CFLAGS 为 `-O0 -DMODE=7`。
  保留 C1 产物进入 C2，增量输出 12，clean 输出 19。

`oracle.json` 是人工预期，不是工具运行结果。实际命令、输出、退出码和原始日志放在
每次运行的 work/e3 目录中。每次生成的样例提交时间不同，SHA 可能不同；交付时用
该次运行的 bundle 保存实际提交。别人可用 `git clone fixture-history.bundle fixture`
检出相同 SHA，而不是把另一次生成的提交误当成本次版本。

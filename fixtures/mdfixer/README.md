# fixtures/mdfixer

REPAIR（缺失依赖修复）的**固定输入样例**：一个同时含一处冗余声明与两处缺失声明的
最小 GNU Make C 项目。

它回答的是修复服务的第一个问题——「**我拿到的是什么**」。`contracts/samples/` 里的
请求样例只说明字段长什么样；本目录提供请求所指的真实源码、真实报告和真实补丁，
让「报告属于当前源码版本」这类断言可以被实际核验，而不必相信。

## 项目信息

| 项 | 值 |
| --- | --- |
| 语言 / 构建系统 | C / GNU Make |
| 源文件 | `main.c`、`config.h`、`feature.h`、`unused.h` |
| 外部依赖 | 无（仅 libc） |
| 构建命令 | `make` |
| 可执行产物 | `app` |
| 功能验证 | `./app`，预期标准输出恰好一行 `12` |

## 故意埋入的声明缺陷

`main.c` 读取 `config.h` 与 `feature.h`；`Makefile` 第 9 行的 `main.o` 规则却只声明了
`main.c` 与 `unused.h`：

```make
main.o: main.c unused.h        # 第 9 行
```

| 类型 | 依赖 | 说明 | 后果 |
| --- | --- | --- | --- |
| `MISSING` | `config.h` | 被读取，未声明 | 只改头文件不触发重建，增量构建输出旧值 |
| `MISSING` | `feature.h` | 被读取，未声明 | 同上 |
| `REDUNDANT` | `unused.h` | 已声明，从未被读取 | 只改它也会触发多余的重编译 |

本目录下有**两份**报告，用途不同，不要混用：

| 文件 | 来源 | 用途 |
| --- | --- | --- |
| `error-report.json` | 全部 `INSTRUCTOR_ORACLE`，`mode: MANUAL` | **本项目的固定人工报告**。MDFixer 的输入就是它，REPAIR 请求样例指向的也是它 |
| `contracts/samples/error-report.json` | 混合 `TOOL` 与 `INSTRUCTOR_ORACLE` | 契约样例。它演示的是「同一份报告里两类发现、两种来源并存」这一**形状**，不是本项目的答案 |

两份报告的 `location.file` 都是 `Makefile`、`location.line` 都是 `9`，指向上面那一行——
`scripts/validate.py` 检查 11 会核验该行确实是 `main.o` 的规则行，并核验每条发现的
`MISSING` / `REDUNDANT` 定性在源码里数得出来（缺失的确实被 include，冗余的确实没被 include）。

## 参考修复

`reference.patch` 以 `TARGET` 风格（原子依赖列表）直接补齐两项缺失依赖：

```bash
cp -R fixtures/mdfixer /tmp/mdfixer-check
cd /tmp/mdfixer-check
git apply --check reference.patch     # 先核验能否干净应用
git apply reference.patch
make clean && make && ./app           # 预期输出 12
```

补丁**不得**改动 `unused.h`——修复只针对缺失依赖，冗余声明的去留由维护者决定。
`scripts/validate.py` 检查 11 会核验这一点。

### 修复有效性怎么判

「`make` 成功」证明不了任何事——修复前构建同样成功，因为头文件确实存在。判据是
**再改一次头文件**：

```bash
# 应用补丁并完整构建后，改 config.h 的取值，不 clean
sed -i 's/#define BASE 10/#define BASE 100/' config.h
make && ./app                          # 修复生效则输出 102；输出 12 说明漏重建仍在
```

这与 DRAFT 的两层判据是同一条思路：**构建通过只是第一层，功能行为正确才算数**。

## 无效候选与恢复

验证不通过的候选不予采纳，且这一判断必须留痕（`output.rejected[].reason`）：

```bash
cp Makefile Makefile.before
# 换一种宏风格改写依赖，但引入本配置下未使用的依赖
make clean && make && ./app            # 若行为改变或复检仍报缺失，则该候选被拒
cp Makefile.before Makefile            # 恢复原状，构建与验证再次通过
```

「全部候选都被拒绝」**不是**任务失败：任务终态仍是 `SUCCEEDED`，`output.fixed`
为空数组、`output.rejected` 记录每条理由。边界见 `contracts/error-codes.md` 第 6 节。

## Docker 参考环境

```bash
docker build -f fixtures/mdfixer/docker/Dockerfile.reference -t mdfixer-fixture fixtures/mdfixer
docker run   --rm mdfixer-fixture      # 预期输出 12
```

补丁产出后的复构建与复检都在这个镜像里进行——环境不一致，前后结论不可比。

## 运行环境

与 `fixtures/draft` 相同：构建与验证**必须在 Linux 环境下进行**（Docker 容器即可）。
宿主开发机为 Windows + MSYS，没有 `make`、没有 `gcc`。

## 后续（E3）

本目录是 E3「固定 MD 报告及其对应 Makefile」的初版。E3 还需在此之上补齐
C0 / C1 / C2 连续提交（一次新增 include、一次只改编译命令）与真实运行记录，
以及四种声明风格（`TARGET` / `MACRO` / `HYBRID` / `IMPLICIT`）的参考修复对照。

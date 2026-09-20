# fixtures/draft

DRAFT（Dockerfile 自动生成）的**固定输入样例**：一个最小 GNU Make C 项目。

它同时充当「可构建环境」的最小验证对象——DRAFT 的任务就是把这样一个仓库变成一个能成功构建的 Dockerfile。

## 项目信息

| 项 | 值 |
| --- | --- |
| 语言 / 构建系统 | C / GNU Make |
| 源码文件 | `main.c`（1 个） |
| 外部依赖 | 无（仅 libc） |

## 构建

```bash
make
```

预期：退出码 `0`，当前目录生成可执行文件 `hello`。

## 功能验证

```bash
./hello
```

预期：标准输出恰好一行 `hello draft`，退出码 `0`。

## 两层成功判据

DRAFT 的产出必须同时满足两层，缺一不可——自动构建需要明确的成功判据：

| 层级 | 判据 | 检查方式 |
| --- | --- | --- |
| 第一层：编译通过 | 构建命令退出码为 0，且可执行文件**确实生成** | 构建后确认 `hello` 存在 |
| 第二层：功能验证 | 运行 `verify_command`，退出码 0 且输出符合 `verify_expect` | 比对 stdout |

> 只满足第一层不算成功。编译通过但运行结果不对，仍是失败候选。

## 清理

```bash
make clean
```

## Docker 参考实现

`docker/` 下有两个对照样例，用于演示迭代修复的两端：

| 文件 | 作用 | 预期结果 |
| --- | --- | --- |
| `Dockerfile.ok` | 参考成功样例 | 构建成功，容器输出 `hello draft` |
| `Dockerfile.broken` | 故障样例 | 构建失败，日志含 `make: not found` |

`Dockerfile.broken` 的设计意图：基础镜像 `python:3.13-slim` 里**没有 C 工具链**，构建在第 `RUN make` 步非零退出。这正是需要从日志中定位并修复的那类错误——错误信息可定位、可归因、有明确修法（换基础镜像或补装 `build-essential`）。

```bash
# 成功样例
docker build -f fixtures/draft/docker/Dockerfile.ok     -t draft-fixture-ok     fixtures/draft
docker run   --rm draft-fixture-ok        # 预期输出 hello draft

# 故障样例（预期失败）
docker build -f fixtures/draft/docker/Dockerfile.broken -t draft-fixture-broken fixtures/draft
```

## 运行环境

构建与验证**必须在 Linux 环境下进行**（Docker 容器即可）。

宿主开发机为 Windows + MSYS，**没有 `make`、没有 `gcc`**，因此所有构建动作都通过 Docker 执行，不在宿主机直接运行。

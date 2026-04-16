# 🌐 auto33MIMO — Ubuntu 服务器网络质量自动化测试脚本

一键检测服务器网络质量，覆盖 5 大维度，自动对比行业标准并生成报告。

## ✨ 测试维度

| 测试项 | 工具 | 检测内容 | 行业标准 |
|--------|------|----------|----------|
| 基础连通性 | `ping` | 丢包率、延迟 | 丢包 < 1%，延迟 < 50ms |
| 路径健康 | `mtr` | 路由追踪、节点丢包 | 最终节点丢包 = 0%，无环回 |
| TCP 响应 | `tcping` | TCP 连接时间 | 连接时间 < 100ms |
| 带宽质量 | `iperf3` | 抖动、重传率 | 抖动 < 5ms，重传 < 5% |
| 业务可用性 | `curl` | HTTP 状态码、TTFB | 2xx/3xx，TTFB < 500ms |

## 📋 环境要求

- **系统**：Ubuntu / Debian（其他 Linux 发行版需手动安装依赖）
- **Python**：3.8+
- **依赖工具**：自动检测并安装（`ping`, `mtr`, `tcping`, `iperf3`, `curl`）
- **权限**：部分测试需要 `sudo`（安装依赖）

## 🚀 快速开始

### 克隆并运行

```bash
git clone https://github.com/LinKZ1003/auto33MIMO.git
cd auto33MIMO
python3 auto33MIMO.py --all
```

### 先装依赖再跑

```bash
python3 auto33MIMO.py --install   # 安装所有依赖
python3 auto33MIMO.py --all       # 运行全部测试
```

## 💻 使用方式

### 交互模式（无参数）

```bash
python3 auto33MIMO.py
```

按提示逐项选择测试内容和参数。

### CLI 模式（指定参数）

```bash
# 全部测试
python3 auto33MIMO.py --all

# 单项测试
python3 auto33MIMO.py --ping
python3 auto33MIMO.py --ping --ping-target 8.8.8.8 --ping-count 50
python3 auto33MIMO.py --mtr --mtr-target baidu.com
python3 auto33MIMO.py --tcping --tcping-target example.com --tcping-port 80
python3 auto33MIMO.py --curl --curl-url https://www.qq.com

# 组合测试
python3 auto33MIMO.py --ping --mtr --tcping --curl

# 带 iperf3 带宽测试（需要先启动 iperf3 服务端）
python3 auto33MIMO.py --iperf3 --iperf3-server 192.168.1.100
```

### 高级选项

```bash
# 并行执行（非 iperf3 测试并行跑，节省时间）
python3 auto33MIMO.py --all --parallel

# JSON 格式报告（方便对接监控系统）
python3 auto33MIMO.py --all --json

# 指定报告输出路径
python3 auto33MIMO.py --all --report /tmp/net_report.txt
```

## 📋 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--all` | 运行所有测试 | - |
| `--parallel` | 并行执行 | false |
| `--json` | 输出 JSON 报告 | false |
| `--report PATH` | 报告输出路径 | `~/Desktop/网络测试报告_时间.txt` |
| `--install` | 仅安装依赖 | - |
| `--ping` | 运行 ping 测试 | - |
| `--ping-target` | ping 目标地址 | `bilibili.com` |
| `--ping-count` | ping 包数 | `100` |
| `--ping-size` | ping 包大小(bytes) | `1400` |
| `--mtr` | 运行 MTR 测试 | - |
| `--mtr-target` | MTR 目标地址 | `bilibili.com` |
| `--tcping` | 运行 TCPing 测试 | - |
| `--tcping-target` | TCPing 目标 | `bilibili.com` |
| `--tcping-port` | TCPing 端口 | `443` |
| `--iperf3` | 运行 iperf3 测试 | - |
| `--iperf3-server` | iperf3 服务端地址 | 必填 |
| `--iperf3-port` | iperf3 端口 | `5201` |
| `--iperf3-duration` | iperf3 测试时长(s) | `10` |
| `--curl` | 运行 curl 测试 | - |
| `--curl-url` | curl 测试 URL | `https://www.baidu.com` |

## 📊 报告示例

```
======================================================================
 测试报告
======================================================================

[PING] bilibili.com
  状态：✅ PASS
  标准：丢包率 < 1.0%, 平均延迟 < 50.0ms
  丢包率：0%
  平均延迟：12.35ms

[MTR] bilibili.com
  状态：✅ PASS
  标准：最终节点丢包率 = 0.0%, 无路由环回

[CURL] https://www.baidu.com
  状态：✅ PASS
  标准：HTTP Status 2xx/3xx, TTFB < 500ms
  HTTP 状态码：200
  TTFB: 58ms
  DNS 解析：2.15ms
  TCP 连接：5.30ms

======================================================================
 总结
======================================================================
总测试数：5
✅ 通过：5
❌ 失败：0
通过率：100.0%

总体评估：✅ 所有测试通过
```

## 🤝 贡献

欢迎 Issue 和 PR！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/xxx`)
3. 提交更改 (`git commit -m 'Add xxx'`)
4. 推送分支 (`git push origin feature/xxx`)
5. 创建 Pull Request

## 📄 许可证

[MIT](LICENSE)

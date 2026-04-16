#!/usr/bin/env python3
"""
Ubuntu 服务器网络质量自动化测试脚本（优化版）
测试维度：
- 基础连通性：ping 测试（丢包率、延迟）
- 路径健康：mtr 测试（路由追踪、节点丢包）
- TCP 响应：tcping 测试（连接时间）
- 带宽质量：iperf3 测试（抖动、重传率）
- 业务可用性：curl 测试（HTTP 状态、TTFB）

行业合格标准（公网参考）：
- ping: 丢包率 < 1%，平均延迟 < 50ms（同城/同运营商）
- mtr: 最终节点丢包率 = 0%，无路由环回
- tcping: 连接时间 < 100ms
- iperf3: 抖动 < 5ms，重传率 < 5%
- curl: HTTP Status 2xx/3xx，TTFB < 500ms
"""

import os
import pwd
import sys
import time
import subprocess
import json
import argparse
import shutil
import signal
import re
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================
# 日志配置（替代散落的 print）
# ============================
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("nettest")

# ============================
# 常量集中管理
# ============================
STANDARDS = {
    "ping_packet_loss": 1.0,      # %
    "ping_avg_latency": 50.0,     # ms
    "mtr_final_loss": 0.0,        # %
    "tcping_avg_time": 100.0,     # ms
    "iperf3_jitter": 5.0,         # ms
    "iperf3_retransmit_rate": 5.0,  # %
    "curl_ttfb": 500.0,           # ms
}

DEFAULT_CONFIG = {
    "ping_target": "bilibili.com",
    "ping_count": 100,
    "ping_size": 1400,
    "mtr_target": "bilibili.com",
    "mtr_max_hops": 30,
    "tcping_target": "bilibili.com",
    "tcping_port": 443,
    "tcping_count": 10,
    "iperf3_server": None,
    "iperf3_port": 5201,
    "iperf3_duration": 10,
    "curl_url": "https://www.baidu.com",
    "curl_timeout": 30,
}

# ============================
# 数据结构（替代散落的 dict）
# ============================
@dataclass
class TestResult:
    test_type: str
    target: str
    success: bool = False
    standard: str = ""
    issues: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""
    error: Optional[str] = None

    def add_issue(self, msg: str):
        self.success = False
        self.issues.append(msg)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw_output", None)  # 报告中不需要原始输出
        return d


# ============================
# 工具函数
# ============================
def get_real_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return Path(pwd.getpwnam(sudo_user).pw_dir)
    return Path.home()


def run_command(cmd: str, check=False, capture_output=False, timeout=300):
    """运行 Shell 命令，统一超时处理"""
    try:
        if capture_output:
            result = subprocess.run(
                cmd, shell=True, check=check,
                capture_output=True, text=True, timeout=timeout,
            )
            return result.stdout, result.stderr, result.returncode
        else:
            result = subprocess.run(cmd, shell=True, check=check, timeout=timeout)
            return "", "", result.returncode
    except subprocess.TimeoutExpired:
        return "", "命令执行超时", -1
    except subprocess.CalledProcessError as e:
        return "", str(e), e.returncode


def ensure_tool(name: str, apt_package: str = None) -> bool:
    """检查工具是否存在，不存在则尝试安装"""
    if shutil.which(name):
        return True
    pkg = apt_package or name
    logger.info(f"⚠️  {name} 未安装，尝试安装 {pkg}...")
    run_command(f"sudo apt install -y {pkg}")
    return shutil.which(name) is not None


def print_banner(text: str):
    logger.info(f"\n{'='*70}")
    logger.info(f" {text}")
    logger.info(f"{'='*70}")


def yes_no_prompt(question: str, default="y") -> bool:
    options = "Y/n" if default.lower() in ("y", "yes") else "y/N"
    while True:
        answer = input(f"{question} [{options}]: ").strip().lower()
        if not answer:
            answer = default.lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        logger.info("请输入 y 或 n")


def input_with_default(prompt: str, default, value_type=str):
    while True:
        try:
            answer = input(f"{prompt} [{default}]: ").strip()
        except EOFError:
            return default
        if not answer:
            return default
        try:
            return value_type(answer)
        except ValueError:
            logger.info(f"请输入有效的 {value_type.__name__} 值")


def clear_input_buffer():
    try:
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (ImportError, OSError, AttributeError):
        pass  # 非 TTY 环境忽略


# ============================
# 网络测试模块
# ============================

def test_ping(target: str, count=100, packet_size=1400) -> TestResult:
    """基础连通性测试 - ping"""
    print_banner(f"PING 测试 - {target}")
    logger.info(f"目标：{target} | 包数：{count} | 包大小：{packet_size} bytes")

    result = TestResult(
        test_type="ping",
        target=target,
        standard=f"丢包率 < {STANDARDS['ping_packet_loss']}%, 平均延迟 < {STANDARDS['ping_avg_latency']}ms",
    )

    cmd = f"ping -c {count} -s {packet_size} -W 1 {target}"
    stdout, stderr, retcode = run_command(cmd, capture_output=True, timeout=600)

    if retcode != 0 and not stdout:
        result.error = stderr
        logger.info(f"❌ Ping 测试失败：{stderr}")
        return result

    result.raw_output = stdout

    # 解析 RTT（兼容中英文）
    rtt_match = re.search(
        r'(?:rtt|延迟).*?[=:]\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms',
        stdout, re.IGNORECASE,
    )
    if rtt_match:
        result.metrics["min_latency"] = float(rtt_match.group(1))
        result.metrics["avg_latency"] = float(rtt_match.group(2))
        result.metrics["max_latency"] = float(rtt_match.group(3))
        result.metrics["stddev"] = float(rtt_match.group(4))

    # 解析丢包率
    loss_match = re.search(r'(\d+(?:\.\d+)?)\s*%\s*(?:packet loss|丢包)', stdout, re.IGNORECASE)
    if loss_match:
        result.metrics["packet_loss"] = float(loss_match.group(1))

    # 判断是否合格
    result.success = True
    pl = result.metrics.get("packet_loss")
    avg = result.metrics.get("avg_latency")
    if pl is not None and pl >= STANDARDS["ping_packet_loss"]:
        result.add_issue(f"丢包率 {pl}% >= {STANDARDS['ping_packet_loss']}%")
    if avg is not None and avg >= STANDARDS["ping_avg_latency"]:
        result.add_issue(f"平均延迟 {avg:.2f}ms >= {STANDARDS['ping_avg_latency']}ms")

    # 输出
    logger.info("\n📊 测试结果:")
    if pl is not None:
        tag = "✅" if pl < STANDARDS["ping_packet_loss"] else "❌"
        logger.info(f"  {tag} 丢包率：{pl}% (标准：< {STANDARDS['ping_packet_loss']}%)")
    if avg is not None:
        tag = "✅" if avg < STANDARDS["ping_avg_latency"] else "⚠️"
        logger.info(f"  {tag} 平均延迟：{avg:.2f}ms (标准：< {STANDARDS['ping_avg_latency']}ms)")
    for key in ("min_latency", "max_latency", "stddev"):
        if key in result.metrics:
            logger.info(f"  {key}：{result.metrics[key]:.2f}")

    if result.issues:
        logger.info(f"\n⚠️  未达标项：{', '.join(result.issues)}")

    return result


def test_mtr(target: str, max_hops=30) -> TestResult:
    """路径健康测试 - mtr"""
    print_banner(f"MTR 路径测试 - {target}")

    result = TestResult(
        test_type="mtr",
        target=target,
        standard=f"最终节点丢包率 = {STANDARDS['mtr_final_loss']}%, 无路由环回",
    )

    if not ensure_tool("mtr"):
        result.error = "mtr not installed"
        logger.info("❌ 无法安装 mtr，跳过测试")
        return result

    cmd = f"mtr --report --report-cycles 5 --max-ttl {max_hops} {target}"
    stdout, stderr, retcode = run_command(cmd, capture_output=True)
    result.raw_output = stdout

    if retcode != 0 and not stdout:
        result.error = stderr
        logger.info(f"❌ MTR 测试失败：{stderr}")
        return result

    # 解析 mtr 输出
    hop_ips = []
    hops = []
    for line in stdout.strip().split("\n"):
        match = re.match(
            r'\s*(\d+)\.\s+(\S+)\s+([\d.]+)%\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)',
            line,
        )
        if match:
            hop = {
                "hop_num": int(match.group(1)),
                "host": match.group(2),
                "loss": float(match.group(3)),
                "sent": int(match.group(4)),
                "last": float(match.group(5)),
                "avg": float(match.group(6)),
                "best": float(match.group(7)),
                "worst": float(match.group(8)),
                "stdev": float(match.group(9)),
            }
            hops.append(hop)
            hop_ips.append(match.group(2))

    result.metrics["hops"] = hops
    result.success = True

    # 最终节点丢包
    if hops:
        final_loss = hops[-1]["loss"]
        result.metrics["final_loss"] = final_loss
        if final_loss > STANDARDS["mtr_final_loss"]:
            result.add_issue(f"最终节点丢包率 {final_loss}%")
            logger.info(f"\n❌ 最终节点丢包率：{final_loss}% (标准：{STANDARDS['mtr_final_loss']}%)")
        else:
            logger.info(f"\n✅ 最终节点丢包率：{final_loss}%")

    # 路由环回检测
    if len(hop_ips) != len(set(hop_ips)):
        result.add_issue("检测到路由环回")
        logger.info("❌ 检测到路由环回")
    else:
        logger.info("✅ 未检测到路由环回")

    # 路由表
    logger.info("\n📍 路由路径:")
    for hop in hops[:15]:
        tag = "✅" if hop["loss"] == 0 else "⚠️" if hop["loss"] < 50 else "❌"
        logger.info(f"  {tag} 跳{hop['hop_num']:2d}: {hop['host']:15s} 丢包:{hop['loss']:5.1f}% 平均:{hop['avg']:6.2f}ms")
    if len(hops) > 15:
        logger.info(f"  ... 还有 {len(hops) - 15} 跳")

    return result


def _tcping_bash_fallback(target: str, port: int, count: int) -> TestResult:
    """tcping 备用方案：使用 bash /dev/tcp"""
    result = TestResult(test_type="tcping (bash)", target=f"{target}:{port}",
                        standard=f"连接时间 < {STANDARDS['tcping_avg_time']}ms")
    times = []
    for i in range(count):
        start = time.time()
        try:
            ret = subprocess.run(
                f"timeout 2 bash -c 'echo > /dev/tcp/{target}/{port}' 2>/dev/null",
                shell=True, timeout=3,
            )
            if ret.returncode == 0:
                elapsed = (time.time() - start) * 1000
                times.append(elapsed)
                logger.info(f"  探测 {i+1}/{count}: {elapsed:.2f}ms ✅")
            else:
                logger.info(f"  探测 {i+1}/{count}: 失败 ❌")
        except subprocess.TimeoutExpired:
            logger.info(f"  探测 {i+1}/{count}: 超时 ❌")

    if times:
        avg = sum(times) / len(times)
        result.metrics = {
            "successful_probes": len(times),
            "total_probes": count,
            "avg_connect_time": avg,
            "min_connect_time": min(times),
            "max_connect_time": max(times),
        }
        result.success = avg < STANDARDS["tcping_avg_time"]

    logger.info(f"\n📊 成功探测：{len(times)}/{count}")
    if result.metrics.get("avg_connect_time"):
        logger.info(f"  平均连接时间：{result.metrics['avg_connect_time']:.2f}ms")
    return result


def test_tcping(target: str, port=443, count=10) -> TestResult:
    """TCP 响应测试 - tcping"""
    print_banner(f"TCPING 测试 - {target}:{port}")

    result = TestResult(
        test_type="tcping",
        target=f"{target}:{port}",
        standard=f"连接时间 < {STANDARDS['tcping_avg_time']}ms",
    )

    if not ensure_tool("tcping"):
        logger.info("使用备用方案：/dev/tcp 测试")
        return _tcping_bash_fallback(target, port, count)

    cmd = f"tcping -c {count} {target} {port}"
    stdout, stderr, retcode = run_command(cmd, capture_output=True)
    result.raw_output = stdout

    # 解析
    probes_match = re.search(r'(\d+)\s+successful', stdout)
    if probes_match:
        result.metrics["successful_probes"] = int(probes_match.group(1))

    time_match = re.search(r'Min/Max/Avg.*?:\s*([\d.]+)/([\d.]+)/([\d.]+)\s*ms', stdout)
    if time_match:
        result.metrics["min_connect_time"] = float(time_match.group(1))
        result.metrics["max_connect_time"] = float(time_match.group(2))
        result.metrics["avg_connect_time"] = float(time_match.group(3))

    avg = result.metrics.get("avg_connect_time")
    if avg is not None:
        result.success = avg < STANDARDS["tcping_avg_time"]
        tag = "✅" if result.success else "❌"
        logger.info(f"\n{tag} 平均连接时间：{avg:.2f}ms (标准：< {STANDARDS['tcping_avg_time']}ms)")

    logger.info(f"\n📊 成功探测：{result.metrics.get('successful_probes', 0)}/{count}")
    return result


def test_iperf3(server: str, duration=10, port=5201) -> TestResult:
    """带宽质量测试 - iperf3"""
    print_banner(f"IPERF3 带宽测试 - {server}")

    result = TestResult(
        test_type="iperf3",
        target=server,
        standard=f"抖动 < {STANDARDS['iperf3_jitter']}ms, 重传率 < {STANDARDS['iperf3_retransmit_rate']}%",
    )

    if not ensure_tool("iperf3"):
        result.error = "iperf3 not installed"
        logger.info("❌ 无法安装 iperf3，跳过测试")
        return result

    cmd = f"iperf3 -c {server} -p {port} -t {duration} -J"
    stdout, stderr, retcode = run_command(cmd, capture_output=True)
    result.raw_output = stdout

    if retcode != 0:
        result.error = stderr
        logger.info(f"❌ Iperf3 测试失败：{stderr}")
        return result

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        result.error = f"JSON 解析失败: {e}"
        logger.info(f"❌ 解析 JSON 失败：{e}")
        return result

    # 带宽
    try:
        bps = data["end"]["sum_received"]["bits_per_second"]
        result.metrics["bandwidth_mbps"] = bps / 1_000_000
    except (KeyError, TypeError):
        pass

    # 抖动和重传（从 streams 提取）
    jitter = None
    try:
        for stream in data["end"].get("streams", []):
            si = stream.get("socket", {})
            if "jitter_ms" in si:
                jitter = si["jitter_ms"]
                break
    except (KeyError, TypeError):
        pass
    if jitter is not None:
        result.metrics["jitter"] = jitter

    # 重传率
    retransmit_rate = None
    try:
        sum_sent = data["end"]["sum_sent"]
        bytes_sent = sum_sent.get("bytes", 0)
        retransmits = sum_sent.get("retransmits", 0)
        result.metrics["total_retransmits"] = retransmits
        if bytes_sent > 0:
            retransmit_rate = (retransmits * 1460) / bytes_sent * 100
            result.metrics["retransmit_rate"] = retransmit_rate
    except (KeyError, TypeError):
        pass

    # 判定
    result.success = True
    if jitter is not None and jitter >= STANDARDS["iperf3_jitter"]:
        result.add_issue(f"抖动 {jitter:.2f}ms >= {STANDARDS['iperf3_jitter']}ms")
    if retransmit_rate is not None and retransmit_rate >= STANDARDS["iperf3_retransmit_rate"]:
        result.add_issue(f"重传率 {retransmit_rate:.2f}% >= {STANDARDS['iperf3_retransmit_rate']}%")

    # 输出
    logger.info("\n📊 测试结果:")
    bw = result.metrics.get("bandwidth_mbps")
    if bw is not None:
        if bw >= 1000:
            logger.info(f"  带宽：{bw/1000:.2f} Gbps")
        else:
            logger.info(f"  带宽：{bw:.2f} Mbps")
    if jitter is not None:
        tag = "✅" if jitter < STANDARDS["iperf3_jitter"] else "❌"
        logger.info(f"  {tag} 抖动：{jitter:.2f}ms")
    if retransmit_rate is not None:
        tag = "✅" if retransmit_rate < STANDARDS["iperf3_retransmit_rate"] else "❌"
        logger.info(f"  {tag} 估算重传率：{retransmit_rate:.2f}%")

    if result.issues:
        logger.info(f"\n⚠️  未达标项：{', '.join(result.issues)}")
    else:
        logger.info("\n✅ 所有指标均达标")

    return result


def test_curl(url: str, timeout=30) -> TestResult:
    """业务可用性测试 - curl"""
    print_banner(f"CURL 业务测试 - {url}")

    result = TestResult(
        test_type="curl",
        target=url,
        standard=f"HTTP Status 2xx/3xx, TTFB < {STANDARDS['curl_ttfb']}ms",
    )

    if not shutil.which("curl"):
        result.error = "curl not installed"
        logger.info("❌ curl 未安装")
        return result

    # 【优化】使用 -w 加写文件代替 shell 字符串拼接，避免引号嵌套问题
    fmt = json.dumps({
        "http_code": "%{http_code}",
        "time_total": "%{time_total}",
        "time_namelookup": "%{time_namelookup}",
        "time_connect": "%{time_connect}",
        "time_appconnect": "%{time_appconnect}",
        "time_starttransfer": "%{time_starttransfer}",
        "size_download": "%{size_download}",
    })
    # curl -w 的 % 需要转义为 %%
    fmt_escaped = fmt.replace("%", "%%")
    cmd = f"curl -s -o /dev/null -w '{fmt_escaped}' --max-time {timeout} '{url}'"
    stdout, stderr, retcode = run_command(cmd, capture_output=True)
    result.raw_output = stdout

    if retcode != 0 and not stdout:
        result.error = stderr
        logger.info(f"❌ Curl 测试失败：{stderr}")
        return result

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        result.error = f"JSON 解析失败: {e}"
        logger.info(f"❌ 解析 JSON 失败：{e}")
        return result

    http_code = int(data.get("http_code", 0))
    ttfb = float(data.get("time_starttransfer", 0)) * 1000
    result.metrics = {
        "http_code": http_code,
        "ttfb": round(ttfb),
        "total_time": round(float(data.get("time_total", 0)) * 1000),
        "dns_time": round(float(data.get("time_namelookup", 0)) * 1000, 2),
        "connect_time": round(float(data.get("time_connect", 0)) * 1000, 2),
        "download_size": int(data.get("size_download", 0)),
    }
    appconnect = float(data.get("time_appconnect", 0))
    if appconnect > 0:
        result.metrics["ssl_time"] = round(appconnect * 1000, 2)

    # 判定
    result.success = True
    if not (200 <= http_code < 400):
        result.add_issue(f"HTTP 状态码 {http_code} 非 2xx/3xx")
    if ttfb >= STANDARDS["curl_ttfb"]:
        result.add_issue(f"TTFB {ttfb:.0f}ms >= {STANDARDS['curl_ttfb']}ms")

    # 输出
    logger.info("\n📊 测试结果:")
    if 200 <= http_code < 300:
        tag = "✅"
    elif 300 <= http_code < 400:
        tag = "⚠️"
    else:
        tag = "❌"
    logger.info(f"  {tag} HTTP 状态码：{http_code}")
    logger.info(f"  {'✅' if ttfb < STANDARDS['curl_ttfb'] else '❌'} TTFB: {ttfb:.0f}ms")
    logger.info(f"  DNS 解析：{result.metrics['dns_time']}ms")
    logger.info(f"  TCP 连接：{result.metrics['connect_time']}ms")
    if "ssl_time" in result.metrics:
        logger.info(f"  SSL 握手：{result.metrics['ssl_time']}ms")
    logger.info(f"  总时间：{result.metrics['total_time']}ms")

    if result.issues:
        logger.info(f"\n⚠️  未达标项：{', '.join(result.issues)}")
    else:
        logger.info("\n✅ 所有指标均达标")

    return result


# ============================
# 综合测试与报告
# ============================

# 【优化】测试函数注册表，避免 if 链
TEST_REGISTRY = {
    "ping":   lambda c: test_ping(c["ping_target"], c["ping_count"], c["ping_size"]),
    "mtr":    lambda c: test_mtr(c["mtr_target"], c["mtr_max_hops"]),
    "tcping": lambda c: test_tcping(c["tcping_target"], c["tcping_port"], c["tcping_count"]),
    "iperf3": lambda c: test_iperf3(c["iperf3_server"], c["iperf3_duration"], c["iperf3_port"]),
    "curl":   lambda c: test_curl(c["curl_url"], c["curl_timeout"]),
}


def run_all_tests(config: dict, parallel=False) -> List[TestResult]:
    """运行所有选中的测试，可选并行"""
    selected = [
        name for name in ("ping", "mtr", "tcping", "iperf3", "curl")
        if config.get(f"run_{name}")
    ]

    # iperf3 需要 server 参数
    if "iperf3" in selected and not config.get("iperf3_server"):
        logger.info("⚠️  未提供 iperf3 服务器地址，跳过")
        selected.remove("iperf3")

    results = []

    if parallel:
        # 【新增】非 iperf3 的测试可以并行跑
        parallel_tests = [t for t in selected if t != "iperf3"]
        sequential_tests = [t for t in selected if t == "iperf3"]

        with ThreadPoolExecutor(max_workers=len(parallel_tests)) as pool:
            futures = {
                pool.submit(TEST_REGISTRY[name], config): name
                for name in parallel_tests
            }
            for future in as_completed(futures):
                results.append(future.result())

        for name in sequential_tests:
            results.append(TEST_REGISTRY[name](config))
    else:
        for name in selected:
            results.append(TEST_REGISTRY[name](config))

    return results


def generate_report(results: List[TestResult], output_file: str = None) -> List[str]:
    """生成测试报告"""
    print_banner("测试报告")

    lines = [
        "=" * 70,
        "网络质量测试报告",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 70,
        "",
    ]

    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed

    for r in results:
        status = "✅ PASS" if r.success else "❌ FAIL"
        lines.append(f"[{r.test_type.upper()}] {r.target}")
        lines.append(f"  状态：{status}")
        lines.append(f"  标准：{r.standard}")

        m = r.metrics
        # 只输出有值的关键指标
        key_map = [
            ("packet_loss", "丢包率", "%"),
            ("avg_latency", "平均延迟", "ms"),
            ("final_loss", "最终节点丢包", "%"),
            ("avg_connect_time", "平均连接时间", "ms"),
            ("jitter", "抖动", "ms"),
            ("ttfb", "TTFB", "ms"),
            ("http_code", "HTTP 状态码", ""),
            ("bandwidth_mbps", "带宽", "Mbps"),
            ("retransmit_rate", "重传率", "%"),
        ]
        for key, label, unit in key_map:
            if key in m and m[key] is not None:
                suffix = f" {unit}" if unit else ""
                lines.append(f"  {label}：{m[key]}{suffix}")

        if r.issues:
            lines.append(f"  问题：{', '.join(r.issues)}")
        lines.append("")

    # 总结
    lines += [
        "=" * 70,
        "总结",
        "=" * 70,
        f"总测试数：{len(results)}",
        f"✅ 通过：{passed}",
        f"❌ 失败：{failed}",
    ]
    if results:
        lines.append(f"通过率：{passed / len(results) * 100:.1f}%")

    overall = "✅ 所有测试通过" if failed == 0 else f"⚠️  {failed} 项测试未通过"
    lines.append(f"\n总体评估：{overall}")
    lines.append("=" * 70)

    for line in lines:
        logger.info(line)

    if output_file:
        try:
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            logger.info(f"\n📄 报告已保存至：{output_file}")
        except OSError as e:
            logger.info(f"保存报告失败：{e}")

    return lines


def install_dependencies():
    """批量安装缺失依赖（只调一次 apt install）"""
    print_banner("安装测试依赖")
    pkg_map = {"ping": "iputils-ping", "mtr": "mtr", "tcping": "tcping", "iperf3": "iperf3", "curl": "curl"}
    missing = [pkg_map[k] for k, v in pkg_map.items() if not shutil.which(k)]
    if not missing:
        logger.info("✅ 所有依赖已安装")
        return
    logger.info(f"需要安装：{', '.join(missing)}")
    if yes_no_prompt("是否安装这些依赖？", "y"):
        run_command("sudo apt update")
        run_command(f"sudo apt install -y {' '.join(missing)}")
        logger.info("✅ 依赖安装完成")
    else:
        logger.info("⚠️  跳过依赖安装，部分测试可能无法运行")


# ============================
# CLI
# ============================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="网络质量测试脚本")
    p.add_argument("--ping", action="store_true", help="运行 ping 测试")
    p.add_argument("--ping-target", default=DEFAULT_CONFIG["ping_target"])
    p.add_argument("--ping-count", type=int, default=DEFAULT_CONFIG["ping_count"])
    p.add_argument("--ping-size", type=int, default=DEFAULT_CONFIG["ping_size"])
    p.add_argument("--mtr", action="store_true", help="运行 MTR 测试")
    p.add_argument("--mtr-target", default=DEFAULT_CONFIG["mtr_target"])
    p.add_argument("--tcping", action="store_true")
    p.add_argument("--tcping-target", default=DEFAULT_CONFIG["tcping_target"])
    p.add_argument("--tcping-port", type=int, default=DEFAULT_CONFIG["tcping_port"])
    p.add_argument("--iperf3", action="store_true")
    p.add_argument("--iperf3-server")
    p.add_argument("--iperf3-port", type=int, default=DEFAULT_CONFIG["iperf3_port"])
    p.add_argument("--iperf3-duration", type=int, default=DEFAULT_CONFIG["iperf3_duration"])
    p.add_argument("--curl", action="store_true")
    p.add_argument("--curl-url", default=DEFAULT_CONFIG["curl_url"])
    p.add_argument("--all", action="store_true", help="运行所有测试")
    p.add_argument("--parallel", action="store_true", help="并行执行（非 iperf3）")
    p.add_argument("--json", action="store_true", help="输出 JSON 格式报告")
    p.add_argument("--install", action="store_true", help="仅安装依赖")
    p.add_argument("--report", help="报告输出文件路径")
    return p


def cli_config_from_args(args) -> dict:
    """从 argparse 结果构建 config dict"""
    return {
        "run_ping": args.ping or args.all,
        "run_mtr": args.mtr or args.all,
        "run_tcping": args.tcping or args.all,
        "run_iperf3": args.iperf3 or args.all,
        "run_curl": args.curl or args.all,
        "ping_target": args.ping_target,
        "ping_count": args.ping_count,
        "ping_size": args.ping_size,
        "mtr_target": args.mtr_target,
        "tcping_target": args.tcping_target,
        "tcping_port": args.tcping_port,
        "iperf3_server": args.iperf3_server,
        "iperf3_port": args.iperf3_port,
        "iperf3_duration": args.iperf3_duration,
        "curl_url": args.curl_url,
        "curl_timeout": DEFAULT_CONFIG["curl_timeout"],
    }


def interactive_config() -> dict:
    """交互式配置"""
    config = dict(DEFAULT_CONFIG)
    config.update({k: False for k in ("run_ping", "run_mtr", "run_tcping", "run_iperf3", "run_curl")})

    logger.info("\n请选择测试项目:")

    config["run_ping"] = yes_no_prompt("1. Ping 基础连通性测试", "y")
    clear_input_buffer()
    if config["run_ping"]:
        config["ping_target"] = input_with_default("Ping 目标", config["ping_target"])
        config["ping_count"] = input_with_default("Ping 包数", str(config["ping_count"]), int)
        clear_input_buffer()

    config["run_mtr"] = yes_no_prompt("2. MTR 路径健康测试", "y")
    clear_input_buffer()
    if config["run_mtr"]:
        config["mtr_target"] = input_with_default("MTR 目标", config["mtr_target"])
        clear_input_buffer()

    config["run_tcping"] = yes_no_prompt("3. TCPing TCP 响应测试", "y")
    clear_input_buffer()
    if config["run_tcping"]:
        config["tcping_target"] = input_with_default("TCPing 目标", config["tcping_target"])
        config["tcping_port"] = input_with_default("TCPing 端口", str(config["tcping_port"]), int)
        clear_input_buffer()

    config["run_iperf3"] = yes_no_prompt("4. Iperf3 带宽质量测试", "n")
    clear_input_buffer()
    if config["run_iperf3"]:
        server = input("Iperf3 服务器地址 (必需): ").strip()
        clear_input_buffer()
        if not server:
            logger.info("⚠️  未提供服务器地址，跳过 Iperf3 测试")
            config["run_iperf3"] = False
        else:
            config["iperf3_server"] = server

    config["run_curl"] = yes_no_prompt("5. Curl 业务可用性测试", "y")
    clear_input_buffer()
    if config["run_curl"]:
        config["curl_url"] = input_with_default("测试 URL", config["curl_url"])
        clear_input_buffer()

    if not any(config[k] for k in ("run_ping", "run_mtr", "run_tcping", "run_iperf3", "run_curl")):
        logger.info("\n⚠️  未选择任何测试，退出")
        sys.exit(0)

    return config


def main():
    print_banner("Ubuntu 服务器网络质量自动化测试")

    parser = build_parser()
    args = parser.parse_args()

    if args.install:
        install_dependencies()
        return

    # 有命令行参数则用 CLI 模式，否则交互模式
    cli_flags = any([args.ping, args.mtr, args.tcping, args.iperf3, args.curl, args.all])
    config = cli_config_from_args(args) if cli_flags else interactive_config()

    install_dependencies()

    print_banner("开始测试")
    results = run_all_tests(config, parallel=args.parallel)

    # 报告输出
    output_file = args.report
    if not output_file:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        desktop = get_real_home() / "Desktop"
        output_file = str(desktop / f"网络测试报告_{ts}.txt")

    if args.json:
        # 【新增】JSON 输出支持
        json_out = {
            "generated_at": datetime.now().isoformat(),
            "results": [r.to_dict() for r in results],
            "summary": {
                "total": len(results),
                "passed": sum(1 for r in results if r.success),
                "failed": sum(1 for r in results if not r.success),
            },
        }
        json_path = output_file.rsplit(".", 1)[0] + ".json"
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_out, f, ensure_ascii=False, indent=2)
        logger.info(f"\n📄 JSON 报告已保存至：{json_path}")

    generate_report(results, output_file)


if __name__ == "__main__":
    main()

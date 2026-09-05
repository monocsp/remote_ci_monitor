"""호스트 파서 — 실제 macOS 픽스처 5종 + Linux 합성 픽스처 · 빈 입력 · 깨진 줄 · stale 경계.

PLAN.md 「호스트 자원」·「fail-open 금지」: 값이 없으면 None(0 아님), 이상한 입력도 예외 대신 None.
mutcheck ④(stale 경계)·⑤(top 은 마지막 표본)의 표적이 여기 있다. 기대값은 픽스처에서 손으로 뽑았다.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remote_ci_monitor.core.hostparse import (
    PAGE_SIZE_DEFAULT,
    mac_memory,
    parse_ioreg_gpu,
    parse_nvidia_smi,
    parse_proc_loadavg,
    parse_proc_meminfo,
    parse_proc_stat_cpu,
    parse_ps,
    parse_sysctl_int,
    parse_top_cpu,
    parse_top_load,
    parse_vm_stat,
    stale,
)

FIXTURES = Path(__file__).parent / "fixtures" / "host"
NOW = datetime(2026, 9, 5, 1, 1, 46, tzinfo=UTC)
GARBAGE = "\x00\xff not a report\n=== ??? ===\n12ab\n: : :\n"
MEM_NONE = {"total_bytes": None, "used_bytes": None, "compressed_bytes": None}
NO_GPU = (None, "no IOAccelerator PerformanceStatistics")


def mac(name: str) -> str:
    return (FIXTURES / "macos" / name).read_text()


def linux(name: str) -> str:
    return (FIXTURES / "linux" / name).read_text()


# ── vm_stat · sysctl · mac_memory ─────────────────────────────────────────────


def test_parse_vm_stat_real_fixture_pages_and_page_size():
    vm = parse_vm_stat(mac("vm_stat.txt"))
    assert vm is not None
    assert vm["page_size"] == 16384  # Apple Silicon — 기본값 4096 이 아니다
    assert vm["free"] == 46220
    assert vm["active"] == 409210
    assert vm["inactive"] == 402764
    assert vm["speculative"] == 5170
    assert vm["wired"] == 192322  # "Pages wired down"
    assert vm["compressor"] == 469673  # "Pages occupied by compressor" (stored 1886474 가 아니다)


def test_parse_vm_stat_without_header_falls_back_to_default_page_size():
    text = "Pages active:  10.\nPages wired down:  5.\nPages occupied by compressor:  2.\n"
    vm = parse_vm_stat(text)
    assert vm is not None
    assert vm["page_size"] == PAGE_SIZE_DEFAULT == 4096
    assert (vm["active"], vm["wired"], vm["compressor"]) == (10, 5, 2)


def test_parse_vm_stat_empty_and_garbage_are_none():
    assert parse_vm_stat("") is None
    assert parse_vm_stat("   \n\n") is None
    assert parse_vm_stat(GARBAGE) is None


def test_parse_sysctl_int():
    assert parse_sysctl_int(mac("sysctl_hw_memsize.txt")) == 25769803776
    assert parse_sysctl_int("  42 \n") == 42
    assert parse_sysctl_int("") is None
    assert parse_sysctl_int("abc\n") is None
    assert parse_sysctl_int(GARBAGE) is None


def test_mac_memory_real_fixture_bytes():
    vm = parse_vm_stat(mac("vm_stat.txt"))
    total = parse_sysctl_int(mac("sysctl_hw_memsize.txt"))
    # (409210 + 192322 + 469673) × 16384 · 469673 × 16384
    assert mac_memory(vm, total) == {
        "total_bytes": 25769803776,
        "used_bytes": 17550622720,
        "compressed_bytes": 7695122432,
    }


def test_mac_memory_with_none_parts():
    assert mac_memory(None, 25769803776) == {
        "total_bytes": 25769803776,
        "used_bytes": None,
        "compressed_bytes": None,
    }
    assert mac_memory(None, None) == MEM_NONE
    # vm 은 있는데 total 만 없으면 used/compressed 는 그대로 계산한다
    vm = parse_vm_stat(mac("vm_stat.txt"))
    mem = mac_memory(vm, None)
    assert mem["total_bytes"] is None
    assert mem["used_bytes"] == 17550622720 and mem["compressed_bytes"] == 7695122432


# ── top ──────────────────────────────────────────────────────────────────────


def test_parse_top_cpu_uses_last_sample_not_first():
    cpu = parse_top_cpu(mac("top_l2.txt"))
    assert cpu == {"user": 39.82, "sys": 14.33, "idle": 45.84, "busy": 54.16}
    # mutcheck ⑤: 첫 표본(부팅 이후 누적)은 46.75/21.8/32.16 — 그걸 쓰면 여기서 빨개진다
    assert cpu["idle"] != 32.16 and cpu["user"] != 46.75 and cpu["busy"] != 67.84


def test_parse_top_cpu_single_sample_busy_is_100_minus_idle():
    text = "Processes: 1 total\nCPU usage: 10.0% user, 5.5% sys, 84.5% idle \n"
    assert parse_top_cpu(text) == {"user": 10.0, "sys": 5.5, "idle": 84.5, "busy": 15.5}


def test_parse_top_cpu_missing_line_empty_and_garbage_are_none():
    assert parse_top_cpu("Processes: 1 total\nLoad Avg: 1.0, 2.0, 3.0\n") is None
    assert parse_top_cpu("") is None
    assert parse_top_cpu(GARBAGE) is None


def test_parse_top_load_real_fixture():
    assert parse_top_load(mac("top_l2.txt")) == (5.42, 5.06, 3.45)


def test_parse_top_load_takes_last_line_and_rejects_bad_input():
    text = "Load Avg: 1.00, 2.00, 3.00 \nCPU usage: 1% user\nLoad Avg: 4.50, 5.50, 6.50 \n"
    assert parse_top_load(text) == (4.5, 5.5, 6.5)
    assert parse_top_load("") is None
    assert parse_top_load("Load Avg: a, b, c\n") is None
    assert parse_top_load(GARBAGE) is None


# ── ps ───────────────────────────────────────────────────────────────────────


def test_parse_ps_real_macos_fixture_top5():
    # 실제 `ps -Aro` 는 이미 cpu 내림차순 · 경로는 basename 만 · rss KiB → 가장 가까운 MB 정수
    assert parse_ps(mac("ps_Aro.txt")) == [
        {"comm": "dartaotruntime", "cpu": 74.6, "rss_mb": 2881},
        {"comm": "flutter_tester", "cpu": 71.8, "rss_mb": 266},
        {"comm": "flutter_tester", "cpu": 60.7, "rss_mb": 263},
        {"comm": "dartaotruntime", "cpu": 52.1, "rss_mb": 1174},
        {"comm": "dartvm", "cpu": 33.1, "rss_mb": 213},  # 217680 / 1024 = 212.58
    ]


def test_parse_ps_linux_fixture_top5():
    assert parse_ps(linux("ps_eo.txt")) == [
        {"comm": "dart", "cpu": 88.5, "rss_mb": 2670},
        {"comm": "flutter_tester", "cpu": 64.2, "rss_mb": 277},
        {"comm": "java", "cpu": 41.7, "rss_mb": 1094},
        {"comm": "node", "cpu": 22.3, "rss_mb": 337},
        {"comm": "gradle", "cpu": 12.9, "rss_mb": 196},
    ]


def test_parse_ps_comm_is_rest_of_line_with_basename():
    rows = parse_ps(mac("ps_Aro.txt"), limit=30)
    assert len(rows) == 30
    comms = [r["comm"] for r in rows]
    assert not any("/" in c for c in comms)
    assert "Orca Helper (Renderer)" in comms  # 경로에 공백이 있어도 마지막 조각 전체
    assert "claude bg-spare" in comms  # 경로 없는 comm 에 공백
    assert "io.tailscale.ipn.macsys.network-extension" in comms
    linux_rows = parse_ps(linux("ps_eo.txt"), limit=99)
    assert len(linux_rows) == 15  # limit 이 줄 수보다 커도 있는 만큼만
    assert {"comm": "Web Content", "cpu": 0.3, "rss_mb": 8} in linux_rows


def test_parse_ps_sorts_cpu_desc_and_limits():
    text = " 1.0 1024 a\n 180.4 2048 /x/dart\n 5.0 4096 c\n 9.5 3072 d\n"
    rows = parse_ps(text, limit=10)
    assert [r["comm"] for r in rows] == ["dart", "d", "c", "a"]
    assert rows[0]["cpu"] == 180.4  # 멀티코어면 100 을 넘는다
    assert parse_ps(text, limit=2) == [
        {"comm": "dart", "cpu": 180.4, "rss_mb": 2},
        {"comm": "d", "cpu": 9.5, "rss_mb": 3},
    ]
    assert parse_ps(text, limit=1) == [{"comm": "dart", "cpu": 180.4, "rss_mb": 2}]


def test_parse_ps_rss_mb_rounds_kib_to_nearest():
    # 정확히 .5 는 피한다(banker's rounding 여부는 안 본다)
    assert parse_ps(" 1.0 1535 x\n") == [{"comm": "x", "cpu": 1.0, "rss_mb": 1}]
    assert parse_ps(" 1.0 1537 x\n") == [{"comm": "x", "cpu": 1.0, "rss_mb": 2}]
    assert parse_ps(" 1.0 2048 x\n") == [{"comm": "x", "cpu": 1.0, "rss_mb": 2}]
    assert parse_ps(" 1.0 0 x\n") == [{"comm": "x", "cpu": 1.0, "rss_mb": 0}]


def test_parse_ps_skips_unparsable_lines():
    text = (
        "\n"
        "%CPU   RSS COMM\n"  # 헤더가 섞여 들어와도
        "garbage\n"
        "abc 123 foo\n"
        " 1.0 notanumber bar\n"
        "   \n"
        " 3.0 3072 /usr/bin/baz\n"
    )
    assert parse_ps(text) == [{"comm": "baz", "cpu": 3.0, "rss_mb": 3}]
    assert parse_ps("") == []
    assert parse_ps(GARBAGE) == []


# ── ioreg (Apple Silicon GPU) ────────────────────────────────────────────────

NO_PERFSTATS = (
    "+-o AGXAcceleratorG16G  <class AGXAcceleratorG16G, id 0x100000422, registered, matched>\n"
    "    {\n"
    '      "IOClass" = "AGXAcceleratorG16G"\n'
    '      "IOProviderClass" = "IOService"\n'
    "    }\n"
)


def test_parse_ioreg_gpu_real_fixture():
    gpu, note = parse_ioreg_gpu(mac("ioreg_IOAccelerator.txt"))
    assert note is None
    assert gpu == {
        "util_pct": 1,
        "mem_used_bytes": 27443200,  # 같은 dict 에 "In use system memory (driver)"=0 이 먼저 있다
        "mem_total_bytes": None,  # 통합 메모리
        "source": "ioreg",
    }
    assert isinstance(gpu["util_pct"], int)


def test_parse_ioreg_gpu_without_performance_statistics():
    assert parse_ioreg_gpu(NO_PERFSTATS) == NO_GPU
    assert parse_ioreg_gpu("") == NO_GPU
    assert parse_ioreg_gpu(GARBAGE) == NO_GPU


def test_parse_ioreg_gpu_partial_keys_leave_missing_none():
    only_util = (
        '      "PerformanceStatistics" = {"Tiler Utilization %"=3,"Device Utilization %"=37}\n'
    )
    assert parse_ioreg_gpu(only_util) == (
        {"util_pct": 37, "mem_used_bytes": None, "mem_total_bytes": None, "source": "ioreg"},
        None,
    )
    only_mem = (
        '      "PerformanceStatistics" = {"In use system memory (driver)"=0,'
        '"In use system memory"=1048576}\n'
    )
    assert parse_ioreg_gpu(only_mem) == (
        {"util_pct": None, "mem_used_bytes": 1048576, "mem_total_bytes": None, "source": "ioreg"},
        None,
    )


def test_parse_ioreg_gpu_multiple_accelerators_first_wins():
    text = (
        "+-o AGXAcceleratorA  <class AGXAcceleratorA, id 0x1>\n    {\n"
        '      "PerformanceStatistics" = {"Device Utilization %"=5,"In use system memory"=100}\n'
        "    }\n"
        "+-o AGXAcceleratorB  <class AGXAcceleratorB, id 0x2>\n    {\n"
        '      "PerformanceStatistics" = {"Device Utilization %"=90,"In use system memory"=900}\n'
        "    }\n"
    )
    gpu, note = parse_ioreg_gpu(text)
    assert note is None
    assert gpu["util_pct"] == 5 and gpu["mem_used_bytes"] == 100


# ── /proc (Linux) ────────────────────────────────────────────────────────────


def test_parse_proc_loadavg():
    assert parse_proc_loadavg(linux("proc_loadavg.txt")) == (0.58, 0.71, 0.83)
    assert parse_proc_loadavg("") is None
    assert parse_proc_loadavg("x y z 1/2 3\n") is None
    assert parse_proc_loadavg("1.5\n") is None
    assert parse_proc_loadavg(GARBAGE) is None


def test_parse_proc_meminfo_full():
    # MemTotal 16281456 kB · MemAvailable 9834720 kB
    assert parse_proc_meminfo(linux("proc_meminfo.txt")) == {
        "total_bytes": 16672210944,
        "used_bytes": 6601457664,
        "compressed_bytes": None,
    }


def test_parse_proc_meminfo_without_memavailable_leaves_used_none():
    assert parse_proc_meminfo(linux("proc_meminfo_no_available.txt")) == {
        "total_bytes": 16672210944,
        "used_bytes": None,
        "compressed_bytes": None,
    }


def test_parse_proc_meminfo_empty_and_garbage_are_all_none():
    assert parse_proc_meminfo("") == MEM_NONE
    assert parse_proc_meminfo(GARBAGE) == MEM_NONE
    assert parse_proc_meminfo("MemTotal:  abc kB\n") == MEM_NONE


def test_parse_proc_stat_cpu_fixture_delta():
    # 차분 user 400 nice 10 system 120 idle 400 iowait 20 irq 10 softirq 40 steal 0 → total 1000
    cpu = parse_proc_stat_cpu(linux("proc_stat_1.txt"), linux("proc_stat_2.txt"))
    assert cpu is not None
    assert cpu["user"] == pytest.approx(41.0)
    assert cpu["sys"] == pytest.approx(17.0)
    assert cpu["idle"] == pytest.approx(42.0)
    assert cpu["busy"] == pytest.approx(58.0)


def test_parse_proc_stat_cpu_inline_math():
    first = "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 100 0 50 800 50 0 0 0 0 0\n"
    second = "cpu  200 0 100 1400 100 0 0 0 0 0\ncpu0 200 0 100 1400 100 0 0 0 0 0\n"
    assert parse_proc_stat_cpu(first, second) == {
        "user": 12.5,
        "sys": 6.25,
        "idle": 81.25,
        "busy": 18.75,
    }


def test_parse_proc_stat_cpu_zero_delta_is_none():
    one = linux("proc_stat_1.txt")
    assert parse_proc_stat_cpu(one, one) is None


def test_parse_proc_stat_cpu_bad_input_is_none():
    assert parse_proc_stat_cpu("", "") is None
    assert parse_proc_stat_cpu(linux("proc_stat_1.txt"), "") is None
    assert parse_proc_stat_cpu("", linux("proc_stat_2.txt")) is None
    assert parse_proc_stat_cpu(GARBAGE, GARBAGE) is None
    # 합계 "cpu " 줄이 없고 cpu0 만 있으면 모른다
    assert parse_proc_stat_cpu("cpu0 1 2 3 4 5 6 7\n", "cpu0 2 3 4 5 6 7 8\n") is None


# ── nvidia-smi ───────────────────────────────────────────────────────────────


def test_parse_nvidia_smi_ok():
    assert parse_nvidia_smi(linux("nvidia_smi.txt")) == {
        "util_pct": 13,
        "mem_used_bytes": 594 * 2**20,
        "mem_total_bytes": 8192 * 2**20,
        "source": "nvidia-smi",
    }


def test_parse_nvidia_smi_multi_gpu_uses_first_line():
    gpu = parse_nvidia_smi("13, 594, 8192\n45, 2048, 16384\n")
    assert gpu is not None
    assert gpu["util_pct"] == 13 and gpu["mem_total_bytes"] == 8192 * 2**20


def test_parse_nvidia_smi_error_and_empty_are_none():
    assert parse_nvidia_smi(linux("nvidia_smi_error.txt")) is None
    assert parse_nvidia_smi("") is None
    assert parse_nvidia_smi("   \n") is None
    assert parse_nvidia_smi(GARBAGE) is None


# ── stale ────────────────────────────────────────────────────────────────────


def test_stale_exactly_three_intervals_is_not_stale():
    # mutcheck ④ 표적: `> 3 × interval` 이 `> 0 × interval` 로 바뀌면 여기가 빨개진다
    assert stale(NOW - timedelta(seconds=15), NOW, 5) is False
    assert stale(NOW - timedelta(seconds=6), NOW, 2) is False
    assert stale(NOW - timedelta(seconds=14.9), NOW, 5) is False


def test_stale_one_second_past_three_intervals_is_stale():
    assert stale(NOW - timedelta(seconds=16), NOW, 5) is True
    assert stale(NOW - timedelta(seconds=7), NOW, 2) is True
    assert stale(NOW - timedelta(hours=1), NOW, 5) is True


def test_stale_negative_or_zero_age_is_not_stale():
    assert stale(NOW + timedelta(seconds=30), NOW, 5) is False  # 시계가 뒤로 갔다
    assert stale(NOW, NOW, 5) is False

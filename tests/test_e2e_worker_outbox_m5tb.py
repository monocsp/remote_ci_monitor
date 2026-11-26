"""e2e: 원격 잡 로그가 분단을 넘어 바이트 그대로 도착한다(M5t-b §4.5 · RL6 b · RL16 · RL17 a).

진짜 서버 프로세스 + 진짜 `rcm worker` + 표준 라이브러리 TCP 중계(`tests/tcp_relay.py`)다. 하네스는
`tests/test_e2e_worker_grace_m5tb.py` 의 `Grace` 를 쓰고 프리셋 하나를 더한다. 출력이 정해진 잡이라
서버의 `log.txt` 를 그 출력과 바이트로 비교한다(가릴 값이 없을 때 sha256 일치).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from test_e2e_worker import rcm
from test_e2e_worker_grace_m5tb import Grace

LINES = 4000
#: 한 줄씩 천천히 내는 잡 — 분단 동안에도 출력이 쌓인다. 마지막 줄에는 줄 끝이 없다(RL17 a)
COUNT = (
    "i=0; while [ $i -lt " + str(LINES) + " ]; do echo line-$i; i=$((i+1));"
    " if [ $((i % 400)) -eq 0 ]; then sleep 1; fi; done; printf tail-without-newline"
)
EXTRA = f"""
[[presets]]
name = "count"
pool = "linux"
argv = ["sh", "-c", '{COUNT}']
timeout_seconds = 300
"""


def expected_count() -> bytes:
    body = b"".join(f"line-{i}\n".encode() for i in range(LINES))
    return body + b"tail-without-newline\n"  # 러너가 줄 끝을 붙인다


@pytest.fixture
def grace(tmp_path: Path):
    g = Grace(tmp_path)
    with g.server.config.open("a") as f:
        f.write(EXTRA)
    yield g
    g.teardown()


def server_log(grace: Grace, jid: int) -> bytes:
    return (grace.server.data_dir / "jobs" / str(jid) / "log.txt").read_bytes()


@pytest.mark.slow
def test_rl6_b_rl16_a_partition_mid_job_and_the_server_log_is_byte_equal(grace):
    grace.start("build-02", relay=True)
    jid = grace.submit("count")
    grace.wait_state(jid, ("running",), 30)
    time.sleep(1.5)
    assert grace.relay is not None
    grace.relay.pause()  # 응답을 붙잡는다 — 보낸 조각의 답을 잃는다
    time.sleep(4)
    grace.relay.resume()
    assert grace.wait_exit(jid, 120) == 0
    got = server_log(grace, jid)
    assert hashlib.sha256(got).hexdigest() == hashlib.sha256(expected_count()).hexdigest()
    doc = grace.job(jid)
    assert doc["log_complete"] is True and doc["late_result"] is None


@pytest.mark.slow
def test_rl17_a_the_last_line_without_a_newline_survives_the_job_end(grace):
    grace.start("build-02")
    jid = grace.submit("count")
    assert grace.wait_exit(jid, 120) == 0
    assert server_log(grace, jid) == expected_count()
    out = rcm("logs", str(jid), env=grace.env)
    assert out.returncode == 0 and "tail-without-newline" in out.stdout
    assert grace.job(jid)["log_complete"] is True

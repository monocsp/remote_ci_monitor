"""캡처용 rcm 진입점 — 화면에 이 머신의 진짜 이름이 남지 않게 호스트명을 고정한다."""

import socket
import sys

socket.gethostname = lambda: "macmini"  # 헤더 · 호스트 카드 · 기본 라벨에 쓰인다

from remote_ci_monitor.cli import main  # noqa: E402

sys.exit(main())

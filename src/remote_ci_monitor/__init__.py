"""remote_ci_monitor — 빌드 머신 한 대를 위한 로컬 잡 서버.

세션이 `rcm run <preset>` 으로 작업 트리를 올리면 서버가 자기 큐로 순차 실행하고,
`rcm wait` 가 결과를 종료 코드(0/1/2/3)로 돌려준다. 계획서는 PLAN.md.
"""

__version__ = "0.1.0"
SCHEMA_VERSION = 1

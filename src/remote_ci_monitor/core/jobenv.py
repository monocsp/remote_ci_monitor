"""잡이 실제로 보게 될 환경 — **서버가 자기 환경으로** 미리 재는 자리.

`rcm check` 의 `local preset tools` 는 그 줄이 스스로 밝히듯 **세션의 셸**에서 잰다. 그래서
launchd·systemd 서비스에서 「셸에선 되는데 잡에선 안 되는」 차이를 못 본다. 그 차이는 두
모양으로 온다.

  ① `env_passthrough` 에 적은 이름이 **서비스 환경에 없어서** 잡에 아예 안 들어간다.
     실측(2026-11-11): 프리셋이 `JAVA_HOME` 을 넘기도록 적어 두었는데 launchd 유닛에 그 변수가
     없어, 셸에서 `rcm check` 는 초록인데 잡은 빈 값을 받는다. 사람이 추론으로 찾아냈다.
  ② PATH 가 달라 `requires` 의 도구를 잡이 못 찾는다(이미 `missing_tools` 가 잰다).

둘 다 **서버의 `os.environ`** 으로 재야 답이 맞는다. 이 모듈은 그 계산만 한다 — 파일도 시계도
안 본다. 환경은 인자고, 도구 찾기(`shutil.which`)는 `runner.missing_tools` 가 그대로 맡는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def missing_passthrough(
    env_passthrough: Sequence[str], environ: Mapping[str, str]
) -> tuple[str, ...]:
    """`env_passthrough` 중 `environ` 에 **없는** 이름 — 선언 순서대로.

    빈 문자열은 「있다」로 본다. 유닛이 일부러 빈 값을 주는 일이 있고, 그건 설정한 사람의
    뜻이지 빠진 게 아니다. 판정은 **키의 유무**다.
    """
    return tuple(name for name in env_passthrough if name not in environ)


def job_env(
    env_passthrough: Sequence[str], preset_env: Mapping[str, str], environ: Mapping[str, str]
) -> dict[str, str]:
    """잡이 받을 환경 중 **프리셋이 정하는 부분**. `runner.build_env` 의 앞 두 단계와 같다.

    `RCM_*` 와 입력(`RCM_INPUT_*`)은 잡마다 달라서 여기서 만들지 않는다 — 도구를 찾는 데 쓰는
    것은 PATH 이고, PATH 를 정하는 것은 이 두 단계다.
    """
    env = {key: environ[key] for key in env_passthrough if key in environ}
    env.update(preset_env)
    return env

# scenario_trace 의 표본 명세

`tests/test_scenario_trace.py` 의 레포 수준 시험이 늘 대조하는 표다. 행마다 그 파일의 시험이
`test_trc<n>_…` 이름으로 단다. 접두어 `TRC` 는 이 표의 시험 말고는 쓰지 않는다.

```markdown
<!-- scenario-prefix: TRC -->
| ID | 코드 블록 안의 표지와 표는 세지 않는다 |
|---|---|
| TRC99 | 이 행은 없는 것이다 |
```

<!-- scenario-prefix: TRC -->
| ID | 상황 | 기대 |
|---|---|---|
| TRC1 | 접두어를 이미 쓰는 시험 이름 · 주석이 있다 | `--prefix-usage` 가 세고 1 |
| TRC2 | 표지와 표가 펜스 코드 블록 안에 있다 | 세지 않는다 |
| TRC3 | 명세가 UTF-8 이 아니다 | 트레이스백이 아니라 `format` 문제 |
| TRC4 | 여러 글자 접두어(`TR`) | 받아들인다 |

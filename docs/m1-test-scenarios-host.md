# M1 테스트 시나리오 — 호스트 자원 (`core/hostparse.py` · `hostsample.py`)

`docs/m1-workplan.md` §1·§2·§6·§7 과 PLAN.md 「호스트 자원」·「fail-open 금지」에 대한 테스트 목록이다. 구현보다 먼저 썼다(test-first). 시그니처·키 이름은 workplan §1·§2 를 그대로 따르고, 기대값은 픽스처에서 손으로 뽑아 리터럴로 박았다. 구현과 어긋나면 맨 아래 「결정한 모호점」부터 본다.

## 픽스처

| 경로 | 출처 | 테스트가 기대는 값 |
|---|---|---|
| `tests/fixtures/host/macos/vm_stat.txt` | 실제 Apple Silicon(macOS 26) | page size 16384 · free 46220 · active 409210 · inactive 402764 · speculative 5170 · wired 192322 · compressor 469673 |
| `…/macos/sysctl_hw_memsize.txt` | 실제 | 25769803776 |
| `…/macos/top_l2.txt` | 실제 `top -l 2 -n 0 -s 1` | 표본 2개. 첫째 `46.75% user, 21.8% sys, 32.16% idle`, **둘째** `39.82% user, 14.33% sys, 45.84% idle` · Load Avg `5.42, 5.06, 3.45` |
| `…/macos/ps_Aro.txt` | 실제 `ps -Aro %cpu=,rss=,comm=` (30줄) | 1위 dartaotruntime 74.6 / 2950416 KiB · 공백 든 comm(`Orca Helper (Renderer)` · `claude bg-spare`) 포함 |
| `…/macos/ioreg_IOAccelerator.txt` | 실제 `ioreg -r -d 1 -w 0 -c IOAccelerator` | 가속기 1개. `"Device Utilization %"=1` · `"In use system memory"=27443200` — 같은 dict 에 `"In use system memory (driver)"=0` 이 **먼저** 있다 |
| `tests/fixtures/host/linux/proc_loadavg.txt` | 합성 | `0.58 0.71 0.83 2/1049 24817` |
| `…/linux/proc_meminfo.txt` | 합성(Ubuntu 22.04 모양, 54줄) | MemTotal 16281456 kB · MemAvailable 9834720 kB → total 16672210944 · used 6601457664 |
| `…/linux/proc_meminfo_no_available.txt` | 위에서 MemAvailable 줄만 뺀 것(옛 커널) | used None |
| `…/linux/proc_stat_1.txt` · `proc_stat_2.txt` | 합성, 1초 간격 | `cpu ` 차분 user 400 · nice 10 · system 120 · idle 400 · iowait 20 · irq 10 · softirq 40 · steal 0 → total 1000 → user 41.0 · sys 17.0 · idle 42.0 · busy 58.0 |
| `…/linux/ps_eo.txt` | 합성 `ps -eo %cpu=,rss=,comm= --sort=-%cpu` (15줄) | 1위 dart 88.5 / 2734512 KiB · `Web Content`(공백) 포함 |
| `…/linux/nvidia_smi.txt` | 합성 | `13, 594, 8192` |
| `…/linux/nvidia_smi_error.txt` | 합성 | `NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver. …` |

rss_mb 기대값은 내림·반올림이 같은 값이 되도록 골랐다(macOS 5위 dartvm 217680 KiB → 213 만 반올림 쪽, 아래 모호점 2).

## `tests/test_hostparse.py` — 35 함수

### `parse_vm_stat`
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| 실제 출력 → page_size 16384 + 6개 키 (`wired`·`compressor` 이름 매핑, stored 1886474 가 아니라 occupied 469673) | macos/vm_stat | `test_parse_vm_stat_real_fixture_pages_and_page_size` |
| 헤더(page size) 없는 출력 → `PAGE_SIZE_DEFAULT`(4096) | inline | `test_parse_vm_stat_without_header_falls_back_to_default_page_size` |
| 빈 문자열 · 공백만 · 쓰레기 → None | inline | `test_parse_vm_stat_empty_and_garbage_are_none` |

### `parse_sysctl_int` · `mac_memory`
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| 실제 값 · 앞뒤 공백 · 빈 문자열/문자/쓰레기 → None | macos/sysctl + inline | `test_parse_sysctl_int` |
| (active+wired+compressor)×page · compressor×page · total 그대로 | macos/vm_stat + sysctl | `test_mac_memory_real_fixture_bytes` |
| vm None → used/compressed None, total 그대로 · 둘 다 None · total 만 None | 위 | `test_mac_memory_with_none_parts` |

### `parse_top_cpu` · `parse_top_load`
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| **마지막** 표본만 (39.82/14.33/45.84, busy 54.16) · 첫 표본 값과 다름을 명시 — **mutcheck ⑤** | macos/top_l2 | `test_parse_top_cpu_uses_last_sample_not_first` |
| 표본 1개 · busy = 100 − idle | inline | `test_parse_top_cpu_single_sample_busy_is_100_minus_idle` |
| CPU usage 줄 없음 · 빈 문자열 · 쓰레기 → None | inline | `test_parse_top_cpu_missing_line_empty_and_garbage_are_none` |
| Load Avg 실제 값 | macos/top_l2 | `test_parse_top_load_real_fixture` |
| 마지막 Load Avg 줄 · 빈/숫자 아님/쓰레기 → None | inline | `test_parse_top_load_takes_last_line_and_rejects_bad_input` |

### `parse_ps`
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| 실제 macOS 상위 5 (basename · rss_mb · cpu) | macos/ps_Aro | `test_parse_ps_real_macos_fixture_top5` |
| Linux 상위 5 | linux/ps_eo | `test_parse_ps_linux_fixture_top5` |
| comm 은 두 숫자 뒤 나머지 전체의 basename(공백 보존) · `/` 없음 · limit 이 줄 수보다 커도 있는 만큼 | 둘 다 | `test_parse_ps_comm_is_rest_of_line_with_basename` |
| 섞인 순서 → cpu 내림차순 · 100 초과 cpu · limit 2/1 | inline | `test_parse_ps_sorts_cpu_desc_and_limits` |
| KiB → MB 는 가장 가까운 정수(1535→1, 1537→2, 2048→2, 0→0 · 정확히 .5 는 피함) | inline | `test_parse_ps_rss_mb_rounds_kib_to_nearest` |
| 빈 줄 · 헤더 · 숫자 아닌 필드 · 공백 줄은 건너뜀 · 빈 입력/쓰레기 → `[]` | inline | `test_parse_ps_skips_unparsable_lines` |

### `parse_ioreg_gpu`
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| 실제 출력 → util 1 · mem_used 27443200 (`(driver)` 키에 안 속음) · mem_total None · source ioreg · util 은 int | macos/ioreg | `test_parse_ioreg_gpu_real_fixture` |
| IOAccelerator 는 있는데 PerformanceStatistics 없음 · 빈 문자열 · 쓰레기 → `(None, "no IOAccelerator PerformanceStatistics")` | inline | `test_parse_ioreg_gpu_without_performance_statistics` |
| 키 일부만: util 만 / mem 만(`(driver)` 키 동반) → 없는 값 None, note None | inline | `test_parse_ioreg_gpu_partial_keys_leave_missing_none` |
| 가속기 2개 → 첫 번째 | inline | `test_parse_ioreg_gpu_multiple_accelerators_first_wins` |

### `parse_proc_loadavg` · `parse_proc_meminfo`
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| 실제 모양 · 빈/문자/필드 부족/쓰레기 → None | linux/proc_loadavg + inline | `test_parse_proc_loadavg` |
| MemTotal·MemAvailable → total·used 바이트, compressed None | linux/proc_meminfo | `test_parse_proc_meminfo_full` |
| MemAvailable 없음 → used None, total 은 있음 | linux/proc_meminfo_no_available | `test_parse_proc_meminfo_without_memavailable_leaves_used_none` |
| 빈 · 쓰레기 · 숫자 아닌 MemTotal → 전부 None 인 dict | inline | `test_parse_proc_meminfo_empty_and_garbage_are_all_none` |

### `parse_proc_stat_cpu`
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| 두 표본 차분 → user 41 · sys 17 · idle 42 · busy 58 (`approx`) | linux/proc_stat_1·2 | `test_parse_proc_stat_cpu_fixture_delta` |
| 손으로 계산 가능한 작은 수 → 12.5 / 6.25 / 81.25 / 18.75 (이진 정확) | inline | `test_parse_proc_stat_cpu_inline_math` |
| 같은 표본 두 번 → total 0 → None | linux/proc_stat_1 | `test_parse_proc_stat_cpu_zero_delta_is_none` |
| 빈/한쪽만 빈/쓰레기 · `cpu0` 만 있고 합계 `cpu ` 줄 없음 → None | inline | `test_parse_proc_stat_cpu_bad_input_is_none` |

### `parse_nvidia_smi`
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| `13, 594, 8192` → util 13 · MiB→bytes · source nvidia-smi | linux/nvidia_smi | `test_parse_nvidia_smi_ok` |
| GPU 2개 → 첫 줄 | inline | `test_parse_nvidia_smi_multi_gpu_uses_first_line` |
| 드라이버 오류 문구 · 빈 · 공백만 · 쓰레기 → None | linux/nvidia_smi_error + inline | `test_parse_nvidia_smi_error_and_empty_are_none` |

### `stale`
| 시나리오 | 테스트 |
|---|---|
| 정확히 3×interval(15s@5 · 6s@2 · 14.9s@5) → **stale 아님** — **mutcheck ④** 표적(`> 0 × interval` 이 되면 여기가 빨개진다) | `test_stale_exactly_three_intervals_is_not_stale` |
| 3×interval + 1s(16s@5 · 7s@2) · 1시간 → stale | `test_stale_one_second_past_three_intervals_is_stale` |
| 음수 나이(시계 역행) · 나이 0 → stale 아님 | `test_stale_negative_or_zero_age_is_not_stale` |

## `tests/test_hostsample.py` — 17 함수 (gpu off 는 darwin/linux 파라미터라 18 케이스, Linux 스모크는 macOS 에서 skip)

가짜 주입: `runner`(argv[0] basename → 픽스처, `fail` 집합은 None) · `read_file`(경로 → 픽스처 목록, `/proc/stat` 은 1·2 순서) · `now_fn`(고정 시계 `Clock`, `advance()`) · `loadavg`(`FlakyLoad`, `fail` 이면 None) · `platform` · `cpu_count` · `HostSection(interval_seconds=2, gpu="auto", top_processes=5, history_samples=3)`.

### macOS 경로
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| 5개 명령 전부 성공 → name · source local · os darwin · cores 10 · sampled_at=now · interval 2 · load · cpu(54.16) · memory · gpu · gpu_note None · top 5개 · `latest()` = ([sample], None) · 부른 명령 집합 = {vm_stat, sysctl, top, ps, ioreg} | macos 5종 | `test_macos_sample_once_fills_every_field_from_fixtures` |
| ioreg 실패 → gpu None + gpu_note 문자열, cpu·memory·load 그대로, hosts_error 없음 | macos | `test_macos_ioreg_failure_blanks_gpu_only` |
| top 실패 → cpu None, 나머지 그대로 | macos | `test_macos_top_failure_blanks_cpu_only` |
| vm_stat 실패 → used/compressed None, total_bytes 는 sysctl 값 | macos | `test_macos_vm_stat_failure_keeps_total_bytes` |
| ps 실패 → top 빈 튜플 | macos | `test_macos_ps_failure_gives_empty_top` |
| loadavg 가 None(실패) → 표본은 만들어지고 load 는 None(또는 top 의 Load Avg 폴백) | macos | `test_loadavg_failure_does_not_kill_the_sample` |
| runner 전부 None + loadavg None → `sample_once()` None · `error` 문자열 · `latest()` = ([], "sampler: all collectors failed…") | 없음 | `test_all_collectors_failed_gives_no_sample_and_hosts_error` |
| `gpu="off"` → gpu None · gpu_note "disabled" · ioreg/nvidia-smi 를 안 부름 (darwin·linux) | macos / linux | `test_gpu_off_skips_probe_and_notes_disabled[darwin-ioreg]` · `[linux-nvidia-smi]` |

### Linux 경로 (`loadavg=None` 으로 `/proc/loadavg` 만 쓰게 한다)
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| `/proc/loadavg`·`/proc/meminfo`·`/proc/stat` ×2 · ps · nvidia-smi → os linux · cores 8 · load (0.58, 0.71, 0.83) · cpu 41/17/42/58 · memory · gpu(nvidia-smi) · top 5 · `/proc/stat` 읽기 정확히 2번 · macOS 명령은 안 부름 | linux 전부 | `test_linux_sample_once_reads_proc_twice_and_nvidia_smi` |
| nvidia-smi 없음(runner None) → gpu None · gpu_note "nvidia-smi not found", 나머지 그대로 | linux | `test_linux_nvidia_smi_missing_notes_not_found` |
| nvidia-smi 가 오류 문구를 찍음 → gpu None · gpu_note 문자열 | linux/nvidia_smi_error | `test_linux_nvidia_smi_error_text_is_not_a_gpu` |
| `/proc/meminfo` 못 읽음 → memory None(또는 전부 None), load·cpu 그대로 | linux | `test_linux_meminfo_failure_blanks_memory_only` |

### history · publish · run 루프
| 시나리오 | 픽스처 | 테스트 |
|---|---|---|
| 첫 표본의 history = 자기 항목 하나 `{at, cpu_busy 54.16, mem_used_bytes, gpu_util_pct 1}` | macos | `test_history_first_sample_has_one_entry_of_itself` |
| 표본 4개(maxlen 3) → 가장 오래된 at 밀려남 · 키 집합 고정 · **전부 실패한 주기는 항목을 안 넣음**(그 시각이 history 에 없음) · 회복 후 이어 붙음 | macos | `test_history_is_capped_and_failed_cycles_add_nothing` |
| top·ioreg 실패한 표본의 history 항목은 cpu_busy·gpu_util_pct None, mem_used_bytes 는 값 | macos | `test_history_entries_carry_none_for_missing_fields` |
| `start()` → 8초 안에 표본 1개 + `publish("host_sample", {name, sampled_at})` → `stop.set()` → `join(5)` 후 죽어 있음 | macos | `test_run_loop_samples_publishes_and_stops` |

### 스모크
| 시나리오 | 테스트 |
|---|---|
| `sys.platform.startswith("linux")` 일 때만 실제 `/proc`·`ps` 로 `sample_once()` ≠ None · load·memory·cpu 값 있음 (CI ubuntu 러너) | `test_linux_smoke_reads_real_proc` |

## 다루지 못한 것 · 일부러 안 한 것

| 항목 | 이유 |
|---|---|
| 타입이 틀린 입력(`None`·bytes)에 `TypeError` | workplan §1 이 「허용」이지 「요구」가 아니라 강제하지 않았다 |
| `parse_nvidia_smi` 의 `[N/A]` 열 · 열 2개뿐인 줄 | 스펙 미정(None 인지 부분값인지). 구현이 정하면 추가 |
| `parse_ioreg_gpu` 에 PerformanceStatistics 는 있는데 두 키 다 없음 | 스펙 미정(전부 None + note None 인지, note 를 주는지) |
| `parse_sysctl_int("hw.memsize: N")` (`-n` 없이 찍은 모양) | 스펙 미정. 샘플러는 항상 `-n` 을 쓴다 |
| `latest()` 를 첫 표본 전에 부를 때 · 실패 주기 뒤 `latest()` 가 직전 표본을 유지하는지 | 스펙 미정. history 테스트는 이 값을 안 본다 |
| `cpu_count=None` 일 때 `cores` | None 이 「기본값(os.cpu_count) 써라」인지 「모른다」인지 스펙 미정이라 안 본다 |
| 주입한 `loadavg` 가 예외를 던지는 경우 | 실패 규약은 runner 처럼 None 반환으로 봤다(기본값은 샘플러가 OSError 를 삼키는 래퍼) |
| `_run_command` 의 timeout 8초 · 비정상 종료 코드 처리 | 실제 subprocess 를 안 부른다는 규칙. Linux 스모크가 기본 runner 를 한 번 지나간다 |
| macOS 실기 스모크 | `top -l 2` 가 1초 걸리고 실기 의존이라 결정 D 대로 Linux 만 |
| `interval_seconds` 하한 2 강제 | `config.py` 몫(`test_config.py`) |
| `status.host_json` 이 `hostparse.stale()` 을 쓰는지 · `hosts[]` JSON 모양 | `test_status_schema.py`/`test_server.py` 몫 |
| `host_sample` 이벤트가 EventBus/SSE 를 타는지 | `test_events.py`/`test_server.py` 몫 |
| run 루프의 주기 정확도(2초마다인지) | 실시간 의존이라 「8초 안에 1개 이상 · stop 으로 종료」만 본다 |

## 결정한 모호점 (구현과 어긋나면 여기부터 본다)

1. **busy = 100 − idle** (workplan §1 의 예시 68.55 는 user+sys 로 계산한 값이라 공식과 어긋난다 — 공식을 따랐다). 실제 픽스처 마지막 표본은 100 − 45.84 = **54.16** 이고 float 등호가 정확히 성립한다.
2. **rss_mb 는 가장 가까운 정수**(`round(kib / 1024)`). 스펙은 「정수」만 말한다. 픽스처 값은 내림·반올림이 같도록 골랐고, 차이가 나는 것은 `test_parse_ps_rss_mb_rounds_kib_to_nearest`(1535→1, 1537→2) 와 macOS 5위(217680 → 213) 뿐이다.
3. **comm 은 두 숫자 필드 뒤 나머지 전체**에 basename — 실제 `ps` 출력엔 공백 든 경로·comm 이 있다(`Orca Helper (Renderer)` · `claude bg-spare` · `Web Content`). `split()[-1]` 로 자르면 빨개진다.
4. **history 는 현재 표본을 포함**한다 — 첫 표본의 `history` 길이 1, `at` 은 자기 `sampled_at`.
5. `HostSample.source == "local"` (PLAN 「서버 API」 JSON 예시).
6. `vm_stat` 헤더가 없으면 `PAGE_SIZE_DEFAULT`(상수가 있는 이유로 봤다).
7. `loadavg=None` 은 「그 소스 없음」 — Linux 는 `/proc/loadavg` 를 읽어야 한다. macOS 에서 `loadavg()` 가 None(실패)을 주면 load None 이든 top 의 Load Avg 폴백이든 허용.
8. 전부 실패: `latest()[1]` 은 `"sampler: all collectors failed"` 로 시작. `self.error` 는 비어 있지 않은 문자열이면 된다(전체 문구인지 사유만인지 안 본다).
9. `gpu_note` 문구를 고정한 것은 `"disabled"` · `"nvidia-smi not found"` · 파서 note `"no IOAccelerator PerformanceStatistics"` 뿐. ioreg 실행 실패·nvidia-smi 오류 문구는 「비어 있지 않은 문자열」만 본다.
10. `publish` 의 `sampled_at` 은 iso 문자열(SSE JSON)이 맞지만 datetime 도 받아 준다. publish 는 `run()` 에서 부르는 것으로 보고 `sample_once()` 단독 호출엔 publish 를 요구하지 않는다.
11. Linux `/proc/stat` 두 번째 읽기 사이의 1초 대기는 주입하지 않는다 — Linux 샘플러 테스트는 각각 1초쯤 걸린다.
12. **가속기가 여럿이면 첫 번째**(workplan §1 문구 그대로) — `test_parse_ioreg_gpu_multiple_accelerators_first_wins` 는 util 5 / 90 두 블록에서 5 를 기대한다. 구현이 「util 은 max · 메모리는 합」을 택하면 이 테스트가 빨개진다. 그 의미가 더 낫다고 보면 workplan §1 을 먼저 고치고 테스트를 따라 바꾼다(스펙이 정본).

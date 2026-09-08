# 설계 리뷰 — M5f 병렬 레인 부하 게이트 (2026-09-08)

`docs/m5f-workplan.md` 초안에 대한 크로스리뷰. **Codex 는 이날 사용 한도에 걸려 findings 를 내기 전에
끊겼다**(`gpt-5.5` 는 계정 접근 불가 → `gpt-6-astra` → 오너 요청으로 `gpt-5.6-sol`/medium, 코드는 다 읽고
`You've hit your usage limit` 에서 종료). 대신 **격리 에이전트 두 대**에 역할을 나눠 돌렸다 — 하나는
명세가 코드에 대해 사실을 틀린 곳, 하나는 설계 구멍. 아래는 받은 결과 그대로이고, ✅ 는 이 세션이 코드로
직접 확인한 것이다. 반영은 명세 §13.

## A. 사실 검증

- ✅ **must-fix:** `tests/test_e2e_m3.py:84` `test_group_serializes_while_the_other_lane_keeps_working` starts a real `App` with `lanes=2` and asserts lane 2 picks up the `solo` job while lane 1 runs `qa` (`tests/test_e2e_m3.py:99` `assert t[solo][0] < t[first][1]`), inside a 20 s `wait_terminal`. With `admission = "load"` on by default, lane 2 hits `no_sample` at `app.start()` (the sampler has produced nothing yet), then needs 3 history entries ≈ 15 s and a 30 s cooldown — the job cannot start in time. This is the same test the spec cites twice as "M3 e2e 로 잠겨 있다". 완료 기준 5 ("기존 테스트 전부 초록") is false; PR 2a must set `admission = "always"` in that test and say so.

- ✅ **must-fix:** 결정 39 and 완료 기준 1 claim `lanes = 1` installs are unaffected. `[server] lanes` counts **local lanes only** (`src/remote_ci_monitor/worker.py:448`, `src/remote_ci_monitor/server.py:359-361`); a remote worker's lane count comes from its own `worker.toml` (`config.py:899`, `remote_workers.py:249-256`, rendered 1..N at `remote_workers.py:153`). A server with `lanes = 1` plus one `--lanes 4` worker gets three newly gated lanes.

- ✅ **must-fix:** §6's `confidence(shared=…)` has no carrier. `compute_queue` never calls `confidence()`; it is called at render time from `core/status.py:176` with only `row.estimate`/`row.reason`, re-derived from the serialized JSON at `core/render_text.py:193`, and again client-side in `confidenceBadge` (`web/app.js:263-277`). `shared` needs a new additive field on `Estimate` + `estimate_json` (`core/status.py:75-88`), and all three render sites must change together or `rcm top` and the web print different badges.

- ✅ **must-fix:** §5.4's web list is incomplete. The "Not moving" ordering is duplicated in JS as `var ACTIONABLE = [...]` (`web/app.js:16`) and `notMoving()` drops any reason not in it (`web/app.js:389`). **`ACTIONABLE_REASONS` in `core/model.py:93` is not used by any Python code** — the live copy is the JS one.

- ✅ **must-fix:** §12's "옛 CLI 가 `held` 를 그대로 찍는다" cites `core/render_text.py:265`, which is inside the `if lanes == 1` branch (`:263-265`) — and lane 1 is never held by this design. For `lanes >= 2` the header is `f"lanes {busy}/{lanes} busy"` (`:267`) counting only `busy` (`:260`) and listing only `down` (`:261`, `:268`): a held local lane is **not printed at all**. Only the remote pill prints the state verbatim (`:281`). The web is safe: `workerState()` guards with `I18N.has` (`web/app.js:510-514`).

- ✅ **must-fix:** §9 says "8 → 10" and numbers the new mutants ⑦⑧. `MUTANTS` already holds **10** entries (`scripts/mutcheck.py:45-127`) and ⑦ is taken by `retention-active-guard` (`docs/m3-test-scenarios-a.md:63`). It is 10 → 12, the new ones are ⑪⑫, and `AGENTS.md:40` ("8 known mutations") plus `scripts/mutcheck.py`'s own docstring ("변이 6종") are stale.

- ✅ **should:** §5.1's `hold_code` on `server.workers[]` breaks `tests/test_status_schema.py:224`, which asserts **exact set equality** on the worker object's keys. §8 calls that file an "(추가)" — it is an edit of an existing exhaustive assertion.

- **should:** §4.3 step 8 (memory) has no unknown branch, and memory really can be unknown: no producer emits `free_bytes` — the dict is `{total_bytes, used_bytes, compressed_bytes}` (`core/hostparse.py:89` macOS, `:207-213` Linux), `used_bytes` is None without `MemAvailable` (`:209-211`) or when `vm_stat` lacks active/wired/compressor (`:84-86`). As written that is a fail-open the spec elsewhere forbids.

- ✅ **should:** §4.3 steps 6-7 read `history[].cpu_busy` as a number. `sample_once` appends an entry **every cycle regardless of collector failure**, with `"cpu_busy": (raw["cpu"] or {}).get("busy")` (`hostsample.py:252-257`), so entries can carry `None`; remote entries can be `{}` (`core/hostparse.py:390`). `cpu_busy <= cpu_max_percent` raises `TypeError` on None.

- ✅ **should:** §4.5's "로컬이면 `self.sampler.latest()`" — `App.sampler` is None until `App.start()` (`server.py:209`, `:256`) and `latest()` returns `(list, error)`, not a sample (`hostsample.py:157-161`). The accessor that handles both is `App._hosts()` (`server.py:631-639`).

- **should:** 완료 기준 3 ("워커의 샘플러만 죽여도 닫힌다") holds **only when the worker is also upgraded**. The §3.1 fix lives in the worker binary (`remote_worker.py:242-256`); `host_sample` is optional on the wire, the server only updates its copy when one arrives (`remote_workers.py:518-525`) and re-stamps `sampled_at = now` (`:520`). An old worker keeps shipping a frozen sample and the server cannot tell.

- ✅ **should:** §8's e2e "샘플러의 `runner`/`read_file` 주입 자리를 그대로 쓴다" — those exist on `HostSampler.__init__` but `App.start()` builds the sampler itself with no way to pass them (`server.py:256-263`). The established pattern is replacing the object: `srv.app.sampler = StubSampler([...])` (`tests/test_server_m1.py:208-215`).

- ✅ **should:** §3's "원격 표본은 heartbeat 만큼 늦지만 `stale` 판정에 묻힌다" — the server re-stamps `sampled_at = now` on receipt (`remote_workers.py:520`), so the lag is **erased, not buried**. §3.1 describes the same line correctly; §3 contradicts it.

- ✅ **nit:** §5.2's "보류 레인은 `idle_since` 에 넣지 않는다 — 안 넣으면 `not_scheduled` 가 헛울린다" has the conditional inverted, and needs no code change: `core/queue.py:238-239` only adds lanes with `state == WORKER_IDLE`.

- ✅ **nit:** §2 cites `remote_workers.py:115` for the sample store; `:115` is the empty-dict declaration. The store is `:525`, the re-stamp `:520`.

- **nit:** §3's "`rcm worker` 는 한 줄도 안 바뀐다" is contradicted by §3.1. Say "한 줄만 바뀐다 — 프로토콜은 그대로다."

- ✅ **nit:** §4.5 puts `admit` on `Worker.__init__`; it must also thread through `start_workers()` (`worker.py:427-452`), the only place `App` builds lanes (`server.py:225`).

## B. 설계

- ✅ **must-fix — the gate is not a rate limiter.** `admission_cooldown_seconds` is read before `store.claim` and written after, with nothing in between. All local lanes block on the same `wake` event and then run `get_paused() → admit → claim` independently (`worker.py:242-251`); remote lanes do the same in `_try_claim` (`remote_workers.py:343-353`). With `lanes = 4` idle and 4 queued jobs, lanes 2-4 each read `last_admit_at = None` and the same history, all return open, and four jobs start in one tick. §4.4's "머신당 30초에 하나" is false for exactly the burst it exists to cover. Smallest correct scheme: one lock per machine, taken **only by gated lanes**, held across `decide → store.claim → stamp`.

- **must-fix — the spec never says whether a lane-1 claim stamps the cooldown, and both answers are broken.** If it stamps, a machine fed short jobs resets the window on every lane-1 claim and lanes ≥ 2 never open. If it does not, the cooldown misses the single most common load event on the box (lane 1 picking up a heavy job) and lane 2 admits ~0.5 s later off pre-job history.

- ✅ **must-fix — "machine" is really "worker registration", and the owner's own Mac runs two of them.** `_last_admit` keys by `None` (local) or worker name, and samples come from two places (`server.sampler` vs `_worker_samples[name]`). The live setup is `rcm serve` **and** a `mac2` worker on one Mac, so that box has two permanently-ungated lane 1s and two independent cooldowns. (This session's check: server `lanes = 1` pool `default`, worker `lanes = 1` pool `mac2`, `name = "mac2"` — and because `host_name` is the worker's *display* name (`remote_worker.py:184`), co-location cannot be detected automatically.)

- ✅ **must-fix — switching `can_start` to `open` creates a new null-ETA case paired with the wrong reason.** `open` is empty when lane 1 is down and lane 2 held: `live` is non-empty so `worker_down` never fires (`core/queue.py:308`), yet `wait`/`finish`/`ahead` all stay None (`:286-300`). Every waiting job reads "held by load · eta —" while the real fault is a dead lane-1 thread.

- ✅ **must-fix — the ETA greedy is keyed by lane *number*, so excluding held lanes is a no-op in any pool with local and remote lanes.** `lane_free`, `lane_last_job`, `idle_since` are `dict[int, ...]` keyed by `w.lane` (`core/queue.py:235-237`) and busy jobs are attributed by `job.lane` alone (`:249-252`). The default pool holds local lanes 1..N **plus** every remote `default` worker's lanes 1..M (`server.py:348-352`), and `rcm worker` defaults to `pool = "default"` (`config.py:209`), so local lane 2 and `build-02/2` collapse into key `2`. Key by `(w.worker, w.lane)` and attribute with `(job.worker_name, job.lane)` — `Job.worker_name` exists (`core/model.py:226`).

- ✅ **must-fix — `memory_min_free_percent` has no measurable input and means two different things on the two OSes** — the exact objection §4.2 raises against loadavg. macOS `used = (active + wired + compressor) × page` excludes inactive/purgeable/file cache; Linux `used = total − MemAvailable`. 10% free on Linux is roughly out of memory; on macOS it is routine. The swap argument in §4.4 is unsupported by anything the sampler reads (no swap-in/out, no macOS memory pressure).

- ✅ **must-fix — `admission_samples` counts list entries, not elapsed time.** `_history` is appended only on a successful sample and records no gaps (`hostsample.py:258-259`). After a 10-minute stall the window is `[old, old, fresh]` and the gate opens on one real observation. Require the last N entries to be contiguous (each `at`-to-next-`at` delta ≤ `2 × interval_seconds`), comparing **entry to entry only** — `history[].at` is the worker's clock while `sampled_at` is the server's (`core/hostparse.py:360-366`), and `at` is an ISO string (`core/status.py:41` `parse_iso`).

- ✅ **must-fix — `admission_samples` can exceed `[host] history_samples`, closing lanes ≥ 2 forever with no error.** `history_samples` validates only `>= 1` (`config.py:128,645`). Add a cross-key check in the style of `config.py:609-613`.

- ✅ **should — held remote lanes eat the long-poll slots the open lanes need.** `worker_claim` gets None, then takes one of only 8 `CLAIM_WAIT_SLOTS` and re-polls for 20 s (`remote_workers.py:61,324-341`). Two 4-lane workers put 6 held lanes in those 8 slots; open lanes get an immediate 204 and fall back to 1 s polling. Evaluate the gate **before** `_claim_slots.acquire`.

- ✅ **should — two independent evaluations of the same decision disagree by construction and disarm `not_scheduled`.** `Worker._set` resets `_since` on every state change (`worker.py:222-228`), so a lane flapping idle↔held never accumulates 10 s and `REASON_NOT_SCHEDULED` can no longer fire on that machine (`core/queue.py:237-240,314-318`). The status path can also report `idle` for a lane the claim path just held. Decide once per (machine, lane) in the claim path and have the status path read the stored value.

- ✅ **should — `held_by_load` in `ACTIONABLE_REASONS` above `overdue` makes "Not moving" permanently loud on a machine that is working correctly.** §12 itself calls a held lane "의도한 동작" — self-inflicted, self-healing, nothing for a human to do, the same species as `paused`, which is deliberately last. Users learn to ignore the panel, hiding the `worker_down`/`stuck` entries it exists for. This contradicts 결정 45.

- ✅ **should — §6 fixes the cosmetic half and leaves the load-bearing half: `overdue` and `stuck` come from the same medians.** `expected` drives `overdue = elapsed > expected` and `stuck = elapsed > 3 × expected` (`core/queue.py:178-184`), `finish_at` is nulled for both, and `overdue` is in the JS `ACTIONABLE`. Medians measured alone + jobs now sharing = every shared run trips `overdue` early and loses its ETA. A pooled median over a mixture of alone- and shared-runs is also bimodal — it jumps when the mixture crosses 50%. Splitting need not lose samples: use a hierarchical fallback (shared-key median when its `sample_count >= min_samples`, else pooled, then `preset`/`default`), which strictly dominates and invents no number.

- **should — "lane 1 is never held" proves no permanent starvation, not that the queue keeps moving.** One CPU-hungry job on lane 1 pins the box by itself, so a `lanes = 4` machine delivers `lanes = 1` throughput exactly when the owner raised `lanes`. Defensible, but with `hold_max_seconds` dropped nothing measures how long the gate has been shut. Add the measurement instead of the valve: `held_since` per lane (measured, not invented) and a `rcm check` **warning** (never FAIL — `health()` correctly ignores non-`down` states, `server.py:1368-1372`).

- ✅ **should — `admission_samples = 3` is not "15초 연속 여유"; it is three one-second probes spread over 15 seconds.** macOS CPU is the usable second sample of `top -l 2 -n 0 -s 1` (`hostsample.py:47`, `core/hostparse.py:92-102`); Linux is a 1 s `/proc/stat` delta (`hostsample.py:42,203-206`). The gate observes 3 s of the 15 s window. Fix §4.4's wording — it is the stated justification for the constant — and note this weakens §4.2's own case against load1.

- ✅ **should — the remote-sample freshness bound is three heartbeats.** `stale()` is `age > 3 × interval_seconds` (`core/hostparse.py:282-285`); for a worker sample `interval_seconds` is the worker's sampler period (5 s) while the transport is the 5 s heartbeat. Two dropped heartbeats put the sample at 15 s and close lanes ≥ 2 on a completely idle machine. Use `max(3 × interval_seconds, 3 × worker_heartbeat_seconds)` for `source == "worker"` samples.

- **should — 결정 39's "existing installs are unaffected" needs a CHANGELOG behaviour-change entry and a startup log line** naming the effective policy and cap when lanes ≥ 2 (next to the existing banner, `server.py:1997`) — otherwise the first symptom is "my second lane stopped working".

- ✅ **should — the time constants are expressed in samples and `[host] interval_seconds` has no upper bound** (`config.py:641,908`), so `interval_seconds = 60` turns `admission_samples = 3` into a 3-minute warm-up while the cooldown stays 30 s. Validate the relation or express the window in seconds.

- ✅ **should — §7's argument is wrong even though its conclusion is right.** `concurrency_group` is mutual exclusion by label: giving two heavy presets the same group also serialises each preset **with itself** (two `build` jobs can no longer run in parallel — locked by the M3 e2e). It cannot express "heavy but parallel with its own kind". Keep refusing `heavy`, but use the honest argument: the gate measures, a static weight guesses.

- ✅ **nit — `core/admission.py` must not take a `config.py` object.** The pure layer's convention is a module-local frozen dataclass the server maps into (`core/queue.py:65-79` `QueueConfig`).

- ✅ **nit — the SSE `server` event carries no hold reason.** `_publish_server` emits only `{lane, state, job_id, worker}` (`server.py:405-410`), so the web pill shows a bare `held` until the next full poll.

# M5c 테스트 시나리오 A — mDNS/DNS-SD 패킷 · 규칙 · 루프백 왕복 (2026-09-08)

`docs/m5c-workplan.md` §1(모델) · §2(응답기) · §3(질의기) 과 §5 표의 A 행을
`tests/test_mdns_packets.py`(72 함수 · 파라미터 포함 125건) · `tests/test_mdns_loopback.py`(6 함수, 진짜 소켓)
로 옮긴 것이다(test-first, 역할 A). `src/` 는 건드리지 않았다.

작성 중에 브랜치에 구현(`57d1ca4`)과 B 의 테스트(`d87ca4d`)가 먼저 올라왔다. 그래서 이 파일의 테스트는
**그 구현의 API 이름을 따르되 명세의 규칙을 잠근다** — 이름이 임의인 곳(상수 이름 · SRV 데이터 모양 ·
소문자 정규화)은 구현에 맞췄고, 규칙이 어긋나는 곳만 빨강으로 남겼다(아래 「의도적 빨강」 2건).
오늘 결과: packets 124 초록 / 1 빨강 · loopback 4 초록 / 2 빨강(세 번 연속 같은 결과 — 시간 의존 없음).

공통: 이름은 끝에 `.` 을 붙인 FQDN(`macmini._rcm._tcp.local.` · `macmini.local.`) · 디코더는 소문자로
정규화 · 인코더는 이름을 압축하지 않는다(디코더는 압축을 읽는다) · IP 는 TEST-NET-1(`192.0.2.x`) 이라
실제 인터페이스와 무관하다 · 루프백 파일은 포트 **15353**(시스템 mDNS 데몬 5353 과 절대 겹치지 않음) ·
인터페이스 127.0.0.1 · `IP_MULTICAST_LOOP=1` · 소켓은 `sock_factory` 로 끼운다 · 수집 시점에 0.5초 프로브가
실패하면 파일 전체 skip(이유 문구 포함) · sleep 없음, 기다림은 전부 소켓 타임아웃(테스트당 ≤ 1.0초).

## 잠근 API (`src/remote_ci_monitor/core/mdns.py` · `src/remote_ci_monitor/mdns.py`)

- 상수: `SERVICE = "_rcm._tcp.local."` · `MAX_PACKET = 512` · `MDNS_GROUP = "224.0.0.251"` · `MDNS_PORT = 5353` ·
  `TYPE_A/PTR/TXT/SRV/ANY = 1/12/16/33/255` · `CLASS_IN = 1` · `CACHE_FLUSH = 0x8000` · `TTL_PTR = 4500` ·
  `TTL_OTHER = 120` · `MAX_ANSWERS_PER_SECOND = 20`. 오류 `MdnsError(ValueError)`.
- frozen dataclass: `Question(name, qtype, unicast=False)` · `Record(name, rtype, ttl, data)`
  (A → `"1.2.3.4"` · PTR → 이름 · SRV → `(priority, weight, port, target)` · TXT → `tuple[str, ...]` · 그 외 → `bytes`) ·
  `Found(name, host, port, ips: tuple[str, ...], version, lanes=None)` + `address` 프로퍼티
  (`http://<첫 A>:<port>`, A 없으면 `http://<host 끝 . 뗀 것>:<port>`).
- `instance_name(name) -> "<name>._rcm._tcp.local."` · `encode_query(list[(name, qtype)]) -> bytes` ·
  `encode_response(instance, host, port, ips, txt, *, ttl_ptr=4500, ttl_other=120, query_id=0) -> bytes` ·
  `encode_goodbye(instance, host, port) -> bytes`(TTL 전부 0) · `decode(packet) -> (list[Question], list[Record])` ·
  `is_query(packet) -> bool` · `should_answer(questions, instance, host) -> bool` ·
  `found_from_records(records) -> list[Found]` · `RateLimiter(max_per_second=20).allow(now) -> bool`.
- `Responder(instance, host, port, *, ips_fn, txt_fn, log, mdns_port=5353, sock_factory=None, clock)` —
  `handle(packet, addr) -> bytes | None` · `start() -> bool` · `stop()`(멱등) · `error`.
  `discover(timeout=1.5, *, sock_factory=None, clock, mdns_port=5353) -> list[Found]`(이름순). `local_ipv4s()`.

## `tests/test_mdns_packets.py` — 순수 함수 · `Responder.handle` · 가짜 소켓 `discover`

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 상수가 명세 값(서비스 이름 · 그룹/포트 · TTL 120/4500 · 상한 20 · 타입 번호) · `MdnsError` 는 ValueError | `test_constants_are_the_spec_values` | §1 · §2 |
| 2 | `instance_name("macmini")` → `macmini._rcm._tcp.local.` | `test_instance_name_helper` | §1 |
| 3 | Question/Record/Found 필드 이름·순서 · 모두 frozen · `lanes` 기본 None | `test_dataclasses_are_frozen_with_the_locked_fields` | §3 |
| 4 | `Found.address` 는 첫 A 의 IP, A 없으면 `.local` 이름(끝 `.` 뗌) | `test_found_address_prefers_the_first_ip_and_falls_back_to_the_host_name` | §1 · §3 |
| 5 | 질의: id 0 · QR 0 · 질문 1 · 라벨 인코딩 · QU 비트 없음 · 되읽으면 같은 질문 | `test_encode_query_is_a_plain_multicast_question` | §3 |
| 6 | 질문 여러 개는 순서 그대로 | `test_encode_query_keeps_question_order` | §3 |
| 7 | 질문 0개 · 라벨 64바이트 → MdnsError | `test_encode_query_rejects_no_questions_and_a_label_over_63_bytes` | §2 보안 |
| 8 | `is_query`: QR 비트 · 12바이트 미만은 False | `test_is_query_reads_the_qr_bit_and_tolerates_short_input` | §2 |
| 9 | 응답 헤더: id 0 · QR=1 · AA=1 · RCODE 0 · 질문 0 · answer 에 PTR+SRV+TXT+A×n, 나머지 섹션 0 | `test_response_header_is_an_authoritative_answer_with_all_records_in_the_answer_section` | §2 |
| 10 | 레코드 순서 PTR · SRV · TXT · A(IP 마다) | `test_response_record_order_is_ptr_srv_txt_then_one_a_per_ip` | §1 |
| 11 | PTR `_rcm._tcp.local.` → 인스턴스, TTL 4500 | `test_response_ptr_is_service_to_instance_with_ttl_4500` | §1 · §2 |
| 12 | SRV = `(0, 0, port, host)`, TTL 120 | `test_response_srv_carries_priority_weight_port_and_host` | §1 |
| 13 | TXT = dict 순서대로 `k=v` | `test_response_txt_is_key_equals_value_in_dict_order` | §1 |
| 14 | A 는 IP 마다 하나, 주어진 순서 | `test_response_one_a_record_per_ip_in_the_given_order` | §1 |
| 15 | cache-flush 비트: SRV/TXT/A 켬, PTR 끔(공유 레코드) — 와이어 클래스 필드로 확인 | `test_response_cache_flush_bit_is_on_srv_txt_a_but_not_ptr` | §2 (RFC 6762 §10.2) |
| 16 | 인코더는 압축하지 않는다 — SRV/A rdata · TXT 문자열이 패킷에 그대로 | `test_response_wire_rdata_is_uncompressed` | §5 표 |
| 17 | 빈 TXT 는 0바이트 문자열 하나로 나가고 디코더는 `()` | `test_response_empty_txt_decodes_to_no_strings` | RFC 6763 §6.1 |
| 18 | IP 없음 → A 레코드 없음 | `test_response_without_ips_has_no_a_records` | §1 |
| 19 | `ttl_ptr`/`ttl_other` 가 그대로 실린다 | `test_response_ttl_parameters_are_honoured` | §2 |
| 20 | 인코더도 이름을 소문자 FQDN 으로 정규화(끝 `.` 없어도 같은 패킷) | `test_response_normalises_instance_and_host_case` | §2 |
| 21 | 512 바이트를 넘는 응답은 인코더가 거부 | `test_response_over_max_packet_is_refused_by_the_encoder` | §2 보안 |
| 22 | 잘못된 IP(문자열 · IPv6 · 옥텟 3개 · 999) · 255 바이트 넘는 TXT 항목 → MdnsError (×5) | `test_response_rejects_bad_ip_and_long_txt` | §2 · 결정 35 |
| 23 | `.` 이 든 이름(`mac.mini`, `_NAME_RE` 허용)이 인코딩→디코딩→조립을 지나 그대로 | `test_instance_label_with_a_dot_round_trips` | §2 `advertise_name` |
| 24 | 인코더 출력을 디코더가 정확히 되읽는다(5 레코드) | `test_decode_round_trips_the_encoder_output_exactly` | §5 표 |
| 25 | 헤더만 있는 빈 패킷 → `([], [])` | `test_decode_empty_header_only_packet_is_empty` | §5 표 |
| 26 | 손으로 만든 압축 포인터(소유자 이름 · PTR 대상 · SRV target)를 따라간다 | `test_decode_follows_compression_pointers_in_owner_names_and_rdata` | §2 보안 |
| 27 | answer/authority/additional 세 섹션을 순서대로 · 모르는 타입(NSEC)은 raw bytes · TXT 빈 문자열 버림 | `test_decode_reads_answer_authority_and_additional_sections_in_order` | §5 표 |
| 28 | 이름은 소문자 FQDN 으로 · QCLASS 최상위 비트 → `Question.unicast` | `test_decode_lowercases_names_and_reads_the_qu_bit` | RFC 6762 §16 |
| 29 | 자기 참조 · 2단 루프 · 라벨 뒤 루프 · 패킷 밖/끝 포인터 · 예약 라벨 01/10 → MdnsError (×7) | `test_decode_rejects_pointer_loops_out_of_range_pointers_and_reserved_labels` | §2 보안 |
| 30 | 유효한 응답의 **모든** 진부분 접두(0 ~ n-1 바이트) → MdnsError | `test_decode_rejects_every_proper_prefix_of_a_valid_response` | §2 보안 |
| 31 | 헤더의 카운트가 본문보다 크면 MdnsError | `test_decode_rejects_a_header_whose_counts_exceed_the_body` | §2 보안 |
| 32 | 정확히 512 바이트는 읽고 513 은 거부(`\x00`×513 도) | `test_decode_accepts_exactly_max_packet_and_refuses_one_more_byte` | §2 보안 |
| 33 | `should_answer` 행렬 26건: 서비스 PTR/ANY · 인스턴스 SRV/TXT/ANY · 호스트 A/ANY 만 True, 대소문자 무시, 다른 서비스/인스턴스/호스트 · AAAA · `local.` 은 False | `test_should_answer_matrix` | §2 |
| 34 | 빈 목록 False · 무관한 것만 False · 하나라도 맞으면 True | `test_should_answer_needs_only_one_matching_question` | §2 |
| 35 | QU 비트가 있어도 답한다 | `test_should_answer_ignores_the_qu_bit` | §2 |
| 36 | PTR+SRV+TXT+A → Found 하나(name · host · port · ips 튜플 · version · lanes int) | `test_found_assembles_name_host_port_ips_version_and_lanes` | §3 |
| 37 | name 은 TXT `name=`(대소문자 보존), 없으면 PTR 대상의 라벨 | `test_found_name_comes_from_txt_name_before_the_instance_label` | §1 |
| 38 | A 없음 → `ips == ()` | `test_found_without_a_records_has_empty_ips` | §3 |
| 39 | TXT 없음 → `version == ""` · `lanes is None` | `test_found_without_txt_has_empty_version_and_no_lanes` | §3 |
| 40 | `v=` 는 첫 `=` 뒤 전부 · `lanes=` 는 int(`0` 포함) 아니면 None · 모르는 키 무시 (×8) | `test_found_reads_version_and_lanes_from_txt` | §1 · 결정 34 |
| 41 | `=` 없는 TXT 문자열은 무시 | `test_found_ignores_txt_strings_without_an_equals_sign` | §3 |
| 42 | PTR 만 · SRV/TXT/A 만 · 빈 목록 → `[]` | `test_found_needs_both_ptr_and_srv` | §3 |
| 43 | 다른 서비스(`_http._tcp`)의 PTR 은 무시 | `test_found_ignores_ptr_for_another_service` | §3 |
| 44 | 다른 호스트의 A · 모르는 타입 · AAAA 는 무시 | `test_found_ignores_a_records_for_other_hosts_and_unknown_types` | §3 · 결정 35 |
| 45 | 같은 응답 세 번 · 같은 IP 두 번 → 하나 | `test_found_collapses_duplicate_responses_and_duplicate_ips` | §3 중복 제거 |
| 46 | 인스턴스 둘 → 첫 PTR 순서 | `test_found_lists_instances_in_first_ptr_order` | §3 |
| 47 | A 이름 ↔ SRV target · PTR 대상 ↔ SRV/TXT 소유자 대소문자 무시(정규화 안 된 손 레코드) | `test_found_matches_names_case_insensitively` | RFC 6762 §16 |
| 48 | **PTR TTL 0(goodbye) 은 발견이 아니다** — `encode_goodbye` 출력도 `[]` | `test_found_skips_a_ptr_with_ttl_zero` | §2 goodbye (의도적 빨강 ①) |
| 49 | goodbye: QR=1 · AA=1 · PTR 먼저 · SRV 포함 · 모든 TTL 0 | `test_goodbye_is_an_answer_whose_records_all_have_ttl_zero` | §2 |
| 50 | goodbye 는 512 안 · 살아 있는 응답과 다르다 | `test_goodbye_fits_in_a_packet_and_is_not_the_live_response` | §2 |
| 51 | 같은 초 20개 허용 · 21번째 거부 · 0.999초까지 거부 · 1.0초 허용 | `test_rate_limiter_allows_max_per_second_then_refuses_until_the_next_second` | §2 상한 |
| 52 | 상한 1 → 초당 하나 | `test_rate_limiter_with_max_one_is_one_per_second` | §2 |
| 53 | 기본값 20 (`MAX_ANSWERS_PER_SECOND`) | `test_rate_limiter_default_is_twenty_per_second` | §2 |
| 54 | 초당 10개 흐름은 100개 내내 허용 | `test_rate_limiter_spreads_a_slow_trickle_without_refusing` | §2 |
| 55 | `Responder(instance, host, port, *, …, mdns_port=5353, sock_factory=None)` · `discover(timeout=1.5, *, …, mdns_port=5353)` 모양 | `test_socket_api_shape_and_mdns_port_default` | §5 표(15353) |
| 56 | PTR 질의 → `encode_response(…, ips_fn(), txt_fn())` 바이트 그대로 | `test_handle_answers_a_ptr_query_with_the_full_response` | §2 |
| 57 | 인스턴스 SRV/TXT · 호스트 A · 서비스 ANY 질의도 같은 응답 (×4) | `test_handle_answers_instance_and_host_questions_with_the_same_response` | §2 |
| 58 | 우리 응답 · goodbye · 다른 서비스/인스턴스 · 빈 헤더 · 빈 바이트 · 쓰레기 · 513 바이트 · 포인터 루프 → None, 예외 없음 (×9) | `test_handle_ignores_responses_unrelated_questions_and_garbage` | §2 「그 외는 무시」 |
| 59 | 5353 아닌 포트에서 온 질의(legacy unicast)에는 질의 id 를 되돌려 주고, 5353 에서 온 것은 id 0 | `test_handle_copies_the_query_id_only_for_legacy_unicast_queries` | RFC 6762 §6.7 |
| 60 | `ips_fn`/`txt_fn` 은 응답마다 다시 부른다(lanes 바뀜 반영) | `test_handle_reads_ips_and_txt_on_every_call` | §1 |
| 61 | 같은 초 20개 답하고 21번째 None · 다음 초 다시 답 | `test_handle_rate_limits_to_twenty_answers_per_second` | §2 상한 |
| 62 | 무시한 패킷은 상한을 소모하지 않는다 | `test_handle_ignored_packets_do_not_consume_the_answer_budget` | §2 |
| 63 | 응답을 못 만들면(IP 40개 → 512 초과) None + 로그 한 줄 | `test_handle_returns_none_instead_of_raising_when_the_response_cannot_be_encoded` | §2 실패 처리 |
| 64 | 소켓을 못 열면 `start()` False · `error` · 로그 한 줄(`advertise off`) · `stop()` 두 번 조용 | `test_start_without_a_socket_is_a_logged_no_and_stop_is_quiet` | §2 실패 처리 |
| 65 | `local_ipv4s()` 는 127.x 를 절대 넣지 않는다(중복 없음, 네트워크 없으면 빈 목록) | `test_local_ipv4s_never_lists_loopback` | §1 A 레코드 |
| 66 | 질의 = `encode_query([(SERVICE, PTR)])` 를 `(224.0.0.251, 5353)` 로(재전송은 같은 패킷 ≤ 3회) · 응답 → Found · 소켓 닫힘 | `test_discover_sends_the_ptr_query_to_the_group_and_collects_the_response` | §3 |
| 67 | `mdns_port` 를 바꾸면 그 포트로 보낸다 | `test_discover_honours_mdns_port` | §5 표(15353) |
| 68 | 자기 질의 사본 · 쓰레기 · 빈 바이트 · 600 바이트 · 포인터 루프 · 중복 · 다른 서비스 → 하나만 | `test_discover_ignores_queries_garbage_oversize_other_services_and_duplicates` | §3 |
| 69 | 두 서버 → 이름순 | `test_discover_sorts_instances_by_name` | §3 |
| 70 | 같은 서버의 응답 중 A 가 있는 쪽을 남긴다(순서 무관) | `test_discover_keeps_the_copy_that_has_ips_when_responses_disagree` | §3 |
| 71 | 소켓 생성 실패 · 송신 실패(No route) → `[]`, 소켓 닫힘 | `test_discover_returns_empty_when_no_socket_or_no_route` | §2 「부가 기능」 |
| 72 | 패킷 없이 데드라인까지 → `[]` | `test_discover_stops_at_the_deadline_without_packets` | §3 timeout |

## `tests/test_mdns_loopback.py` — 진짜 소켓, 포트 15353, 루프백

소켓 세 종류: `member_socket()`(0.0.0.0:15353 · `SO_REUSEADDR`+`SO_REUSEPORT` · 그룹 가입 on 127.0.0.1 ·
`IP_MULTICAST_IF`=127.0.0.1 · LOOP 1) — 응답기·멀티캐스트 질의기·감시용 · `ephemeral_socket()`(127.0.0.1:0,
그룹 미가입 — 실제 `rcm` 클라이언트 모양, 답은 unicast 로만 온다). 이 Mac 에서 확인: 한 프로세스의 소켓 셋이
같은 포트를 나눠 듣고 셋 다 멀티캐스트 사본을 받는다. 결정성: 질의기 소켓을 `start()` **전에** 만들어 넘기면
첫 announce 부터 그 소켓에 쌓인다(응답기가 announce 사이에 귀를 닫아도 1·3 은 초록).

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 응답기 하나 → 그룹에 가입한 질의기가 이름·포트·A 의 IP 둘·버전·lanes 를 그대로 받는다(멀티캐스트 경로) | `test_one_responder_is_found_by_a_multicast_querier` | §3 · 완료 ② |
| 2 | 같은 것을 임의 포트 질의기(그룹 미가입)가 **start 직후** 물어도 unicast 답으로 받는다 | `test_one_responder_is_found_by_an_ephemeral_port_querier` | §2 · RFC 6762 §6.7 (의도적 빨강 ②) |
| 3 | 이름이 다른 응답기 둘 → 둘 다, 이름순(buildbox · macmini) | `test_two_responders_with_different_names_are_both_found` | §5 표 |
| 4 | 응답기 없음 → 0.5초 타임아웃 안에 `[]`(1.5초 상한) | `test_no_responder_yields_an_empty_list_within_the_timeout` | 완료 ③ |
| 5 | 15353 에서 그룹으로 보낸 PTR 질의 3개 → 그룹으로 응답 ≥ 3개(announce 는 최대 2개) | `test_responder_answers_hand_sent_ptr_queries_by_multicast` | §2 (의도적 빨강 ②) |
| 6 | `start()` True 를 0.5초 안에 · announce(TTL>0) 감지 · `stop()` 1.5초 안 · goodbye(TTL 0) 감지 · `stop()` 두 번 | `test_start_announces_and_stop_says_goodbye_promptly` | §2 announce/goodbye |

## 의도적 빨강 (구현이 규칙에 어긋나는 곳 — 오너/구현 결정 대기)

① **PTR TTL 0 이 발견으로 잡힌다** (`test_found_skips_a_ptr_with_ttl_zero`). `found_from_records` 가 TTL 을 보지
않아 질의 창 안에 떠나는 서버의 goodbye 가 목록에 오른다. 고치기: PTR 수집 때 `r.ttl == 0` 이면 건너뛴다(두 줄).

② **응답기가 start 뒤 ~2초 동안 귀를 닫는다** (`…ephemeral_port_querier` · `…hand_sent_ptr_queries…`).
`Responder._loop` 이 announce 2회 사이를 `self._stop.wait(1.0)` 으로 **막고** 있어 그동안 온 질의는 announce 단계가
끝난 뒤에야 읽힌다. 서버를 띄우자마자 노트북이 `rcm check` 를 치면 1.5초 창 안에 답이 없다. 프로브로 확인:
announce 단계가 끝난 뒤 같은 임의 포트 질의기는 찾는다(unicast 경로 자체는 맞다). 고치기: announce 를 recv 루프
안에서 `next_announce` 시각으로 예약(recv 타임아웃 0.2초 유지) — 루프가 끊기지 않는다.

## 구현에 맞춘 것(임의 이름 · 규칙 아님) 과 명세와의 차이

- 상수 `MAX_ANSWERS_PER_SECOND`(과제 문구는 `MAX_RESPONSES_PER_SECOND`) · SRV 데이터는 튜플 · `Record` 에 cache-flush
  필드 없음(와이어 클래스 필드로 잠금) · `host_name` 헬퍼 없음(테스트 안 헬퍼) · 디코더가 소문자 정규화(대소문자 보존
  대신) · `Found.host` 는 끝 `.` 포함(`cli.py` 가 `rstrip(".")`) · `RateLimiter` 는 `allow(now)` 만(시계 주입 없음).
- 질의기는 그룹에 가입하지 않고 임의 포트에서 묻고(§3 「임의 포트」), 응답기가 legacy unicast 로 답한다(RFC 6762 §6.7).
  노트북이 5353 을 잡지 않아도 된다 — 과제 문구의 「질의기도 그룹 가입」 대신 이 경로를 잠갔다(루프백 2번).
- §3 「A 가 없으면 `getaddrinfo("<host>.local")` 시도」는 구현에 없다. 대신 `Found.address` 가 `.local` 이름으로
  떨어져 OS 해석기(macOS mDNSResponder · Linux nss-mdns)에 맡긴다. 우리 응답기는 `local_ipv4s()` 가 비지 않는 한
  A 를 항상 싣는다. 명세 문구를 바꾸든 구현을 보태든 결정이 필요하다(테스트는 `address` 폴백만 잠갔다).
- `port` 범위 검증(`encode_response(port=70000)` → `struct.error`)은 잠그지 않았다 — 포트는 설정 검증을 거친 int 다.

범위 밖(v1): goodbye 뒤 목록에서 사라짐 · IPv6(결정 35) · `_services._dns-sd._udp.local.` 메타 질의 · 이름 압축 인코딩.

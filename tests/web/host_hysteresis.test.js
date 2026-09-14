"use strict";
// 호스트 판정의 이력(히스테리시스) — 「바쁨」은 85 에서 켜지고 **80 아래로 내려와야** 꺼진다.
//
// 왜: 빌드 머신의 CPU 는 일하는 동안 85 를 계속 스친다. 경계가 하나뿐이면 판정이 표본마다
// 뒤집히고, 그 판정이 요약 글자와 호스트 절의 열림 상태를 정하므로 **화면이 읽는 사람 발밑에서
// 움직였다**(2026-09-14 운영 인스턴스 실측: 150초에 3번 뒤집힘 · 페이지 높이 315px · CLS 0.245).
// 푸는 값 80 은 새 숫자가 아니다 — M5f 의 `cpu_max_percent` 기본값과 같은 「이 아래면 여유」다.
//
// 잠그는 것:
// - 직전 판정을 안 주면 오늘 규칙 그대로(85)다 — 첫 렌더도 옛 호출부도 안 바뀐다.
// - 직전이 `"busy"` 면 기준이 80 으로 내려간다: 80~84 는 바쁨을 유지하고 79 에서 여유로 돌아온다.
// - 이력이 `partial`·`unknown` 을 `busy` 로 바꾸지 않는다 — 모르는 값은 여전히 모르는 값이다.
// - 디스크 바닥(남은 10 GiB 미만)은 이력과 무관하게 `busy` 다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");

const { load, fixture } = require("./helpers");

const rcm = load();
const GiB = 1024 * 1024 * 1024;

// 픽스처의 진짜 표본(cpu 21 · mem 58 · gpu 13 · disk 29)에 CPU 만 얹는다.
function host(cpuBusy) {
  const h = fixture("main").pools[0].hosts[0];
  if (cpuBusy !== undefined) h.cpu.busy = cpuBusy;
  return h;
}

describe("hostPressure — 직전 판정을 안 주면 오늘 그대로", () => {
  test("85 이상이면 바쁨, 84 면 여유", () => {
    assert.equal(rcm.hostPressure(host(85)).verdict, "busy");
    assert.equal(rcm.hostPressure(host(84)).verdict, "fine");
    assert.equal(rcm.hostPressure(host(80)).verdict, "fine");
  });

  test("모르는 직전 값은 없는 것과 같다", () => {
    assert.equal(rcm.hostPressure(host(82), "nonsense").verdict, "fine");
    assert.equal(rcm.hostPressure(host(82), null).verdict, "fine");
    assert.equal(rcm.hostPressure(host(82), undefined).verdict, "fine");
  });
});

describe("hostPressure — 한 번 바쁨이면 80 아래로 내려와야 여유다", () => {
  test("80~84 는 바쁨을 유지한다", () => {
    for (const cpu of [84, 82, 80]) {
      assert.equal(rcm.hostPressure(host(cpu), "busy").verdict, "busy", `cpu ${cpu}`);
    }
  });

  test("79 에서 여유로 돌아온다", () => {
    assert.equal(rcm.hostPressure(host(79), "busy").verdict, "fine");
  });

  test("여유였으면 85 를 넘어야 바쁨이 된다 — 푸는 값으로 켜지지 않는다", () => {
    assert.equal(rcm.hostPressure(host(84), "fine").verdict, "fine");
    assert.equal(rcm.hostPressure(host(82), "fine").verdict, "fine");
    assert.equal(rcm.hostPressure(host(85), "fine").verdict, "busy");
  });

  test("판정은 스스로 안정하다 — 같은 표본을 다시 먹여도 안 바뀐다", () => {
    const h = host(82);
    const once = rcm.hostPressure(h, "busy").verdict;
    assert.equal(rcm.hostPressure(h, once).verdict, once);
  });
});

describe("hostPressure — 이력이 「모른다」를 「바쁘다」로 바꾸지 않는다", () => {
  test("값이 하나라도 없으면 partial 이다(직전이 바쁨이어도)", () => {
    const h = host(70);
    h.gpu = null;
    assert.equal(rcm.hostPressure(h, "busy").verdict, "partial");
  });

  test("아는 값이 하나도 없으면 unknown 이다", () => {
    const h = host();
    h.cpu = null;
    h.memory = null;
    h.gpu = null;
    delete h.disk;
    assert.equal(rcm.hostPressure(h, "busy").verdict, "unknown");
  });

  test("직전이 바쁨이면 80 위의 값 하나로 바쁨이 유지된다 — 나머지가 없어도", () => {
    const h = host(81);
    h.gpu = null;
    assert.equal(rcm.hostPressure(h, "busy").verdict, "busy");
  });

  test("디스크 바닥은 이력과 무관하다", () => {
    const h = host(10);
    h.disk = { used_bytes: 90 * GiB, free_bytes: 5 * GiB, total_bytes: 100 * GiB, path: "/d" };
    assert.equal(rcm.hostPressure(h, "fine").verdict, "busy");
    assert.equal(rcm.hostPressure(h, undefined).verdict, "busy");
  });
});

describe("hostPressure — 실측 표본을 그대로 먹인다", () => {
  // 2026-09-14 운영 인스턴스에서 5초 간격으로 읽은 CPU 열 개(.verify/evidence/2026-09-14-verdict-150s.log).
  // 경계가 하나면 판정이 여덟 번 뒤집히고, 이력을 주면 네 번이다. 「바쁨」 글자가 5초마다
  // 깜빡이지 않는다는 뜻이지 **움직임이 0** 이라는 뜻이 아니다 — 그건 펼침 규칙이 맡는다.
  const MEASURED = [81, 90, 78, 88, 83, 92, 84, 86, 82, 79];

  function verdicts(withHistory) {
    let prev;
    return MEASURED.map((cpu) => {
      const v = rcm.hostPressure(host(cpu), withHistory ? prev : undefined).verdict;
      prev = v;
      return v;
    });
  }

  function flips(list) {
    return list.filter((v, i) => i > 0 && v !== list[i - 1]).length;
  }

  test("경계가 하나면 여덟 번 뒤집힌다", () => {
    assert.equal(flips(verdicts(false)), 8);
  });

  test("이력을 주면 네 번이다 — 80~84 구간에서 더는 안 움직인다", () => {
    assert.deepEqual(verdicts(true), [
      "fine", "busy", "fine", "busy", "busy", "busy", "busy", "busy", "busy", "fine",
    ]);
    assert.equal(flips(verdicts(true)), 4);
  });
});

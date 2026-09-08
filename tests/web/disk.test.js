"use strict";
// 디스크 — 호스트 카드와 요약 띠 (§4.6-가, 오너 피드백 「전체중에 얼마」).
//
// 잠그는 것:
// - `hostPressure` 가 `disk`(사용률 정수 퍼센트)와 `diskFree`(남은 바이트)를 함께 돌려준다.
//   두 값이 다 필요하다 — 막대만으로는 「얼마 남았나」를 못 읽는다.
// - 판정에 디스크가 들어간다. 꽉 찬 디스크는 바쁜 CPU 와 같은 급의 사실이다.
// - **경고 기준이 두 개**다: 사용률 85% 이상, 또는 남은 공간 10 GiB 미만. 큰 디스크는 1% 만 써도
//   남은 게 5 GiB 면 빌드가 안 돌고, 작은 디스크는 80% 에서 이미 안 돈다 — 하나만 보면 틀린다.
// - `disk` 를 안 보내는 옛 워커는 `null`·`null` 이고, 나머지 셋을 다 알면 판정은 `"partial"`.
// - 새 문자열 키가 두 언어에 다 있다. 키 **이름**은 `host.disk`·`host.disk_free` 두 개만 명세가
//   정한다 — 나머지(요약 띠)는 `i18n.test.js` 의 「키 집합이 같다」·「모든 키가 두 언어에서 비어
//   있지 않은 문자열을 만든다」가 자동으로 덮는다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { load, fixture } = require("./helpers");
const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));

const rcm = load();
const LANGS = I18N.LANGS;
const GiB = 1024 * 1024 * 1024;

// 픽스처의 진짜 표본 모양(cpu 21 · mem 58 · gpu 13)을 쓰되 `disk` 는 **시험마다 명시**한다.
// 픽스처에 `disk` 가 생기든 말든 각 시험의 뜻이 흔들리지 않게.
function host(disk) {
  const h = fixture("main").pools[0].hosts[0];
  if (disk === undefined) delete h.disk;
  else h.disk = disk;
  return h;
}

// 서버·워커가 보내는 모양: {used_bytes, free_bytes, total_bytes, path}. GiB 단위로 받는다.
function disk(usedGiB, totalGiB, freeGiB) {
  return {
    used_bytes: usedGiB * GiB,
    free_bytes: freeGiB * GiB,
    total_bytes: totalGiB * GiB,
    path: "/var/rcm/data",
  };
}

describe("hostPressure — 디스크 칸", () => {
  test("사용률은 used/total 의 정수 퍼센트, diskFree 는 남은 바이트 그대로", () => {
    // 목업의 예: 「디스크 120 GB / 460 GB」 · 「26% · 340 GB 남음」
    const r = rcm.hostPressure(host(disk(120, 460, 340)));
    assert.equal(r.disk, 26, "120/460 = 26.08% → 26");
    assert.equal(r.diskFree, 340 * GiB, "남은 양은 바이트 원값 — 포맷은 그리는 층의 몫");
    assert.equal(r.verdict, "fine");
    // 기존 세 칸은 그대로다
    assert.equal(r.cpu, 21);
    assert.equal(r.mem, 58);
    assert.equal(r.gpu, 13);
  });

  test("표본이 없으면 disk 칸도 null 이다 — 화면에 undefined 를 그리지 않는다", () => {
    [null, undefined].forEach((h) => {
      const r = rcm.hostPressure(h);
      assert.equal(r.verdict, "no_sample");
      assert.equal(r.disk, null);
      assert.equal(r.diskFree, null);
    });
    const empty = rcm.hostPressure(fixture("empty").pools[0].hosts[0]);
    assert.equal(empty.verdict, "no_sample");
    assert.equal(empty.disk, null);
    assert.equal(empty.diskFree, null);
  });
});

describe("hostPressure — 기준 하나: 사용률 85%", () => {
  test("디스크가 85% 이상이면 나머지가 한가해도 busy", () => {
    const r = rcm.hostPressure(host(disk(85, 100, 15)));
    assert.equal(r.disk, 85);
    assert.equal(r.verdict, "busy", "85 는 포함이다");

    assert.equal(rcm.hostPressure(host(disk(91, 100, 9))).verdict, "busy");
  });

  test("84% 는 아직 fine — 넷 다 알고 넷 다 85 미만", () => {
    const r = rcm.hostPressure(host(disk(84, 100, 16)));
    assert.equal(r.disk, 84);
    assert.equal(r.verdict, "fine");
  });

  test("cpu·mem·gpu 가 85 이상이면 디스크가 한가해도 busy (기존 규칙 유지)", () => {
    const h = host(disk(10, 100, 90));
    h.cpu.busy = 90;
    assert.equal(rcm.hostPressure(h).verdict, "busy");
  });
});

describe("hostPressure — 기준 둘: 남은 공간 10 GiB", () => {
  test("큰 디스크: 2 TiB 를 1% 만 썼어도 남은 게 5 GiB 면 busy", () => {
    // 사용률만 보면 「1%, 여유롭다」로 읽힌다. 실제로는 빌드가 안 돈다.
    const r = rcm.hostPressure(host(disk(21, 2048, 5)));
    assert.equal(r.disk, 1, "21/2048 = 1.02% → 1");
    assert.equal(r.diskFree, 5 * GiB);
    assert.equal(r.verdict, "busy", "남은 공간 기준이 사용률 기준과 따로 있어야 한다");
  });

  test("작은 디스크: 80% 라도 남은 게 8 GiB 면 busy", () => {
    const r = rcm.hostPressure(host(disk(32, 40, 8)));
    assert.equal(r.disk, 80);
    assert.equal(r.verdict, "busy");
  });

  test("경계는 10 GiB — 딱 10 GiB 남으면 낮은 게 아니다", () => {
    const ok = rcm.hostPressure(host(disk(50, 100, 10)));
    assert.equal(ok.diskFree, 10 * GiB);
    assert.equal(ok.verdict, "fine", "10 GiB 는 「미만」이 아니다");

    const low = host(disk(50, 100, 10));
    low.disk.free_bytes = 10 * GiB - 1;
    assert.equal(rcm.hostPressure(low).verdict, "busy");
  });

  test("남은 공간이 모자라면 다른 값을 하나도 몰라도 busy", () => {
    const h = host(disk(5, 100, 5));
    h.cpu = null;
    h.memory = null;
    h.gpu = null;
    const r = rcm.hostPressure(h);
    assert.equal(r.disk, 5);
    assert.equal(r.verdict, "busy", "busy 는 partial·unknown 을 이긴다");
  });
});

describe("hostPressure — 모르는 값", () => {
  test("disk 를 안 보내는 옛 워커 → null·null, 나머지 셋을 알면 partial", () => {
    [undefined, null].forEach((d) => {
      const r = rcm.hostPressure(host(d));
      assert.equal(r.disk, null);
      assert.equal(r.diskFree, null);
      assert.equal(r.verdict, "partial", "디스크를 모르는데 fine 이라고 말하면 거짓말이다");
      assert.equal(r.cpu, 21);
      assert.equal(r.mem, 58);
      assert.equal(r.gpu, 13);
    });
  });

  test("넷 다 모르면 unknown", () => {
    const h = host(null);
    h.cpu = null;
    h.memory = null;
    h.gpu = null;
    const r = rcm.hostPressure(h);
    assert.equal(r.verdict, "unknown");
    assert.equal(r.cpu, null);
    assert.equal(r.mem, null);
    assert.equal(r.gpu, null);
    assert.equal(r.disk, null);
    assert.equal(r.diskFree, null);
  });

  test("디스크만 알면 unknown 이 아니라 partial", () => {
    const h = host(disk(50, 100, 50));
    h.cpu = null;
    h.memory = null;
    h.gpu = null;
    const r = rcm.hostPressure(h);
    assert.equal(r.disk, 50);
    assert.equal(r.verdict, "partial");
  });

  test("디스크가 85 이상이면 다른 값을 몰라도 busy — busy 가 partial 을 이긴다", () => {
    const h = host(disk(90, 100, 10));
    h.gpu = null;
    assert.equal(rcm.hostPressure(h).verdict, "busy");
  });

  test("부실한 disk 값에 던지지 않는다 — 워커 버전이 어긋나도 호스트 칸은 산다", () => {
    const bad = [
      {},
      { path: "/var/rcm/data" },
      { used_bytes: 5 * GiB },
      { total_bytes: 100 * GiB },
      { used_bytes: 5 * GiB, total_bytes: 0, free_bytes: null },
      { used_bytes: null, total_bytes: null, free_bytes: null },
      { used_bytes: "5", total_bytes: "100", free_bytes: "50" },
      "nonsense",
      42,
      [],
    ];
    const verdicts = ["fine", "partial", "busy", "unknown", "no_sample"];
    bad.forEach((d) => {
      const label = JSON.stringify(d);
      let r;
      assert.doesNotThrow(() => { r = rcm.hostPressure(host(d)); }, label);
      assert.ok(r.disk === null || Number.isInteger(r.disk), label + ": disk 는 정수 아니면 null");
      assert.ok(r.diskFree === null || typeof r.diskFree === "number", label + ": diskFree 는 수 아니면 null");
      assert.ok(verdicts.includes(r.verdict), label + ": " + r.verdict);
    });
  });
});

describe("디스크 문자열", () => {
  test("host.disk · host.disk_free 가 두 언어에 다 있다", () => {
    // 이름을 명세가 정하는 키는 이 둘뿐이다. 요약 띠 키는 구현자가 정하고,
    // i18n.test.js 의 「키 집합이 같다」가 그것까지 자동으로 덮는다.
    assert.ok(I18N.has("host.disk"), "카탈로그에 host.disk 가 없다");
    assert.ok(I18N.has("host.disk_free"), "카탈로그에 host.disk_free 가 없다");
  });

  test("host.disk 는 {used, total} 을, host.disk_free 는 {free} 를 실제로 쓴다", () => {
    LANGS.forEach((lang) => {
      const line = I18N.t(lang, "host.disk", { used: "120 GB", total: "460 GB" });
      assert.match(line, /120 GB/, lang + ": 쓴 양이 사라졌다");
      assert.match(line, /460 GB/, lang + ": 전체가 사라졌다 — 「전체중에 얼마」가 요구다");

      const free = I18N.t(lang, "host.disk_free", { free: "340 GB" });
      assert.match(free, /340 GB/, lang + ": 남은 양이 사라졌다");
    });
  });
});

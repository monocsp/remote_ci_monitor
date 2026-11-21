"""웹 UI 스토어 탭(영역 C) — 극단값에서도 표·목록·카드가 제 상자 안에 있다. Chrome 이 없으면 skip.

`test_web_browser` 의 스토어 스텁(`RELEASE_STUB_JS` · `DRIVER_STUB_JS` · `SHEET_STUB_JS`) 위에
`EDGE_STUB_JS` 를 덧씌워 **가장 긴 값**을 준다: 80자 넘는 저장소·빌드 이름, `10.123.4567-beta.1+
build.99999` 같은 버전, 드래프트 20개 · 제출된 버전 20개, 상한에 딱 맞거나 한참 넘는 문안(4000자
설명 · 끊김 없는 URL · 긴 한국어), 긴 줄의 릴리스 노트, 스크린샷 30장, 300자 커밋 메시지와 긴
브랜치·태그 이름, 이름이 긴 비밀과 긴 검증 오류, 300자 드라이버 상태 줄, 「심사 전에 남은 것」 15개
넘는 바텀시트.

화면마다(관문 · 버전 목록 · 상태 · 버전 페이지 — 시트 펼침/접힘) 390px · 1240px × 한국어 · 영어에서
잰다:

1. **페이지가 옆으로 밀리지 않는다**(`test_web_layout.OVERFLOW_JS` 와 같은 계산).
2. **글자가 제 상자 밖으로 새지 않는다** — `overflow: visible` 인 상자의 `scrollWidth` 가
   `clientWidth` 를 넘으면 그 안의 무엇인가가 테두리 밖으로 그려진 것이다. 자기 안에서 스크롤하거나
   자르는 상자(`overflow-x` ≠ visible)는 담고 있으므로 뺀다.
3. **나란히 선 것끼리 겹치지 않는다** — flex · grid 상자의 자식 상자들이 서로 덮지 않는다.
4. **잘린 글은 전문을 `title` 에 둔다** — 말줄임(`text-overflow: ellipsis`)이나 줄 수 제한으로
   실제로 잘린 요소는 자기나 조상의 `title` 에 그 글 전체가 있다.
5. **폰에서 입력칸이 쓸 만하다** — 390px 에서 문안 칸은 폭 240px 이상, 글자 16px 이상(iOS 는 16px
   밑의 칸에 포커스하면 화면을 확대해 페이지가 옆으로 밀린다).
6. **표는 자기 상자 안에서 옆으로 스크롤하지 않는다**(오른쪽 열의 드롭존이 가려진다) ·
   **짧은 낱말이 글자마다 꺾이지 않는다**(«파/일» · «소/스») ·
   **한 줄이어야 할 것은 한 줄이다**(커밋 · 요약 띠 칩 · 드라이버 머리 · 시트의 한 줄;
   검증 오류는 세 줄) · **못 받은 스크린샷이 칸을 지킨다**(404 그림을 Chrome 은 alt 글로
   그리고, 인라인이면 경로 길이만큼 옆 그림 위로 번진다).

그리고 데이터가 바뀌어도 자리가 **튀지 않는다**: 치는 동안의 글자 수 카운터, 자동 저장 배지(저장하는
중 → 자동 저장 · 12s 전 → 저장 실패 + 다시 저장), 드라이버 상태 줄, 바텀시트의 접힌 한 줄.

`RCM_UI_SHOTS=<폴더>` 와 `RCM_UI_SHOTS_TAG=before|after` 를 주면 장면마다
`<scene>-<width>-<lang>-<tag>.png` 를 그 폴더에 남긴다(오너 검토용 — 시험의 판정과는 무관하다).
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from test_server import Server
from test_web_browser import (  # noqa: F401  (pytestmark: Chrome 이 없으면 skip)
    DRIVER_STUB_JS,
    RELEASE_STUB_JS,
    SHEET_STUB_JS,
    Chrome,
    _q,
    pytestmark,
)
from test_web_layout import OVERFLOW_JS, describe

CASES = ((390, "ko"), (390, "en"), (1240, "ko"), (1240, "en"))
PHONE_MAX = 720

# ── 극단값 ────────────────────────────────────────────────────────────────────

REPO = "dolomood-app-renew-store-release-pipeline-with-a-deliberately-long-repository-name"
assert len(REPO) >= 80
VER = "10.123.4567-beta.1+build.99999"
LONG_BUILD = VER + "-release-candidate-for-the-spring-campaign-2026-and-a-long-suffix"
assert len(LONG_BUILD) >= 80
BRANCH = "release/" + VER + "-spring-campaign-hotfix-branch-whose-name-keeps-going-and-going"
KO_SENTENCE = "오늘의 기분을 짧게 적고 사진을 붙이면 한 주가 어떻게 흘렀는지 한눈에 보입니다. "
URL = "https://example.invalid/support/" + "very-long-path-segment-" * 12 + "?ref=" + "x" * 80
UNBROKEN = "journal,mood,notes," + "keyword" * 40  # 쉼표 뒤에 끊을 자리가 없는 한 덩어리
LONG_LINE = "• " + "사진 문의 개선 " * 10 + "fixes-" * 30  # 릴리스 노트의 긴 한 줄
COMMIT = (
    "feat(store): 버전 페이지의 문안 칸을 이전 버전 값으로 채우고 자동 저장을 붙인다 — "
    + "release-notes-and-listing-autosave-" * 6
    + " with a deliberately long English tail that keeps going past three hundred characters"
)
assert len(COMMIT) >= 300
DRIVER_STATUS = (
    f"release {LONG_BUILD}: stage S5 scenario QA · job #643 · "
    + "waiting-for-simulator-boot-and-the-device-farm-lease-" * 4
)
assert len(DRIVER_STATUS) >= 300
SECRET_FILE = "google-play-service-account-for-dolomood-app-renew-production-uploads-2026.json"
SECRET_VALUE = "APP_STORE_CONNECT_API_KEY_ISSUER_ID_FOR_THE_PRODUCTION_TEAM_ACCOUNT_2026_SPRING"
VERIFY_ERROR = (
    "401 Unauthorized from https://api.appstoreconnect.apple.com/v1/apps?filter[bundleId]="
    + "com.example.dolomood.renew.production." * 4
    + " — the key was revoked or the issuer id does not match the team that owns the app"
)
assert len(VERIFY_ERROR) >= 200

EDGE = {
    "repo": REPO,
    "ver": VER,
    "longBuild": LONG_BUILD,
    "branch": BRANCH,
    "ko": KO_SENTENCE,
    "url": URL,
    "unbroken": UNBROKEN,
    "longLine": LONG_LINE,
    "commit": COMMIT,
    "driverStatus": DRIVER_STATUS,
    "secretFile": SECRET_FILE,
    "secretValue": SECRET_VALUE,
    "verifyError": VERIFY_ERROR,
}

# RELEASE_STUB_JS · DRIVER_STUB_JS · SHEET_STUB_JS 다음에 심는다 — 마지막에 심은 것이 이긴다.
# `?gate=1` 이면 관문이 닫힌 저장소(비밀 셋이 빠지고 검증 오류가 길다), 아니면 열린 저장소.
EDGE_STUB_JS = (
    "(() => {\n  const E = "
    + json.dumps(EDGE, ensure_ascii=False)
    + ";\n"
    + r"""
  const gate = /[?&]gate=1(&|$)/.test(location.search);
  const ago = (s) => new Date(Date.now() - s * 1000).toISOString();
  const sha = (p) => p + "0".repeat(40 - p.length);
  const ok = (body, status) => Promise.resolve({ ok: true, status: status || 200, body });
  const api = window.rcmStoreApi;
  const setup = gate
    ? { required: 6, present: 2, verified: 1, complete: false,
        missing: [E.secretFile, E.secretValue, "review_information/" + E.secretFile] }
    : { required: 6, present: 6, verified: 6, complete: true, missing: [] };
  const at = ago(3600);
  const secrets = { dir_env: "DOLOMOOD_APP_RENEW_RELEASE_SECRETS_DIRECTORY_FOR_THE_BUILD_MACHINE",
    items: [
      { name: "AuthKey_ABCDEFGHIJ_" + "0123456789".repeat(4) + ".p8", kind: "file", optional: false,
        verify: "asc", present: true, size: 2112, fingerprint: "9f1c2a3b" + "c".repeat(56),
        verified_at: gate ? null : at, verify_error: gate ? E.verifyError : null, max_kb: 64 },
      { name: E.secretValue, kind: "value", optional: false, verify: "github", present: !gate,
        size: null, fingerprint: gate ? null : "ghp_…", verified_at: gate ? null : at,
        verify_error: gate ? E.verifyError : null },
      { name: E.secretFile, kind: "file", optional: true, verify: "play", present: !gate,
        size: gate ? null : 2400, fingerprint: gate ? null : "aabbccdd",
        verified_at: gate ? null : at,
        verify_error: null, max_kb: 64 },
      { name: "review_information", kind: "dir", optional: false, verify: "none", present: !gate,
        fingerprint: gate ? "1/3 files" : "3/3 files", verified_at: null, verify_error: null,
        files: [
          { name: "demo_user.txt", present: true, size: 12, fingerprint: "aabbccdd" },
          { name: E.secretFile, present: !gate, size: gate ? null : 9,
            fingerprint: gate ? null : "eeff0011" },
          { name: "notes-for-the-app-review-team-in-korean-and-english-" + "x".repeat(40) + ".txt",
            present: !gate, size: gate ? null : 900, fingerprint: gate ? null : "01234567" }] },
    ] };
  const presets = { plan: "release-plan", upload: "release-upload", review: "release-review",
    gate: null, qa: null, dev: null };
  const doc = { name: E.repo, url: "git@example.invalid:" + E.repo + ".git",
    profile: { default_branch: E.branch, tag: "prod/{version}-{build}-" + "suffix".repeat(8),
      build_number_policy: "auto", plan_max_age_minutes: 30, driver: ["scripts/release/driver.sh"],
      listing: { preview: ["x"], diff: ["y"], validate: ["z"] },
      secrets_dir_env: secrets.dir_env, presets },
    setup,
    mirror: { path: "/Users/build/.local/share/rcm/mirrors/" + E.repo + "/" + "nested/".repeat(6),
      age_seconds: 200, fetched_at: ago(200) },
    branches: { main: sha("9e1c4d2f"), dev: sha("7a03b9f0"), main_in_dev: true } };
  // 버전 드래프트 20 · 제출된 버전 20
  const states = ["editing", "editing", "creating", "running", "failed"];
  const drafts = [];
  for (let i = 0; i < 20; i++) {
    const id = i === 0 ? 7 : 100 + i;
    const st = i === 0 ? "editing" : states[i % states.length];
    const ios = i % 3 === 0 ? E.ver : i % 3 === 1 ? E.longBuild : "10." + i + ".0";
    // 버전 페이지(#7)는 두 스토어 이름이 달라 제목이 «iOS … · Android <80자>» 가 된다
    const android = i % 3 === 2 || i === 0 ? E.longBuild : ios;
    drafts.push(window.rcmDraftRow(id, ios, android, st, {
      changed: i, last_edit_at: ago(60 * i), created_at: ago(3600 * i + 60),
      expired: i % 4 === 1, expiry_warned: i % 4 === 1 ? 1 : 0,
      release_id: st === "running" ? 9000 + i : null,
      upload_job_id: st === "running" ? 8000 + i : null,
      review_job_id: st === "running" ? 7000 + i : null,
      delete_job_id: i === 6 ? 6006 : null,
      error: st === "failed" ? E.verifyError + " " + E.ko : null }));
  }
  const history = [];
  for (let i = 0; i < 20; i++) {
    history.push({ id: 300 + i, ios: i % 2 ? E.longBuild : "9." + i + ".0",
      android: i % 2 ? E.ver : "9." + i + ".1", submitted_at: ago((i + 1) * 86400),
      review_job_id: 60000 + i });
  }
  window.rcmVersions = { live: { ios: E.longBuild, android: E.ver, from_plan_job: 641 },
    hints: { ios: E.ver, android: E.ver }, ttl_hours: 24, drafts, history };
  // 버전 페이지 — 문안이 상한에 딱 맞거나 한참 넘는다
  const fill = (s, n) => { let o = ""; while (o.length < n) o += s; return o.slice(0, n); };
  window.rcmPrefill = { schema: 1,
    source: "asc_live:" + E.longBuild + " · play_listing:" + E.ver + " · " + "fallback-".repeat(8),
    locale: "ko",
    ios: { subtitle: fill(E.ko, 30), promotional_text: fill(E.ko, 170),
           description: fill(E.ko, 4000), keywords: E.unbroken, support_url: E.url,
           marketing_url: E.url + "#marketing", whats_new: (E.longLine + "\n").repeat(30) },
    android: { title: fill("Dolomood Renew — " + E.ko, 90), short_description: fill(E.ko, 300),
               full_description: fill(E.ko + E.url + " ", 9000),
               whats_new: (E.longLine + "\n").repeat(20) } };
  window.rcmEdited = { ios: { keywords: E.unbroken + ",tarot" } };
  // 30장(iOS) · 0장(Android → «그래픽 없음» 경고)
  const shots = [];
  for (let i = 0; i < 30; i++) {
    shots.push({ path: "store/screenshots/ios/ko/" + String(i + 1).padStart(2, "0")
      + "_iphone65_" + "very_long_screen_name_".repeat(3) + i + ".png",
      bytes: 123456 + i, width: 1284, height: 2778 });
  }
  const listing = { sha: sha("9e1c4d2f"),
    preview: ["ios.promotional_text: " + E.ko.repeat(4), "description: " + E.ko.repeat(20),
              "ios.keywords: " + E.unbroken, "ios.support_url: " + E.url,
              "ios.subtitle: " + E.ko, "android.title: " + E.longBuild,
              "android.short_description: " + E.ko.repeat(3),
              "android.full_description: " + E.ko.repeat(30),
              "generated by store_listing.py " + "--with-a-long-flag ".repeat(10)],
    diff: ["ios/ko/subtitle: «" + E.ko + "» → «" + E.ko.repeat(2) + "»",
           "screenshots ios +30 " + E.url],
    release_notes: { path: "store/release_notes/" + E.longBuild + "/ko.txt",
                     text: (E.longLine + "\n").repeat(12) },
    screenshots: shots, errors: [E.verifyError] };
  // 상태 화면의 릴리스 보기 — 이름이 길고, 막힘·경고가 길다
  const planDoc = { schema: 1, build_name: E.longBuild, n: 181999, first_release: false,
    store: { asc_live: E.longBuild, asc_live_build: 180999, asc_editing: E.ver,
             play: { production: 180999, production_name: E.longBuild } },
    blockers: [{ code: "B-LONG", text: E.verifyError }],
    warnings: Array.from({ length: 6 },
      (_, i) => ({ code: "W-" + i, text: E.ko.repeat(3) + E.url })),
    measured_at: ago(240) };
  const reviewPlanDoc = { schema: 2, build_name: E.longBuild, n: 181999, plan_verdict: "ok",
    ios: "ready", android: "ready",
    observed: { ios: "PREPARE_FOR_SUBMISSION", android: "completed", auto_release: false },
    listing: { preview: [], diff: [] }, measured_at: ago(240) };
  const job = (id, preset, role, state) => ({ id, preset, role, state: state || "succeeded",
    sha: sha("9e1c4d2f"), ref: E.branch, started_at: ago(4000 - id), finished_at: ago(3000 - id),
    artifacts: [] });
  const release = { setup,
    plan: { job_id: 641, state: "succeeded", measured_at: ago(240), age_seconds: 240, stale: false,
            build_name: E.longBuild, doc: planDoc },
    review: { plan: { job_id: 651, state: "succeeded", age_seconds: 240, stale: false,
                      doc: reviewPlanDoc }, result: null },
    upload: { job_id: 650, state: "succeeded",
              doc: { schema: 1, n: 181999, status: "success", mode: "upload",
                     platforms: ["ios", "android"], tag: "prod/" + E.longBuild + "-181999" } },
    jobs: [job(651, "release-review-" + "with-a-long-preset-name-".repeat(3), "review"),
           job(650, "release-upload", "upload"), job(641, "release-plan", "plan")] };
  // 버전 페이지의 시트 — 남은 것이 15개를 넘게: 빌드 없음 · 플랜 낡음 · N 모름 · 관리형 게시 ·
  // 상한 넘은 칸 여럿 · 그래픽 없음 · V 를 모르는 드라이버 · 빨간 띠
  window.rcmRelease = {
    plan: { job_id: 641, state: "succeeded", build_name: E.ver, age_seconds: 99999, stale: true,
            doc: { schema: 1, build_name: E.ver, store: { asc_live: E.longBuild }, blockers: [],
                   warnings: [] } },
    review: { plan: { job_id: 651, state: "succeeded", age_seconds: 99999, stale: true,
                      doc: { schema: 2, build_name: E.ver, plan_verdict: "ok",
                             ios: "unsafe_release_type", android: "ready",
                             observed: { ios: "PREPARE_FOR_SUBMISSION", auto_release: false } } },
              result: null },
    upload: null, jobs: [] };
  const driverDoc = { configured: true, build_name: E.longBuild, started_at: ago(2280),
    started_by: "pcs-" + "operator-".repeat(6), pid: 4242, running: true, exit_code: null,
    plan_n: 181999, knows_version_stage: false, status_error: E.verifyError,
    log_tail: ["stage S0 branch " + E.branch + " @ 7a03b9f", "stage S1 plan #641",
               "plan: N = 181999", E.driverStatus, E.commit],
    status: [E.driverStatus] };
  window.rcmDriverDoc = driverDoc;
  window.rcmStoreApi = Object.assign({}, api, {
    repos: () => ok({ repos: [{ name: E.repo, release: true, setup },
                              { name: "lib-" + E.repo, release: false }] }),
    repo: () => ok(JSON.parse(JSON.stringify(doc))),
    secrets: () => ok(secrets),
    verify: () => ok(secrets),
    fetchRemote: () => ok({ mirror: doc.mirror, branches: doc.branches }),
    release: () => ok(release),
    listing: () => ok(listing),
    driver: () => ok(window.rcmDriverDoc),
    github: () => ok({
      log: [1, 2, 3, 4, 5].map((i) => ({ sha: sha("9e1c4d2" + i), subject: E.commit,
        author: "pcs-" + "operator-".repeat(5), at: ago(i * 3600) })),
      tags: [{ name: "prod/" + E.longBuild + "-181998", at: ago(9 * 86400) },
             { name: "prod/" + E.longBuild + "-181999", at: ago(2 * 86400) }],
      prs: [{ number: 816, title: E.commit, state: "open" }] }),
    // 자동 저장이 거절되면 서버의 긴 사유가 배지에 온다
    versionListing: (name, id, body) => window.rcmEdgeRefuse
      ? Promise.resolve({ ok: false, status: 409, body: { error: E.verifyError,
          code: "listing_refused", error_code: "listing_refused" } })
      : api.versionListing(name, id, body),
  });
  window.rcmEdgeRefuse = false;
})();
"""
)

STUBS = (RELEASE_STUB_JS, DRIVER_STUB_JS, SHEET_STUB_JS, EDGE_STUB_JS)

# ── 재는 JS ────────────────────────────────────────────────────────────────────

_NAME = """
  const name = (el) => el.tagName.toLowerCase()
    + (el.id ? '#' + el.id : '')
    + (typeof el.className === 'string' && el.className.trim()
      ? '.' + el.className.trim().split(/\\s+/).join('.') : '')
    + (el.getAttribute('data-field') ? '[' + el.getAttribute('data-field') + ']' : '');
  const shown = (el) => el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden';
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
"""

# 스토어 절 안의 접힌 것을 모두 펼친다 — 접힌 상자의 넘침은 아무것도 증명하지 않는다.
# 바텀시트는 펼침/접힘을 장면이 따로 고른다(`data-sheet-toggle`).
OPEN_ALL_JS = """
(() => {
  document.querySelectorAll('#store details').forEach((d) => { d.open = true; });
  return document.querySelectorAll('#store details').length;
})()
"""

# 2·3·4 를 한 번에. 결과는 문제 목록 셋.
INSPECT_JS = (
    """
(() => {
  const root = document.getElementById('store');
"""
    + _NAME
    + """
  const spill = [], overlap = [], untitled = [], scrolled = [], broken = [];
  const all = [root, ...root.querySelectorAll('*')].filter(shown);
  all.forEach((el) => {
    const s = getComputedStyle(el);
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'OPTION') return;
    // 2 — 새는 글자. 인라인 상자는 scrollWidth 가 0 이라 블록 계열만 본다.
    const inline = s.display === 'inline' || s.display === 'contents';
    if (!inline && s.overflowX === 'visible' && el.clientWidth > 0
        && el.scrollWidth > el.clientWidth + 1) {
      spill.push({ node: el, el: name(el), sw: el.scrollWidth, cw: el.clientWidth,
                   text: text(el).slice(0, 70) });
    }
    // 2b — 표가 자기 상자 안에서 옆으로 스크롤하면 오른쪽 열(버튼)이 가려진다
    if ((s.overflowX === 'auto' || s.overflowX === 'scroll') && el.querySelector('table')
        && el.scrollWidth > el.clientWidth + 1) {
      scrolled.push({ el: name(el), sw: el.scrollWidth, cw: el.clientWidth,
                      text: text(el).slice(0, 50) });
    }
    // 2c — 짧은 낱말(공백 없는 8자 이하)이 글자마다 꺾인다(«파/일» · «소/스»)
    if (el.children.length === 0) {
      const t = text(el);
      if (t.length >= 2 && t.length <= 8 && !/\\s/.test(t)) {
        const range = document.createRange();
        range.selectNodeContents(el);
        const tops = new Set([...range.getClientRects()].filter((r) => r.width > 0)
          .map((r) => Math.round(r.top)));
        if (tops.size > 1) broken.push({ el: name(el), text: t, lines: tops.size });
      }
    }
    // 3 — 겹침. flex · grid 의 자식 상자끼리.
    if (/flex|grid/.test(s.display)) {
      const kids = [...el.children].filter((k) => shown(k)
        && !/absolute|fixed/.test(getComputedStyle(k).position));
      const rs = kids.map((k) => k.getBoundingClientRect());
      for (let i = 0; i < kids.length; i++) {
        for (let j = i + 1; j < kids.length; j++) {
          const a = rs[i], b = rs[j];
          if (a.width < 1 || a.height < 1 || b.width < 1 || b.height < 1) continue;
          const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
          const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          if (w > 1.5 && h > 1.5) {
            overlap.push({ parent: name(el), a: name(kids[i]), b: name(kids[j]),
              w: Math.round(w), h: Math.round(h), text: text(kids[j]).slice(0, 40) });
          }
        }
      }
    }
    // 4 — 잘린 글에는 전문이 title 에.
    const ell = s.textOverflow === 'ellipsis' && s.overflowX !== 'visible'
      && el.scrollWidth > el.clientWidth + 1;
    const clamp = s.webkitLineClamp && s.webkitLineClamp !== 'none' && s.overflowY !== 'visible'
      && el.scrollHeight > el.clientHeight + 1;
    if (ell || clamp) {
      const holder = el.closest('[title]');
      const full = text(el);
      const title = holder ? holder.getAttribute('title').replace(/\\s+/g, ' ').trim() : '';
      if (!title || title.indexOf(full.slice(0, 200)) < 0) {
        untitled.push({ el: name(el), how: ell ? 'ellipsis' : 'clamp', text: full.slice(0, 60),
          title: title.slice(0, 60) });
      }
    }
  });
  // 새는 상자의 조상도 같이 샌다 — 원인(가장 안쪽)만 남긴다
  const leaf = spill.filter((a) => !spill.some((b) => b !== a && a.node.contains(b.node)))
    .map((a) => ({ el: a.el, sw: a.sw, cw: a.cw, text: a.text }));
  return { spill: leaf.slice(0, 30), overlap: overlap.slice(0, 30), untitled: untitled.slice(0, 30),
           scrolled: scrolled.slice(0, 10), broken: broken.slice(0, 30),
           counts: [leaf.length, overlap.length, untitled.length, scrolled.length, broken.length] };
})()
"""
)

# 5 — 폰에서 입력칸. 문안 칸만(체크박스 · 숨은 file input 은 뺀다).
INPUTS_JS = (
    """
(() => {
"""
    + _NAME
    + """
  return [...document.querySelectorAll('#store [data-listing-field]')].filter(shown).map((i) => {
    const r = i.getBoundingClientRect(), s = getComputedStyle(i);
    return { el: i.getAttribute('data-listing-field'), w: Math.round(r.width),
             font: parseFloat(s.fontSize), right: Math.round(r.right) };
  });
})()
"""
)


# 6 — 한 줄짜리여야 하는 것들이 긴 값에 몇 줄로 불어나는가. 줄 수는 높이 ÷ 줄 높이(안쪽 여백 뺀).
# 커밋 한 줄(제목 한 줄 + 폰에서는 누가·언제 한 줄), 요약 띠의 칩, 폴링마다 바뀌는 드라이버 머리,
# 검증 칸(긴 오류는 세 줄에서 자르고 전문은 title).
COMPACT = (
    ("#store table.gh td:nth-child(2)", 1),  # 커밋 제목
    ("#store table.gh td:nth-child(3)", 1),  # 누가 · 언제
    ("#store .vstrip .vchip", 1),
    ("#store [data-driver-head]", 1),
    ("#store table.sec td.ver > *", 3),
    ("#release-sheet [data-sheet-status]", 1),
)
COMPACT_JS = (
    """
((rules) => {
"""
    + _NAME
    + """
  const out = [];
  rules.forEach(([sel, max]) => {
    document.querySelectorAll(sel).forEach((el) => {
      if (!shown(el)) return;
      const s = getComputedStyle(el);
      const lh = parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.35;
      const inner = el.getBoundingClientRect().height - parseFloat(s.paddingTop)
        - parseFloat(s.paddingBottom) - parseFloat(s.borderTopWidth)
        - parseFloat(s.borderBottomWidth);
      const lines = Math.round(inner / lh);
      if (lines > max) out.push({ sel, el: name(el), lines, max, text: text(el).slice(0, 50) });
    });
  });
  return out.slice(0, 20);
})
"""
)


# 받은(또는 못 받은) 그림이 자기 칸(figure) 폭을 넘는가
SHOTS_JS = """
[...document.querySelectorAll('#store .shot img')].filter((i) => i.complete).map((i) => {
  const r = i.getBoundingClientRect(), f = i.parentElement.getBoundingClientRect();
  return { alt: i.alt.slice(-40), w: Math.round(r.width), h: Math.round(r.height),
           fw: Math.round(f.width) };
}).filter((x) => x.w > x.fw + 1 || x.h < 100)
"""


def _url(srv, lang: str, route: str, *, gate: bool = False, extra: str = "") -> str:
    q = f"http://127.0.0.1:{srv.port}/?poll=1&lang={lang}&admin=1" + ("&gate=1" if gate else "")
    return q + extra + "#/store/" + REPO + route


def _ready(selector: str, lang: str) -> str:
    return f"{_q(selector)} !== null && document.documentElement.lang === {json.dumps(lang)}"


def _wait(c: Chrome, js: str, timeout: float = 8.0) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = c.eval(js)
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"never became true: {js} → {last!r}")


def _boot(c: Chrome, srv) -> None:
    """스텁을 심고 관리자 토큰을 남긴다. 토큰은 쿼리가 바뀐 다음 문서부터 읽힌다."""
    for s in STUBS:
        c.call("Page.addScriptToEvaluateOnNewDocument", {"source": s})
    c.open(
        f"http://127.0.0.1:{srv.port}/?poll=1",
        ready_js=_q('#view-nav a[data-nav="store"]') + " !== null",
    )
    c.eval(f"localStorage.setItem('rcm.token', {json.dumps(srv.tokens['admin'])})")


def _shot(c: Chrome, scene: str, width: int, lang: str, *, viewport_only: bool = False) -> None:
    """`RCM_UI_SHOTS` 가 있으면 그 장면을 PNG 로 남긴다.

    보통은 뷰포트를 문서 높이만큼 늘려 페이지 전체를 찍는다 — `captureBeyondViewport` 는 붙어 있는
    바텀시트를 첫 화면 자리에 그대로 둬 본문 한가운데를 덮는다. `viewport_only` 면 사람이 보는
    첫 화면 그대로다(펼친 시트가 본문을 덮는 모습).
    """
    out = os.environ.get("RCM_UI_SHOTS")
    if not out:
        return
    tag = os.environ.get("RCM_UI_SHOTS_TAG", "shot")
    Path(out).mkdir(parents=True, exist_ok=True)
    mobile = width <= PHONE_MAX
    if not viewport_only:
        height = int(c.eval("document.documentElement.scrollHeight"))
        c.viewport(width, max(900, height), mobile=mobile)
        time.sleep(0.3)
    shot = c.call("Page.captureScreenshot", {"format": "png"}, timeout=60.0)
    (Path(out) / f"{scene}-{width}-{lang}-{tag}.png").write_bytes(base64.b64decode(shot["data"]))
    c.viewport(width, 900, mobile=mobile)


# 장면: 이름 · 경로 · 관문 여부 · 다 그려졌다는 표시 · 시트를 펼칠지(None 이면 시트 없음)
SCENES = (
    ("gate", "", True, "#store [data-secret]", None),
    ("versions", "", False, "#store .vrow.old", None),
    ("status", "/status", False, "#store table.gh td.sha", None),
    ("draft-sheet-open", "/v/7", False, "#store [data-version-edit] #f-ios-description", True),
    ("draft-sheet-closed", "/v/7", False, "#store [data-version-edit] #f-ios-description", False),
)


def _set_sheet(c: Chrome, want_open: bool) -> None:
    is_open = c.eval(_q("#release-sheet", ".getAttribute('data-open')")) == "1"
    if is_open != want_open:
        c.eval(_q("#release-sheet [data-sheet-toggle]", ".click()"))
        _wait(c, _q("#release-sheet", ".getAttribute('data-open')") + f" === '{int(want_open)}'")


def _measure_scene(c: Chrome, srv, scene: tuple, width: int, lang: str) -> dict[str, Any]:
    name, route, gate, ready, sheet = scene
    c.viewport(width, 900, mobile=width <= PHONE_MAX)
    # 해시만 다르면 문서를 다시 싣지 않는다 — 장면마다 쿼리를 달리해 boot() 를 다시 돌린다
    c.open(
        _url(srv, lang, route, gate=gate, extra=f"&s={name}-{width}"), ready_js=_ready(ready, lang)
    )
    if sheet is not None:
        _set_sheet(c, sheet)
        c.eval("window.scrollTo(0, 0)")
    if sheet is not True:
        c.eval(OPEN_ALL_JS)
    if name == "status":
        # 드라이버 스테퍼 · 깃 카드 · 심사 본체까지 다 그려지기를 기다린다
        _wait(c, _q("#store ol.stepper li.st") + " !== null")
    shots = None
    if c.eval(_q("#store .shot")) is not None:
        # 스텁의 스크린샷은 404 다 — 못 받은 그림이 84×150 상자를 지키는지(alt 글로 번지지 않는지)
        c.eval(_q("#store .shot", ".scrollIntoView({block: 'center'})"))
        _wait(c, "[...document.querySelectorAll('#store .shot img')].some((i) => i.complete)")
        shots = c.eval(SHOTS_JS)
        c.eval("window.scrollTo(0, 0)")
    time.sleep(0.3)
    m = {
        "shots": shots or [],
        "overflow": c.eval(OVERFLOW_JS),
        "inspect": c.eval(INSPECT_JS),
        "inputs": c.eval(INPUTS_JS),
        "compact": c.eval(f"({COMPACT_JS})({json.dumps(COMPACT)})"),
        "errors": c.page_errors(),
    }
    _shot(c, name, width, lang, viewport_only=sheet is True)
    return m


def _problems(name: str, width: int, lang: str, m: dict[str, Any]) -> list[str]:
    where = f"{name} {lang}@{width}"
    out: list[str] = []
    ov = m["overflow"]
    if ov["scrollWidth"] > ov["clientWidth"] + 1 or ov["wide"]:
        out.append(f"{where}: 페이지가 옆으로 밀린다 — {describe(ov)}")
    ins = m["inspect"]
    for s in ins["spill"]:
        out.append(
            f"{where}: 글자가 상자 밖으로 샌다 {s['el']} ({s['sw']} > {s['cw']}) «{s['text']}»"
        )
    for o in ins["overlap"]:
        out.append(
            f"{where}: 겹친다 {o['parent']} 안의 {o['a']} ↔ {o['b']} "
            f"({o['w']}×{o['h']}) «{o['text']}»"
        )
    for t in ins["scrolled"]:
        out.append(
            f"{where}: 표가 자기 상자 안에서 옆으로 스크롤한다(오른쪽 열이 가려진다) "
            f"{t['el']} ({t['sw']} > {t['cw']}) «{t['text']}»"
        )
    for b in ins["broken"]:
        out.append(f"{where}: 짧은 낱말이 {b['lines']}줄로 꺾인다 {b['el']} «{b['text']}»")
    for u in ins["untitled"]:
        out.append(f"{where}: 잘린 글({u['how']})의 전문이 title 에 없다 {u['el']} «{u['text']}»")
    for sh in m["shots"][:3]:
        out.append(f"{where}: 못 받은 스크린샷이 칸을 벗어난다(alt 글로 번진다) {sh}")
    for k in m["compact"]:
        out.append(
            f"{where}: {k['max']}줄이어야 할 {k['el']} 이 {k['lines']}줄로 불었다 «{k['text']}»"
        )
    if width <= PHONE_MAX:
        for i in m["inputs"]:
            if i["w"] < 240 or i["font"] < 16:
                out.append(f"{where}: 폰에서 문안 칸이 좁거나 글자가 작다 {i}")
    if m["errors"]:
        out.append(f"{where}: 페이지 오류 {m['errors']}")
    return out


@pytest.mark.slow
def test_store_screens_hold_extreme_content_at_390_and_1240_in_both_languages(tmp_path):
    """다섯 장면 × 네 경우. 문제는 모아서 한 번에 보인다 — 고치는 사람이 다시 재지 않게."""
    srv = Server(tmp_path, workers=False)
    problems: list[str] = []
    try:
        with Chrome(tmp_path / "chrome-store-edges", window="1280,900") as c:
            _boot(c, srv)
            for scene in SCENES:
                for width, lang in CASES:
                    m = _measure_scene(c, srv, scene, width, lang)
                    problems += _problems(scene[0], width, lang, m)
    finally:
        srv.close()
    assert not problems, f"{len(problems)} 건:\n" + "\n".join(problems)


# ── 데이터가 바뀌어도 자리가 튀지 않는다 ─────────────────────────────────────────

# 칸 하나에 길이를 바꿔 가며 쳐 넣고, 그 칸 · 카운터 · 옆 칸 · 본문의 자리를 잰다.
TYPE_AND_MEASURE_JS = """
((field, value) => {
  const i = document.querySelector('#store [data-listing-field="' + field + '"]');
  i.value = value;
  i.dispatchEvent(new Event('input', { bubbles: true }));
  const fld = i.closest('.fld');
  const cnt = fld.querySelector('.counter');
  const ri = i.getBoundingClientRect(), rf = fld.getBoundingClientRect();
  const rc = cnt.getBoundingClientRect();
  const edit = document.querySelector('#store [data-version-edit]').getBoundingClientRect();
  return { left: ri.left, width: ri.width, cntRight: rc.right, cntLeft: rc.left, fldRight: rf.right,
           cntH: rc.height, editTop: edit.top + window.scrollY, counter: cnt.textContent };
})
"""

HEAD_JS = """
(() => {
  const head = document.querySelector('#store .vedit-head');
  const edit = document.querySelector('#store [data-version-edit]');
  const badge = document.querySelector('#store [data-save-state]');
  const txt = badge && badge.querySelector('.txt');
  return { headH: head.getBoundingClientRect().height,
           editTop: edit.getBoundingClientRect().top + window.scrollY,
           state: badge ? badge.getAttribute('data-save-state') : null,
           text: txt ? txt.textContent : null,
           title: txt ? (txt.getAttribute('title') || (badge.getAttribute('title') || '')) : null,
           badgeRight: badge ? badge.getBoundingClientRect().right : null,
           headRight: head.getBoundingClientRect().right };
})()
"""


@pytest.mark.slow
def test_typing_counting_and_autosave_move_nothing(tmp_path):
    """칸에 1자부터 상한 넘김까지 쳐도 입력칸의 자리 · 폭이 그대로고 카운터는 칸 안에 있다.
    자동 저장 배지가 «고치면 저절로 저장됩니다» → «저장하는 중…» → «자동 저장 · 1s 전» →
    «저장 실패: <긴 사유>» + 다시 저장으로 바뀌는 동안 편집 칸들의 위치가 한 픽셀도 안 움직인다."""
    srv = Server(tmp_path, workers=False)
    problems: list[str] = []
    try:
        with Chrome(tmp_path / "chrome-store-typing", window="1280,900") as c:
            _boot(c, srv)
            for width, lang in CASES:
                where = f"{lang}@{width}"
                c.viewport(width, 900, mobile=width <= PHONE_MAX)
                c.open(
                    _url(srv, lang, "/v/7", extra=f"&t={width}"),
                    ready_js=_ready("#store [data-version-edit] #f-ios-keywords", lang),
                )
                c.eval("window.rcmEdgeRefuse = false")
                _set_sheet(c, False)
                head0 = c.eval(HEAD_JS)
                for field, limit in (("ios.keywords", 100), ("ios.description", 4000)):
                    seen = []
                    for n in (0, 1, 9, 99, limit, limit + 1, limit * 3 + 7):
                        seen.append(
                            c.eval(f"({TYPE_AND_MEASURE_JS})({json.dumps(field)}, 'x'.repeat({n}))")
                        )
                    lefts = {round(s["left"]) for s in seen}
                    widths = {round(s["width"]) for s in seen}
                    if len(lefts) > 1 or len(widths) > 1:
                        problems.append(
                            f"{where} {field}: 치는 동안 입력칸이 움직인다 left {sorted(lefts)} "
                            f"width {sorted(widths)} (카운터 {[s['counter'] for s in seen]})"
                        )
                    for s in seen:
                        if s["cntRight"] > s["fldRight"] + 1:
                            problems.append(f"{where} {field}: 카운터가 칸 밖으로 나갔다 {s}")
                    if len({round(s["cntH"]) for s in seen}) > 1:
                        problems.append(f"{where} {field}: 카운터 높이가 바뀐다 {seen}")
                # 저장하는 중 → 저장됨
                _wait(c, _q('#store [data-save-state="saved"]') + " !== null")
                time.sleep(1.2)  # «1s 전» 처럼 나이가 붙은 뒤
                saved = c.eval(HEAD_JS)
                # 거절 — 서버의 긴 사유가 배지에 온다
                c.eval("window.rcmEdgeRefuse = true")
                c.eval(f"({TYPE_AND_MEASURE_JS})('ios.subtitle', 'refused')")
                _wait(c, _q('#store [data-save-state="failed"]') + " !== null")
                failed = c.eval(HEAD_JS)
                for label, h in (("saved", saved), ("failed", failed)):
                    if (
                        abs(h["editTop"] - head0["editTop"]) > 1
                        or abs(h["headH"] - head0["headH"]) > 1
                    ):
                        problems.append(
                            f"{where}: 배지가 «{head0['text']}» → «{h['text']}»({label}) 로 바뀌자 "
                            f"머리 {head0['headH']:.0f}→{h['headH']:.0f}px, "
                            f"편집 칸 {head0['editTop']:.0f}→{h['editTop']:.0f}px 로 움직였다"
                        )
                    if h["badgeRight"] is not None and h["badgeRight"] > h["headRight"] + 1:
                        problems.append(f"{where}: 배지가 머리 밖으로 나갔다 ({label}) {h}")
                if not failed["title"] or failed["text"] not in failed["title"]:
                    problems.append(f"{where}: 실패 배지의 전문이 title 에 없다 {failed}")
                errors = c.page_errors()
                if errors:
                    problems.append(f"{where}: 페이지 오류 {errors}")
    finally:
        srv.close()
    assert not problems, f"{len(problems)} 건:\n" + "\n".join(problems)


# 시트 머리의 한 줄 · 드라이버 머리 줄 — 같은 eval 안에서 글을 바꾸고 재고 되돌린다(페이지가 다시
# 그리기 전에). 높이가 그대로이고, 잘린 글은 title 로 남아야 한다(그 title 은 진짜 렌더가 단다).
SWAP_JS = """
((sel, outerSel, long) => {
  const el = document.querySelector(sel), outer = document.querySelector(outerSel);
  if (!el || !outer) return { missing: sel };
  const before = outer.getBoundingClientRect().height, h0 = el.getBoundingClientRect().height;
  const orig = el.textContent;
  el.textContent = long;
  const after = outer.getBoundingClientRect().height, h1 = el.getBoundingClientRect().height;
  const doc = document.scrollingElement;
  const out = { before, after, h0, h1, scrollW: doc.scrollWidth, clientW: doc.clientWidth,
                right: el.getBoundingClientRect().right,
                outerRight: outer.getBoundingClientRect().right };
  el.textContent = orig;
  const holder = el.closest('[title]');
  out.title = holder ? holder.getAttribute('title') : '';
  out.text = orig.replace(/\\s+/g, ' ').trim();
  return out;
})
"""


def _swap(c: Chrome, sel: str, outer: str, long: str) -> dict[str, Any]:
    return c.eval(f"({SWAP_JS})({json.dumps(sel)}, {json.dumps(outer)}, {json.dumps(long)})")


def test_status_lines_that_poll_keep_their_height(tmp_path):
    """바텀시트의 접힌 한 줄과 드라이버 머리 줄은 폴링마다 글이 바뀐다 — 그 줄은 한 줄이고(말줄임 ·
    전문은 title) 300자 상태가 와도 시트 · 행의 높이가 그대로다. 시트가 커지면 본문을 덮고,
    머리 줄이 커지면 그 아래 스테퍼가 통째로 튄다."""
    srv = Server(tmp_path, workers=False)
    problems: list[str] = []
    try:
        with Chrome(tmp_path / "chrome-store-lines", window="1280,900") as c:
            _boot(c, srv)
            for width, lang in CASES:
                where = f"{lang}@{width}"
                c.viewport(width, 900, mobile=width <= PHONE_MAX)
                c.open(
                    _url(srv, lang, "/v/7", extra=f"&l={width}"),
                    ready_js=_ready("#release-sheet [data-sheet-status]", lang),
                )
                _set_sheet(c, False)
                m = _swap(c, "#release-sheet [data-sheet-status]", "#release-sheet", DRIVER_STATUS)
                if abs(m["after"] - m["before"]) > 1:
                    problems.append(
                        f"{where}: 시트의 한 줄이 길어지자 접힌 시트가 {m['before']:.0f}→"
                        f"{m['after']:.0f}px 로 자랐다"
                    )
                if m["right"] > m["outerRight"] + 1 or m["scrollW"] > m["clientW"] + 1:
                    problems.append(f"{where}: 시트의 한 줄이 시트 밖으로 나갔다 {m}")
                if not m["title"] or m["text"] not in m["title"].replace("\n", " "):
                    problems.append(f"{where}: 시트의 한 줄 전문이 title 에 없다 {m}")
                # 상태 화면의 드라이버 머리 줄
                c.open(
                    _url(srv, lang, "/status", extra=f"&l={width}"),
                    ready_js=_ready("#store [data-driver-head]", lang),
                )
                c.eval(OPEN_ALL_JS)
                d = _swap(
                    c,
                    "#store [data-driver-head] > span:last-child",
                    '#store details.srow[data-row="build"]',
                    DRIVER_STATUS,
                )
                if abs(d["after"] - d["before"]) > 1:
                    problems.append(
                        f"{where}: 드라이버 머리 줄이 길어지자 Build 행이 {d['before']:.0f}→"
                        f"{d['after']:.0f}px 로 바뀌었다"
                    )
                if not d["title"] or d["text"] not in d["title"]:
                    problems.append(f"{where}: 드라이버 머리 줄 전문이 title 에 없다 {d}")
                errors = c.page_errors()
                if errors:
                    problems.append(f"{where}: 페이지 오류 {errors}")
    finally:
        srv.close()
    assert not problems, f"{len(problems)} 건:\n" + "\n".join(problems)

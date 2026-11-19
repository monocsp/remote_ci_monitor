"""보관함(M5p) 서버 HTML — 목록 · 항목 · 보관 중 · 실패 · 묘비 · 없음 · 쓸 수 없음 페이지.

명세는 docs/m5p-workplan.md §4.7 · §15.6(결정 141). 규칙:

- 템플릿 문자열이고 **스크립트가 없다**. 폰에서 바로 뜨고 토큰이 필요 없다.
- 한국어가 기본이고 `?lang=en` 이면 영어다(정확히 `en` 만 — `EN` · `fr` · 빈 값은 한국어). 영어
  페이지의 항목 링크 · 되살리기 form · 그 뒤 303 이 `?lang=en` 을 잇는다. 파일 링크는 잇지 않는다.
- **모든 링크는 절대 경로**(`/shelf/<N>/f/<quote(path, safe='/')>`)다. 라우터가 끝 `/` 를 떼므로
  상대 경로는 깨진다. Host 로 만든 절대 URL 은 넣지 않는다. 보이는 문자열은 전부 이스케이프한다.
- 시각은 `[display] timezone`(비면 서버의 지역 시간)으로, 날짜는 그 시간대의 달력 날짜다.
- 바이트는 `rcm check` · `rcm shelf` 와 같은 십진 눈금(`render_text._bytes`)이다.
- 정리 대기 항목 페이지는 그림 격자가 없다 — 지울지 판단하려고 보는 것이 되살리기가 되면 안 된다.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, tzinfo
from typing import Any
from urllib.parse import quote

from remote_ci_monitor.core.render_text import _bytes

LANGS = ("ko", "en")

#: 화면 문구. 두 로케일의 키 집합은 같다(시험이 잠근다). 이유 코드(`SHELF_REASON_CODES`)는 문장으로
#: 바꾸지 않고 코드 그대로 보인다 — 페이지 · CLI · SPA 가 같은 글자를 쓴다(결정 37).
CATALOG: dict[str, dict[str, str]] = {
    "ko": {
        "shelf": "보관함",
        "kept": "보관 중",
        "stale": "정리 대기",
        "removed": "최근 지움(30일)",
        "restore": "되살리기",
        "used": "사용량",
        "all": "전체",
        "job": "잡",
        "preset": "프리셋",
        "inputs": "입력",
        "branch": "브랜치",
        "commit": "커밋",
        "requester": "요청자",
        "finished": "끝난 시각",
        "job_state": "잡 결과",
        "files": "파일",
        "size": "크기",
        "last_used": "마지막 사용",
        "kept_at": "보관한 시각",
        "stale_from": "정리 대기 예정",
        "delete_on": "삭제 예정",
        "zip": "zip 으로 받기",
        "path": "경로",
        "download": "받기",
        "expanded": "펼친 파일",
        "copying": "보관하는 중",
        "copying_note": "잡의 산출물을 보관함으로 옮기는 중입니다. 잠시 뒤 다시 열어 보세요.",
        "failed": "보관하지 못했습니다",
        "removes_on": "{date} 에 지워집니다",
        "removed_on": "{date} 에 지웠습니다",
        "stale_note": "오래 쓰지 않아 정리 대기 중입니다. 되살리면 다시 보관 중이 됩니다.",
        "not_found": "보관함에 없는 항목입니다",
        "unavailable": "보관함 폴더를 쓸 수 없습니다 — 그 디스크가 붙어 있는지 확인하세요",
        "nothing": "보관한 항목이 없습니다",
        "job_page": "잡 보기",
        "back": "보관함으로",
        "skipped": "건너뛴 멤버",
        "not_expanded": "펼치지 않은 tar",
    },
    "en": {
        "shelf": "Shelf",
        "kept": "Kept",
        "stale": "Cleanup pending",
        "removed": "Recently removed (30 days)",
        "restore": "Restore",
        "used": "Used",
        "all": "All",
        "job": "Job",
        "preset": "Preset",
        "inputs": "Inputs",
        "branch": "Branch",
        "commit": "Commit",
        "requester": "Requested by",
        "finished": "Finished",
        "job_state": "Job result",
        "files": "Files",
        "size": "Size",
        "last_used": "Last used",
        "kept_at": "Kept at",
        "stale_from": "Cleanup pending from",
        "delete_on": "Removed on",
        "zip": "Download as zip",
        "path": "Path",
        "download": "Download",
        "expanded": "expanded",
        "copying": "Copying to the shelf…",
        "copying_note": "The job's files are being copied to the shelf. Open this page again soon.",
        "failed": "Not kept",
        "removes_on": "Removed on {date}",
        "removed_on": "Removed on {date}",
        "stale_note": "Unused for a long time. Restore it to keep it again.",
        "not_found": "Not on the shelf",
        "unavailable": "The shelf folder is unavailable — is its disk mounted?",
        "nothing": "Nothing on the shelf",
        "job_page": "Job page",
        "back": "Back to the shelf",
        "skipped": "Skipped members",
        "not_expanded": "Tars not expanded",
    },
}

_STYLE = (
    "body{font:15px/1.45 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:1100px;"
    "padding:16px;color:#1d1d1f;background:#fff}"
    "h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:24px 0 8px}"
    "a{color:#0a58ca}table{border-collapse:collapse;width:100%}"
    "th,td{text-align:left;padding:4px 8px;border-bottom:1px solid #e5e5e5;vertical-align:top}"
    "td.num{text-align:right;white-space:nowrap}.muted{color:#6e6e73}"
    ".grid{display:flex;flex-wrap:wrap;gap:12px}.grid figure{margin:0;max-width:240px}"
    ".grid img{max-width:240px;height:auto;border:1px solid #e5e5e5;background:#fafafa}"
    ".grid figcaption{font-size:12px;word-break:break-all}"
    ".sub td:first-child{padding-left:24px}nav a{margin-right:12px}"
    "button{font:inherit;padding:2px 10px}.wrap{overflow-x:auto}"
    "dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;margin:8px 0}"
    "dt{color:#6e6e73}dd{margin:0;word-break:break-all}"
    "@media (prefers-color-scheme: dark){body{background:#1c1c1e;color:#f2f2f7}"
    "th,td{border-color:#3a3a3c}a{color:#64a8ff}.muted{color:#98989d}}"
)


def lang_of(query: Mapping[str, Sequence[str]]) -> str:
    """`?lang=en` 일 때만 영어. 대소문자를 고치지 않는다(CG35)."""
    return "en" if list(query.get("lang") or ()) == ["en"] else "ko"


def lang_query(lang: str) -> str:
    return "?lang=en" if lang == "en" else ""


def _t(lang: str, key: str, **args: Any) -> str:
    text = CATALOG.get(lang, CATALOG["ko"])[key]
    return text.format(**args) if args else text


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _when(ts: float | None, tz: tzinfo | None, *, date_only: bool = False) -> str:
    if ts is None:
        return "—"
    moment = datetime.fromtimestamp(float(ts), UTC)
    local = moment.astimezone(tz) if tz is not None else moment.astimezone()
    return local.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M")


def file_href(job_id: int, path: str) -> str:
    return f"/shelf/{int(job_id)}/f/{quote(path, safe='/')}"


def _inputs(raw: str | None) -> str:
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    if not isinstance(doc, dict) or not doc:
        return "—"
    return ", ".join(f"{k}={v}" for k, v in sorted(doc.items()))


def _page(lang: str, title: str, body: str) -> str:
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title)}</title><style>{_STYLE}</style></head><body>{body}</body></html>"
    )


def _back(lang: str) -> str:
    return f'<nav><a href="/shelf/{lang_query(lang)}">{_e(_t(lang, "back"))}</a></nav>'


def _restore_form(job_id: int, lang: str) -> str:
    return (
        f'<form method="post" action="/shelf/{int(job_id)}/restore{lang_query(lang)}">'
        f'<button type="submit">{_e(_t(lang, "restore"))}</button></form>'
    )


# ── 목록 ──


def list_page(
    *,
    lang: str,
    tz: tzinfo | None,
    kept: Iterable[Mapping[str, Any]],
    stale: Iterable[Mapping[str, Any]],
    removed: Iterable[Mapping[str, Any]],
    stored_bytes: int | None,
    limit_bytes: int | None,
    presets: Iterable[str],
    preset: str | None,
) -> str:
    """`GET /shelf/` — 두 칸(보관 중 · 정리 대기)과 최근 지움. 행은 `item_row` 의 dict 다."""
    q = lang_query(lang)
    amp = "&amp;lang=en" if lang == "en" else ""
    usage = f"{_bytes(stored_bytes)} / {_bytes(limit_bytes)}"
    links = [f'<a href="/shelf/{q}">{_e(_t(lang, "all"))}</a>']
    for name in sorted(set(presets)):
        links.append(f'<a href="/shelf/?preset={quote(name, safe="")}{amp}">{_e(name)}</a>')
    parts = [
        f"<h1>{_e(_t(lang, 'shelf'))}</h1>",
        f'<p class="muted">{_e(_t(lang, "used"))} {_e(usage)}'
        + (f" · {_e(_t(lang, 'preset'))}: {_e(preset)}" if preset else "")
        + "</p>",
        f"<nav>{''.join(links)}</nav>",
        _section(lang, tz, "kept", kept, due_key="stale_at", due_label="stale_from"),
        _section(lang, tz, "stale", stale, due_key="delete_at", due_label="delete_on"),
        f'<h2 id="removed">{_e(_t(lang, "removed"))}</h2>',
    ]
    gone = list(removed)
    if gone:
        rows = "".join(
            f"<li>#{int(r['job_id'])} · {_e(r['preset'])} — "
            f"{_e(_t(lang, 'removed_on', date=_when(r['deleted_at'], tz, date_only=True)))}</li>"
            for r in gone
        )
        parts.append(f"<ul>{rows}</ul>")
    else:
        parts.append('<p class="muted">—</p>')
    return _page(lang, _t(lang, "shelf"), "".join(parts))


def _section(
    lang: str,
    tz: tzinfo | None,
    key: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    due_key: str,
    due_label: str,
) -> str:
    q = lang_query(lang)
    items = list(rows)
    head = f'<h2 id="{key}">{_e(_t(lang, key))} ({len(items)})</h2>'
    if not items:
        return head + f'<p class="muted">{_e(_t(lang, "nothing"))}</p>'
    cols = [
        "job",
        "preset",
        "inputs",
        "branch",
        "finished",
        "files",
        "size",
        "last_used",
        due_label,
    ]
    th = "".join(f"<th>{_e(_t(lang, c))}</th>" for c in cols)
    if key == "stale":
        th += "<th></th>"
    body = []
    for r in items:
        jid = int(r["job_id"])
        count = r.get("file_count")
        cells = [
            f'<td><a href="/shelf/{jid}/{q}">#{jid}</a></td>',
            f"<td>{_e(r['preset'])}</td>",
            f"<td>{_e(_inputs(r.get('inputs_json')))}</td>",
            f"<td>{_e(r.get('branch') or '—')}</td>",
            f"<td>{_e(_when(r.get('job_finished_at'), tz))}</td>",
            f'<td class="num">{_e(count if count is not None else "—")}</td>',
            f'<td class="num">{_e(_bytes(r.get("total_bytes")))}</td>',
            f"<td>{_e(_when(r.get('last_used_at'), tz, date_only=True))}</td>",
            f"<td>{_e(_when(r.get(due_key), tz, date_only=True))}</td>",
        ]
        if key == "stale":
            cells.append(f"<td>{_restore_form(jid, lang)}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return head + f'<div class="wrap"><table><tr>{th}</tr>{"".join(body)}</table></div>'


# ── 항목 ──


def _facts(lang: str, tz: tzinfo | None, r: Mapping[str, Any], *, job_link: bool) -> str:
    jid = int(r["job_id"])
    sha = r.get("sha") or ""
    facts = [
        ("preset", _e(r.get("preset"))),
        ("inputs", _e(_inputs(r.get("inputs_json")))),
        ("branch", _e(r.get("branch") or "—")),
        ("commit", _e(sha[:12] or "—")),
        ("finished", _e(_when(r.get("job_finished_at"), tz))),
        ("job_state", _e(r.get("job_state") or "—")),
        ("requester", _e(r.get("requester") or "—")),
    ]
    rows = "".join(f"<dt>{_e(_t(lang, k))}</dt><dd>{v}</dd>" for k, v in facts)
    link = f'<a href="/#/jobs/{jid}">{_e(_t(lang, "job_page"))}</a>' if job_link else ""
    return f"{_back(lang)}<p>{link}</p><dl>{rows}</dl>"


def item_page(
    *,
    lang: str,
    tz: tzinfo | None,
    item: Mapping[str, Any],
    files: Sequence[Mapping[str, Any]],
    expand_skipped: Sequence[Mapping[str, Any]],
    skipped_count: int,
    view: str,
    job_link: bool,
) -> str:
    """보관 중(`view="active"`) · 정리 대기(`view="stale"`) 항목. 정리 대기는 그림이 없다."""
    jid = int(item["job_id"])
    title = f"#{jid} · {item.get('preset')}"
    parts = [f"<h1>{_e(title)}</h1>", _facts(lang, tz, item, job_link=job_link)]
    state_word = _t(lang, "kept" if view == "active" else "stale")
    dates = [
        (_t(lang, "kept_at"), _when(item.get("shelved_at"), tz)),
        (_t(lang, "last_used"), _when(item.get("last_used_at"), tz)),
        (_t(lang, "stale_from"), _when(item.get("stale_at"), tz, date_only=True)),
        (_t(lang, "delete_on"), _when(item.get("delete_at"), tz, date_only=True)),
    ]
    facts = " · ".join(f"{_e(k)} {_e(v)}" for k, v in dates)
    parts.append(f'<h2>{_e(state_word)}</h2><p class="muted">{facts}</p>')
    if view == "stale":
        when = _when(item.get("delete_at"), tz, date_only=True)
        parts.append(
            f"<p><strong>{_e(_t(lang, 'removes_on', date=when))}</strong> "
            f"{_e(_t(lang, 'stale_note'))}</p>{_restore_form(jid, lang)}"
        )
    else:
        parts.append(f'<p><a href="/shelf/{jid}.zip">{_e(_t(lang, "zip"))}</a></p>')
        images = [f for f in files if str(f.get("type", "")).startswith("image/")]
        if images:
            figs = "".join(
                f'<figure><a href="{file_href(jid, f["path"])}">'
                f'<img src="{file_href(jid, f["path"])}" width="240" alt="{_e(f["path"])}" '
                f'loading="lazy"></a><figcaption>{_e(f["path"])}</figcaption></figure>'
                for f in images
            )
            parts.append(f'<div class="grid">{figs}</div>')
    head = (
        f"<tr><th>{_e(_t(lang, 'path'))}</th><th>{_e(_t(lang, 'size'))}</th>"
        f"<th>{_e(_t(lang, 'download'))}</th></tr>"
    )
    rows = []
    for f in files:
        sub = ' class="sub"' if f.get("expanded_from") else ""
        href = file_href(jid, f["path"])
        rows.append(
            f'<tr{sub}><td>{_e(f["path"])}</td><td class="num">{_e(_bytes(f.get("size")))}</td>'
            f'<td><a href="{href}">{_e(_t(lang, "download"))}</a></td></tr>'
        )
    parts.append(
        f"<h2>{_e(_t(lang, 'files'))} ({len(files)})</h2>"
        f'<div class="wrap"><table>{head}{"".join(rows)}</table></div>'
    )
    if expand_skipped:
        items = "".join(
            f"<li>{_e(s.get('path'))} — {_e(s.get('reason'))}</li>" for s in expand_skipped
        )
        parts.append(f"<h2>{_e(_t(lang, 'not_expanded'))}</h2><ul>{items}</ul>")
    if skipped_count:
        parts.append(f'<p class="muted">{_e(_t(lang, "skipped"))}: {int(skipped_count)}</p>')
    return _page(lang, title, "".join(parts))


def copying_page(*, lang: str, tz: tzinfo | None, item: Mapping[str, Any], job_link: bool) -> str:
    jid = int(item["job_id"])
    title = f"#{jid} · {item.get('preset')}"
    body = (
        f"<h1>{_e(title)}</h1>{_facts(lang, tz, item, job_link=job_link)}"
        f"<h2>{_e(_t(lang, 'copying'))}</h2><p>{_e(_t(lang, 'copying_note'))}</p>"
    )
    return _page(lang, title, body)


def failed_page(*, lang: str, tz: tzinfo | None, item: Mapping[str, Any], job_link: bool) -> str:
    jid = int(item["job_id"])
    title = f"#{jid} · {item.get('preset')}"
    body = (
        f"<h1>{_e(title)}</h1>{_facts(lang, tz, item, job_link=job_link)}"
        f"<h2>{_e(_t(lang, 'failed'))}</h2><p><code>{_e(item.get('reason_code') or '—')}</code></p>"
    )
    return _page(lang, title, body)


def gone_page(*, lang: str, tz: tzinfo | None, job_id: int, deleted_at: float | None) -> str:
    when = _when(deleted_at, tz, date_only=True)
    body = f"<h1>#{int(job_id)}</h1><p>{_e(_t(lang, 'removed_on', date=when))}</p>{_back(lang)}"
    return _page(lang, _t(lang, "shelf"), body)


def message_page(*, lang: str, key: str) -> str:
    """없음 · 쓸 수 없음 같은 한 줄 페이지. `key` 는 카탈로그 키다."""
    body = f"<h1>{_e(_t(lang, 'shelf'))}</h1><p>{_e(_t(lang, key))}</p>{_back(lang)}"
    return _page(lang, _t(lang, "shelf"), body)


__all__ = [
    "CATALOG",
    "LANGS",
    "copying_page",
    "failed_page",
    "file_href",
    "gone_page",
    "item_page",
    "lang_of",
    "lang_query",
    "list_page",
    "message_page",
]

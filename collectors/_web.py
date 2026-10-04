# -*- coding: utf-8 -*-
"""国税庁・国税不服審判所のサイトを読むための共通部品。

収集プログラムごとに同じ処理を書くと、片方だけ直して片方が壊れたままになる。
（実際に、通達の収集で直した CRLF の問題は、ここに寄せる前は他で再発しうる状態だった）
取得・文字コード判定・タグ除去・書き出しは、ここだけで行う。

作法
----
- 1回の取得ごとに SLEEP 秒あける。官公庁サイトへの負荷を避けるためである
- robots.txt で禁止された場所には行かない（取得前に確認する）
- User-Agent に連絡先（リポジトリのURL）を入れる
- 書き出しは中身が変わったときだけ行う。**差分がそのまま改正・追加の記録になる**
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_AGENT = "tax-knowledge-collector/1.0 (https://github.com/teamtks/tax-knowledge)"
SLEEP = 0.8
RETRY = 3

_robots: dict[str, urllib.robotparser.RobotFileParser] = {}
_last = [0.0]


def 許可されているか(url: str) -> bool:
    p = urllib.parse.urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    rp = _robots.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                rp.parse(r.read().decode("utf-8", "replace").splitlines())
        except Exception:                                   # noqa: BLE001
            rp.parse([])            # 取れなければ制限なしとみなす（公開サイトの通例）
        _robots[base] = rp
    return rp.can_fetch(USER_AGENT, url)


def fetch(url: str) -> tuple[bytes, str]:
    if not 許可されているか(url):
        raise RuntimeError(f"robots.txt で禁止されています: {url}")
    wait = SLEEP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    last = None
    for attempt in range(RETRY):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                final = r.geturl()
            _last[0] = time.time()
            # 国税庁サイトは、存在しないページを /error/404.htm へ転送して 200 を返す。
            # これを資料として保存しないよう、ここで失敗にする（再試行もしない）。
            if "/error/" in urllib.parse.urlsplit(final).path:
                raise LookupError(f"ページがありません（{final} へ転送された）: {url}")
            return raw, final
        except LookupError:
            _last[0] = time.time()
            raise
        except Exception as e:                              # noqa: BLE001
            last = e
            if attempt < RETRY - 1:
                time.sleep(2 ** attempt)
    _last[0] = time.time()
    raise RuntimeError(f"取得に失敗しました: {url} / {last}")


def decode(raw: bytes) -> str:
    """国税庁サイトは Shift_JIS、審判所は UTF-8。meta charset で決める。"""
    m = re.search(rb'charset=["\']?([A-Za-z0-9_-]+)', raw[:3000], re.I)
    enc = (m.group(1).decode("ascii", "ignore") if m else "utf-8").lower()
    if enc in ("shift_jis", "sjis", "x-sjis", "shift-jis"):
        enc = "cp932"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("cp932", errors="replace")


def get(url: str) -> tuple[str, str]:
    raw, final = fetch(url)
    return decode(raw), final


def strip_tags(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|dd|dt)>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n[ 　]*\n[\s　]*", "\n\n", s)
    return s.strip()


def links(h: str, base: str) -> list[tuple[str, str]]:
    out = []
    for href, txt in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', h, re.S | re.I):
        if href.startswith(("javascript:", "mailto:")):
            continue
        out.append((urllib.parse.urljoin(base, href), strip_tags(txt)))
    return out


def title(h: str) -> str:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S | re.I)
    if m and strip_tags(m.group(1)):
        return strip_tags(m.group(1))
    m = re.search(r"<title>(.*?)</title>", h, re.S | re.I)
    return strip_tags(m.group(1)).split("｜")[0].split("|")[0].strip() if m else ""


def nta_body(h: str) -> str:
    """国税庁ページの本文領域。取れなければ <body> 全体。"""
    m = re.search(r'<!--\s*InstanceBeginEditable\s+name="bodyArea"\s*-->(.*?)<!--\s*InstanceEndEditable\s*-->',
                  h, re.S | re.I)
    if m:
        return m.group(1)
    m = re.search(r'<div[^>]+id="(?:bodyArea|contents|main)"[^>]*>(.*)', h, re.S | re.I)
    if m:
        return m.group(1)
    m = re.search(r"<body[^>]*>(.*)</body>", h, re.S | re.I)
    return m.group(1) if m else h


_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(s: str, fallback: str = "untitled") -> str:
    s = _BAD.sub("_", (s or "").strip()).strip(" .")
    return s[:120] or fallback


def write_if_changed(path: str, text: str) -> str:
    """'作成' / '更新' / '不変' を返す。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = text.replace("\r\n", "\n")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return "不変"
        st = "更新"
    else:
        st = "作成"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return st


def write_json_if_changed(path: str, obj) -> str:
    return write_if_changed(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def 出典(url: str, 発行: str = "国税庁") -> str:
    """出典表示。国税庁・国税不服審判所の利用規約（公共データ利用規約 第1.0版）に合わせる。

    規約は「出典：国税庁ホームページ（URL）」の形の出典と、
    **編集・加工した場合はその旨の記載**を求め、国が作成したかのように見せることを禁じている。
    このリポジトリは見出し・基準日・注意書きを付けて書式を整えているので「加工して作成」にあたる。
    税務大学校（税務訴訟資料）は国税庁ホームページの一部なので、国税庁として表示する。
    """
    発行 = "国税不服審判所" if "審判所" in 発行 else "国税庁"
    return (f"> 出典：{発行}ホームページ（{url}）を加工して作成。"
            f"tax-knowledge が書式を整え見出し等を付したもので、{発行}が作成したものではない。"
            "公共データ利用規約（第1.0版）に準拠して利用している。")

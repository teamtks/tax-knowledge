#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国税庁サイトから法令解釈通達を取得し、通達番号ごとの Markdown として保存する。

設計上の約束は collectors/egov_law.py と同じ。
    1. AI を使わない。取得と整形だけを行う。
    2. 標準ライブラリのみを使う。
    3. 取得日時をファイルに書き込まない（git のコミット日時が取得日時）。
    4. 本文が変わらないファイルは書き換えない。
    5. 要約も解釈も行わない。保存するのは通達の原文だけ。

法令と違い通達にはAPIが無いため、目次ページから子ページを再帰的に辿る。
巡回は指定されたパス配下に限定し、1ページごとに間隔を空ける。

国税庁ページの構造（2026-09 時点で確認）
--------------------------------------
    <!-- InstanceBeginEditable name="bodyArea" -->  … 本文領域の始まり
    <h2>（短期の前払費用）</h2>                      … 通達の見出し
    <p class="indent1"><strong>2－2－14　</strong>本文…</p>
    <p class="indent2">(注)　…</p>
    <!-- InstanceEndEditable -->                    … 本文領域の終わり

    文字コードは Shift_JIS。Shift_JIS に無い文字は &#x5861; のような
    数値文字参照で書かれているため、必ず HTML エンティティを戻すこと。

出力
----
    tsutatsu/<code>/_meta.json           通達名・出典・項目一覧
    tsutatsu/<code>/README.md            通達番号と見出しの索引
    tsutatsu/<code>/items/<番号>.md       通達1項目1ファイル

出典
----
    国税庁 法令解釈通達 https://www.nta.go.jp/law/tsutatsu/menu.htm
    通達は著作権法13条2号により著作権の目的とならない。
    国税庁サイトは政府標準利用規約に準拠しており、出典明示のうえ複製・加工できる。
"""

import argparse
import collections
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "tsutatsu")
CONFIG = os.path.join(ROOT, "config", "tsutatsu.json")

USER_AGENT = "tax-knowledge-collector/1.0 (https://github.com/teamtks/tax-knowledge)"
SLEEP = 0.8          # 国税庁サイトへの負荷を避ける
RETRY = 3
HOST = "www.nta.go.jp"


# ---------------------------------------------------------------- 取得

def fetch(url):
    last = None
    for attempt in range(RETRY):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                final = r.geturl()
            return raw, final
        except Exception as e:                        # noqa: BLE001
            last = e
            if attempt < RETRY - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError("取得に失敗しました: %s / %s" % (url, last))


def decode(raw):
    """国税庁サイトは Shift_JIS。meta charset を見て決める。"""
    m = re.search(rb'charset=["\']?([A-Za-z0-9_-]+)', raw[:3000], re.I)
    enc = (m.group(1).decode("ascii", "ignore") if m else "cp932").lower()
    if enc in ("shift_jis", "sjis", "x-sjis", "shift-jis"):
        enc = "cp932"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("cp932", errors="replace")


# ---------------------------------------------------------------- 整形

def strip_tags(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)                 # &#x5861; → 塡
    # 元のHTMLは CRLF 改行である。そのまま書き出すと .gitattributes の eol=lf と
    # 食い違い、中身が変わっていなくても毎回「変更あり」と判定されてしまう。
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("　", "　")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


_FW = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_num(s):
    """通達番号を正規化する。`２－２－１４　` → `2-2-14`

    措置法通達では、同じ条でも `42の4（1）-1` と `42の4(4)-3` のように
    全角括弧と半角括弧が混在している（国税庁サイト側の揺れ）。
    引く側が両方を試さずに済むよう、半角に寄せて1つの表記に統一する。
    """
    s = strip_tags(s).translate(_FW)
    s = re.sub(r"[－ー‐―−–—]", "-", s)
    s = s.replace("（", "(").replace("）", ")")
    s = re.sub(r"\s+", "", s)
    return s.strip("-　 ")


# 段落の先頭にある通達番号を取り出す。
#
# <strong> の囲み方がページによって揺れており、
#     <strong>2－2－14　</strong>前払費用…       （番号全体を囲む）
#     <strong>1</strong>－1－1　事業者とは…      （最初の数字だけを囲む）
# の両方が実在する。したがって <strong> の範囲ではなく、
# タグを外した段落テキストの先頭から番号を読み取る。
_NUM_HEAD = re.compile(r"^\s*([0-9０-９]+(?:[の\-－ー‐―−–—()（）0-9０-９]*[0-9０-９)）])?)\s*　*")

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(s, fallback="untitled"):
    s = _BAD.sub("_", (s or "").strip()).strip(" .")
    return s or fallback


def body_area(h):
    """本文領域を切り出す。取れなければページ全体を返す。"""
    m = re.search(r'<!--\s*InstanceBeginEditable\s+name="bodyArea"\s*-->(.*?)<!--\s*InstanceEndEditable\s*-->',
                  h, re.S | re.I)
    return m.group(1) if m else h


def page_heading(h):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S | re.I)
    if m:
        return strip_tags(m.group(1))
    m = re.search(r"<title>(.*?)</title>", h, re.S | re.I)
    return strip_tags(m.group(1)).split("｜")[0] if m else ""


def parse_items(h, page_url):
    """1ページから通達項目を取り出す。

    <h2> が見出し、<p class="indent1"><strong>番号</strong>本文</p> が項目の本体、
    続く <p class="indentN"> がその項目にぶら下がる注記・号である。
    """
    area = body_area(h)
    heading = page_heading(h)

    # 見出し・段落を文書順に並べる
    tokens = []
    for m in re.finditer(
            r'<h2[^>]*>(?P<h2>.*?)</h2>|<p\s+class="(?P<cls>indent\d+)"[^>]*>(?P<p>.*?)</p>',
            area, re.S | re.I):
        if m.group("h2") is not None:
            tokens.append(("h2", strip_tags(m.group("h2"))))
        else:
            tokens.append((m.group("cls"), m.group("p")))

    items = []
    caption = ""
    cur = None
    for kind, val in tokens:
        if kind == "h2":
            caption = val
            continue
        if kind == "indent1" and re.search(r"<strong>", val, re.I):
            # <strong> の有無だけを「項目の先頭である」印として使い、
            # 番号自体はタグを外した本文から読み取る。
            plain = strip_tags(val)
            mh = _NUM_HEAD.match(plain)
            num = normalize_num(mh.group(1)) if mh else ""
            # 通達番号は必ず階層を持つ（2-2-14 / 61の4(1)-1 など）。
            # ハイフンを含まないものは番号ではなく本文の書き出しなので採らない。
            if num and "-" in num:
                body = plain[mh.end():].strip()
                cur = {"num": num, "caption": caption, "heading": heading,
                       "url": page_url, "body": [body] if body else [], "notes": []}
                items.append(cur)
                continue
        # 番号を持たない段落は、直前の項目にぶら下げる
        text = strip_tags(val)
        if not text:
            continue
        if cur is not None:
            cur["notes"].append((kind, text))
    return items


def render_item(it, ryakusho):
    L = []
    title = "%s%s" % (ryakusho, it["num"])
    if it["caption"]:
        title += "　" + it["caption"]
    L.append("# " + title)
    L.append("")
    if it["heading"]:
        L.append("> " + it["heading"])
        L.append("")
    for b in it["body"]:
        L.append(b)
        L.append("")
    for kind, text in it["notes"]:
        depth = int(re.sub(r"\D", "", kind) or 1)
        if text.startswith("(注)") or text.startswith("（注）"):
            L.append(text)
        else:
            L.append(("  " * max(depth - 1, 0)) + "- " + text if depth > 1 else text)
        L.append("")
    L.append("---")
    L.append("")
    L.append("出典: 国税庁 [%s](%s)" % (it["heading"] or "法令解釈通達", it["url"]))
    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------- 入出力

def write_if_changed(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", newline="") as f:
            if f.read() == text:
                return "unchanged"
        status = "updated"
    else:
        status = "created"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return status


def prune(dirpath, keep):
    removed = []
    if not os.path.isdir(dirpath):
        return removed
    for name in sorted(os.listdir(dirpath)):
        if name not in keep:
            os.remove(os.path.join(dirpath, name))
            removed.append(name)
    return removed


# ---------------------------------------------------------------- 巡回

def crawl(entry, verbose=True):
    root = entry["root"]
    prefix = entry["prefix"]
    excludes = entry.get("exclude") or []
    # 基本通達では、8桁の日付を名前にしたディレクトリ（例 /20230930/）が
    # 旧版のアーカイブである。拾うと廃止前の本文が現行として混ざるため除く。
    # 個別通達では日付ディレクトリが本体なので、設定で明示したときだけ効かせる。
    ex_re = re.compile(entry["exclude_regex"]) if entry.get("exclude_regex") else None
    max_pages = int(entry.get("max_pages", 300))

    seen, queue, pages = set(), collections.deque([root]), []
    while queue and len(pages) < max_pages:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        try:
            raw, final = fetch(url)
        except Exception as e:                       # noqa: BLE001
            print("    !! %s: %s" % (url, e))
            continue
        if "/error/" in final:
            continue
        h = decode(raw)
        pages.append((final, h))

        for href in re.findall(r'href="([^"#?]+\.htm[l]?)"', h, re.I):
            absu = urllib.parse.urljoin(final, href)
            p = urllib.parse.urlparse(absu)
            if p.netloc != HOST or not p.path.startswith(prefix):
                continue
            if any(x in p.path for x in excludes):
                continue
            if ex_re is not None and ex_re.search(p.path):
                continue
            clean = "https://%s%s" % (HOST, p.path)
            if clean not in seen:
                queue.append(clean)
        time.sleep(SLEEP)
        if verbose and len(pages) % 25 == 0:
            print("    %d ページ取得（待ち %d）" % (len(pages), len(queue)))

    truncated = bool(queue)
    return pages, truncated


def collect(entry, verbose=True):
    code = entry["code"]
    out = os.path.join(OUT_DIR, code)
    ryakusho = entry.get("略称", "")

    if verbose:
        print("  %s を巡回中…" % entry["名称"])
    pages, truncated = crawl(entry, verbose)

    items, dup = {}, []
    for url, h in pages:
        for it in parse_items(h, url):
            if it["num"] in items:
                dup.append(it["num"])
                continue
            items[it["num"]] = it

    stats = {"created": 0, "updated": 0, "unchanged": 0}
    keep = set()
    meta_items = []
    for num, it in items.items():
        fname = safe_name(num) + ".md"
        keep.add(fname)
        stats[write_if_changed(os.path.join(out, "items", fname), render_item(it, ryakusho))] += 1
        meta_items.append({"num": num, "caption": it["caption"],
                           "heading": it["heading"], "file": "items/" + fname,
                           "source": it["url"]})

    removed = prune(os.path.join(out, "items"), keep)

    if len(items) != len(keep):
        raise RuntimeError("%s: 通達番号の異なる項目が同じファイル名になりました（%d / %d）"
                           % (code, len(items), len(keep)))

    meta_items.sort(key=lambda x: _sortkey(x["num"]))
    meta = {
        "code": code,
        "名称": entry["名称"],
        "略称": ryakusho,
        "root": entry["root"],
        "page_count": len(pages),
        "item_count": len(meta_items),
        "truncated": truncated,
        "出典": "国税庁 法令解釈通達",
        "利用条件": "通達は著作権法13条2号により著作権の目的とならない。国税庁サイトは政府標準利用規約に準拠。",
        "items": meta_items,
    }
    stats[write_if_changed(os.path.join(out, "_meta.json"),
                           json.dumps(meta, ensure_ascii=False, indent=2) + "\n")] += 1
    stats[write_if_changed(os.path.join(out, "README.md"), render_readme(meta))] += 1

    if verbose:
        print("    %s  %dページ / %d項目  作成%d 更新%d 不変%d%s%s" % (
            entry["名称"], len(pages), len(meta_items),
            stats["created"], stats["updated"], stats["unchanged"],
            "  削除%d" % len(removed) if removed else "",
            "  ★max_pages に達した（取りこぼしあり）" if truncated else ""))
        if dup:
            print("    重複した通達番号 %d 件（先に出た方を採用）: %s" % (
                len(dup), "、".join(sorted(set(dup))[:8])))
    return meta, stats, truncated


def _sortkey(num):
    """通達番号を人が見て自然な順に並べる。

    措置法通達には `42の10-1` `61の4(1)-1` のように数字と文字が混じる番号がある。
    要素ごとに (種別, 数値, 文字列) の組にしておかないと、int と str を比較して
    TypeError で落ちる。実際に一度落ちている。
    """
    key = []
    for part in re.split(r"[-の]", num):
        m = re.match(r"(\d*)(.*)", part)
        digits, rest = m.group(1), m.group(2)
        key.append((0 if digits else 1, int(digits) if digits else 0, rest))
    return key


def render_readme(meta):
    L = ["# %s" % meta["名称"], ""]
    L.append("| | |")
    L.append("|---|---|")
    L.append("| 略称 | %s |" % meta["略称"])
    L.append("| 項目数 | %d |" % meta["item_count"])
    L.append("| 取得ページ数 | %d |" % meta["page_count"])
    L.append("| 目次 | %s |" % meta["root"])
    L.append("")
    L.append("> 出典: 国税庁 法令解釈通達。取得日時は git のコミット日時を参照すること。")
    L.append("> 通達は著作権法13条2号により著作権の目的とならない。")
    L.append("")
    if meta["truncated"]:
        L.append("> [!warning] max_pages に達したため、取りこぼしがある可能性があります。")
        L.append("")
    L.append("| 通達番号 | 見出し | 収録箇所 | ファイル |")
    L.append("|---|---|---|---|")
    for it in meta["items"]:
        L.append("| %s%s | %s | %s | [%s](%s) |" % (
            meta["略称"], it["num"],
            (it["caption"] or "").replace("|", "／"),
            (it["heading"] or "").replace("|", "／"),
            os.path.basename(it["file"]), it["file"]))
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 全体

def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return [e for e in json.load(f)["tsutatsu"] if e.get("有効")]


def write_index():
    """tsutatsu/*/_meta.json を全部読んで索引を作り直す。"""
    rows = []
    if os.path.isdir(OUT_DIR):
        for code in sorted(os.listdir(OUT_DIR)):
            p = os.path.join(OUT_DIR, code, "_meta.json")
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                m = json.load(f)
            rows.append({"code": m["code"], "名称": m["名称"], "略称": m["略称"],
                         "item_count": m["item_count"], "path": "tsutatsu/%s/" % m["code"]})
    idx = {
        "_説明": "収集済みの法令解釈通達の一覧。取得日時は記録しない（git のコミット日時が取得日時）。",
        "_出典": "国税庁 法令解釈通達 https://www.nta.go.jp/law/tsutatsu/menu.htm",
        "_法的根拠": "通達は著作権法13条2号により著作権の目的とならない。国税庁サイトは政府標準利用規約に準拠。",
        "item_total": sum(r["item_count"] for r in rows),
        "tsutatsu": rows,
    }
    return write_if_changed(os.path.join(OUT_DIR, "index.json"),
                            json.dumps(idx, ensure_ascii=False, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser(description="国税庁サイトから法令解釈通達を収集する")
    ap.add_argument("--code", action="append", help="通達コードを指定（複数可）")
    ap.add_argument("--summary-file", help="項目数の変わった通達名を書き出す")
    args = ap.parse_args()

    entries = load_config()
    if args.code:
        entries = [e for e in entries if e["code"] in args.code]
        if not entries:
            print("該当する通達がありません: " + ", ".join(args.code))
            return 2

    print("収集対象: %d 通達" % len(entries))
    metas, total, failed, changed = [], {"created": 0, "updated": 0, "unchanged": 0}, [], []
    for e in entries:
        try:
            meta, stats, _ = collect(e)
            metas.append(meta)
            for k in total:
                total[k] += stats[k]
            if stats["created"] or stats["updated"]:
                changed.append(meta["名称"])
        except Exception as ex:                      # noqa: BLE001
            print("  !! %s の収集に失敗: %s" % (e["名称"], ex))
            failed.append(e["名称"])

    # 索引は「今回収集した分」ではなく「ディスク上にある全部」から作る。
    # --code で1つだけ収集したときに、他の通達が索引から消えてしまうため。
    write_index()

    print("\n完了  作成 %d / 更新 %d / 不変 %d" % (total["created"], total["updated"], total["unchanged"]))
    if args.summary_file:
        with open(args.summary_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("、".join(changed) if changed else "")
    if failed:
        print("失敗: " + "、".join(failed))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

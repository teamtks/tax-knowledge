#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国税不服審判所「公表裁決事例要旨」を収集する。

裁決は、税務署の処分を審判所が見直した結論である。
**どういう事実なら否認され、どういう事実なら取り消されたか**が分かる。
決算レビューで「この処理は調査で何を言われるか」を考えるときの、一番近い材料になる。

取り込むもの
------------
    saiketsu/<税法>/<分類コード>.md   分類ごとに要旨を並べたもの（審判所のページと同じ単位）
    saiketsu/<税法>/README.md         税法ごとの目次
    saiketsu/index.json               要旨1件ずつの一覧（題・裁決日・事例集の巻頁・全文URL）

**要旨は審判所が作った要約である。**結論の根拠を引用するときは、全文（公表裁決事例）を確かめること。
全文が公開されているものは index.json の「全文」に URL を残してある。
`python collectors/fetch.py page <全文URL>` で保管庫に取り込める。

使い方
------
    python collectors/kfs_saiketsu.py           # 全税法
    python collectors/kfs_saiketsu.py 03 05     # 法人税法関係・消費税法関係
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _web as W                                            # noqa: E402

ROOT = W.ROOT
OUT = os.path.join(ROOT, "saiketsu")
TOP = "https://www.kfs.go.jp/service/MP/index.html"


def 税法一覧() -> list[tuple[str, str]]:
    h, f = W.get(TOP)
    out = []
    for u, t in W.links(h, f):
        m = re.search(r"/service/MP/(\d{2})/index\.html$", u)
        if m and (m.group(1), t) not in out:
            out.append((m.group(1), t))
    return out


def 分類一覧(code: str) -> list[tuple[str, str]]:
    h, f = W.get(f"https://www.kfs.go.jp/service/MP/{code}/index.html")
    out = []
    for u, t in W.links(h, f):
        if re.search(r"/service/MP/%s/\d{10}\.html$" % code, u) and u not in [x[0] for x in out]:
            out.append((u, t))
    return out


_要旨 = re.compile(
    r'<h2[^>]*id="(y\d+)"[^>]*>(.*?)</h2>(.*?)(?=<h2[^>]*id="y\d+"|<!--\s*InstanceEndRepeat\s*-->\s*</div>|\Z)',
    re.S | re.I)


_日付 = re.compile(r"(?:昭和|平成|令和)\s*\d+年\s*\d+月\s*\d+日")
# 題の末尾の「（…・棄却・平成26年12月8日裁決）」から結論を取る。
# 結論は「調査の処分が維持されたか（棄却）、取り消されたか」を示す。レビューで最も使う情報である。
_結論 = re.compile(r"・(棄却|却下|一部取消し|全部取消し|取消し|一部取消|全部取消|変更)[・）]")


def 分類を読む(url: str) -> dict:
    h, final = W.get(url)
    大 = re.search(r'<div class="gtitle">(.*?)</div>', h, re.S)
    小 = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    out = {"url": final, "大分類": W.strip_tags(大.group(1)) if 大 else "",
           "小分類": W.strip_tags(小.group(1)) if 小 else "", "要旨": []}
    # 1つの題（h2）の下に、裁決が2件以上並ぶことがある。id の無い h2 もある。
    # したがって h2 ではなく <div class="article"> を1件の単位とし、題は直前の h2 から取る。
    題, anchor, n = "", "", 0
    for m in re.finditer(r'<h2[^>]*?(?:id="(y\d+)")?[^>]*>(.*?)</h2>|<div class="article">(.*?)</div>',
                         h, re.S | re.I):
        if m.group(2) is not None:
            題 = W.strip_tags(m.group(2))
            anchor = m.group(1) or anchor
            continue
        a = m.group(3)
        n += 1
        point = re.search(r'<p class="article_point">(.*?)</p>', a, re.S)
        date = re.search(r'<p class="article_date">(.*?)</p>', a, re.S)
        全文 = ""
        for href, _t in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', a, re.S):
            if "/JP/" in href or "/service/JP" in href:
                全文 = urllib.parse.urljoin(final, href)
        本文 = re.sub(r'<p class="article_(?:point|date)">.*?</p>', "", a, flags=re.S)
        pt = W.strip_tags(point.group(1)) if point else ""
        # 古い事例は article_date に、新しい事例は「▼ 平成26年12月8日裁決」のリンクに日付がある
        日 = W.strip_tags(date.group(1)) if date else ""
        if not 日:
            m2 = _日付.search(pt) or _日付.search(題)
            日 = m2.group(0) if m2 else ""
        結 = _結論.search(題)
        out["要旨"].append({
            "anchor": anchor, "連番": n, "題": 題,
            "事例集": pt if "事例集" in pt else "",
            "裁決日": 日.replace("裁決", "").strip(),
            "結論": 結.group(1) if 結 else "",
            "要旨": W.strip_tags(本文), "全文": 全文})
    return out


def 書く(code: str, 税法: str, page: dict) -> tuple[str, list[dict]]:
    pid = re.search(r"/(\d{10})\.html$", page["url"]).group(1)
    L = [f"# 公表裁決事例要旨　{税法}　{page['大分類']} ＞ {page['小分類']}", "",
         W.出典(page["url"], "国税不服審判所"), "",
         "> [!note] 要旨は審判所が作成した要約である。結論の根拠を引用するときは全文を確かめること。", ""]
    rows = []
    for y in page["要旨"]:
        L += [f"## {y['題']}", "",
              f"- 裁決日: **{y['裁決日'] or '—'}**" + (f"　結論: **{y['結論']}**" if y.get("結論") else ""),
              f"- 出典: {y['事例集'] or '—'}",
              f"- 全文: {y['全文'] or '（公開なし）'}",
              f"- 原典: {page['url']}#{y['anchor']}", "",
              y["要旨"], ""]
        rows.append({"id": f"{code}/{pid}-{y['連番']:02d}", "税法": 税法,
                     "分類": f"{page['大分類']} ＞ {page['小分類']}", "題": y["題"],
                     "裁決日": y["裁決日"], "結論": y.get("結論", ""), "事例集": y["事例集"], "全文": y["全文"],
                     "path": f"saiketsu/{code}/{pid}.md", "url": f"{page['url']}#{y['anchor']}"})
    st = W.write_if_changed(os.path.join(OUT, code, f"{pid}.md"), "\n".join(L) + "\n")
    return st, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*")
    ap.add_argument("--summary-file")
    a = ap.parse_args()
    idx_p = os.path.join(OUT, "index.json")
    idx = {}
    if os.path.exists(idx_p):
        with open(idx_p, encoding="utf-8") as f:
            idx = {r["id"]: r for r in json.load(f).get("要旨", [])}
    changed = []
    for code, 税法 in 税法一覧():
        if a.codes and code not in a.codes:
            continue
        cats = 分類一覧(code)
        数 = {"作成": 0, "更新": 0, "不変": 0, "失敗": 0}
        rows_all, keep = [], set()
        for u, _t in cats:
            try:
                page = 分類を読む(u)
                st, rows = 書く(code, 税法, page)
                数[st] += 1
                rows_all += rows
                keep.add(os.path.basename(u).replace(".html", ".md"))
            except Exception as ex:                         # noqa: BLE001
                数["失敗"] += 1
                print(f"  !! {u}: {ex}")
        d = os.path.join(OUT, code)
        if 数["失敗"] == 0 and os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".md") and f != "README.md" and f not in keep:
                    os.remove(os.path.join(d, f))
        for k in [k for k in idx if k.startswith(code + "/")]:
            del idx[k]
        for r in rows_all:
            idx[r["id"]] = r
        # 税法ごとの目次
        L = [f"# 公表裁決事例要旨　{税法}", "", W.出典(f"https://www.kfs.go.jp/service/MP/{code}/index.html", "国税不服審判所"), "",
             f"{len(rows_all)}件。", ""]
        cur = None
        for r in rows_all:
            if r["分類"] != cur:
                cur = r["分類"]
                L += ["", f"## {cur}", ""]
            L.append(f"- [{r['題'][:80]}]({os.path.basename(r['path'])})　{r['裁決日']}")
        W.write_if_changed(os.path.join(d, "README.md"), "\n".join(L) + "\n")
        print(f"{税法} ({code}): 分類{len(cats)} 要旨{len(rows_all)}  作成{数['作成']} 更新{数['更新']} 不変{数['不変']} 失敗{数['失敗']}")
        changed.append(税法)
    W.write_json_if_changed(idx_p, {
        "_説明": "国税不服審判所 公表裁決事例要旨の一覧。全文が公開されているものは「全文」にURLがある。",
        "_出典": TOP, "要旨": sorted(idx.values(), key=lambda r: r["id"])})
    if a.summary_file:
        with open(a.summary_file, "w", encoding="utf-8") as f:
            f.write("、".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())

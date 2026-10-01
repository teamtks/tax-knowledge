#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""税務大学校「税務訴訟資料」（課税関係判決）の目次を収集する。

税務訴訟資料は、国が当事者となった課税関係の判決を年ごとに収録したものである。
判決は裁決よりも先例としての重みがある。

取り込むもの
------------
    hanrei/index.json          全判決の目次（順号・裁判所・事件名・判決日・結果・上訴・PDF）
    hanrei/sosho/<年>.md       年ごとの目次
    hanrei/sosho/<年>/<順号>.md 本文（必要になったものだけ。collectors/fetch.py sosho <順号>）

**本文は常備しない。**1件1〜6MBのPDFで、年150件前後ある。全件を取ると年に数百MBになり、
リポジトリが重くなるわりに、使うのは一部である。
目次は全年分を常備し、本文は必要になったときに順号で取る。取ったものは wanted.json に載り、
以後は定期収集で保たれる。

使い方
------
    python collectors/nta_sosho.py
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _web as W                                            # noqa: E402

ROOT = W.ROOT
OUT = os.path.join(ROOT, "hanrei")
TOP = "https://www.nta.go.jp/about/organization/ntc/soshoshiryo/kazei/index.htm"

# 事件名から税目の見当をつける。目次には税目の欄が無いため。
_税目 = [("消費税", "消費税"), ("地方消費税", "消費税"), ("法人税", "法人税"), ("所得税", "所得税"),
         ("源泉", "源泉所得税"), ("相続税", "相続税"), ("贈与税", "贈与税"), ("登録免許税", "登録免許税"),
         ("印紙税", "印紙税"), ("酒税", "酒税"), ("関税", "関税"), ("差押", "徴収"), ("滞納", "徴収"),
         ("国家賠償", "国家賠償"), ("還付金", "還付"), ("更正の請求", "更正の請求"),
         ("情報公開", "情報公開")]


def _税目推定(事件: str) -> str:
    hit = [v for k, v in _税目 if k in 事件]
    return "・".join(dict.fromkeys(hit)) or "—"


def 年一覧() -> list[tuple[str, str, str]]:
    h, f = W.get(TOP)
    out = []
    for u, t in W.links(h, f):
        m = re.search(r"/kazei/(\d{4})/index\.htm$", u)
        if m:
            号 = re.search(r"第(\d+)号", t)
            out.append((m.group(1), u, 号.group(1) if 号 else ""))
    return out


def 年を読む(年: str, url: str, 号: str) -> list[dict]:
    h, f = W.get(url)
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", h, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 6:
            continue
        a = re.search(r'href="([^"]+\.pdf)"', tds[0])
        if not a:
            continue
        順号 = W.strip_tags(tds[0])
        事件 = W.strip_tags(tds[2])
        rows.append({"順号": 順号, "年": 年, "号": 号,
                     "裁判所": W.strip_tags(tds[1]).replace("地方", "地裁").replace("高等", "高裁").replace("最高", "最高裁"),
                     "事件": 事件, "判決日": W.strip_tags(tds[3]),
                     "結果": W.strip_tags(tds[4]), "上訴": W.strip_tags(tds[5]),
                     "税目": _税目推定(事件),
                     "pdf": W.urllib.parse.urljoin(f, a.group(1))})
    return rows


def main() -> int:
    allrows = []
    for 年, url, 号 in 年一覧():
        rows = 年を読む(年, url, 号)
        allrows += rows
        L = [f"# 税務訴訟資料 {年}年判決分（第{号}号）", "", W.出典(url, "国税庁 税務大学校"), "",
             "本文は常備していない。必要なものは `python collectors/fetch.py sosho <順号>` で取り込む。", "",
             "| 順号 | 裁判所 | 判決日 | 結果 | 上訴 | 税目（推定） | 事件名 |",
             "|---|---|---|---|---|---|---|"]
        for r in rows:
            本文 = os.path.exists(os.path.join(OUT, "sosho", 年, f"{r['順号']}.md"))
            順 = f"[{r['順号']}]({年}/{r['順号']}.md)" if 本文 else r["順号"]
            L.append(f"| {順} | {r['裁判所']} | {r['判決日']} | {r['結果']} | {r['上訴']} | {r['税目']} | {r['事件']} |")
        W.write_if_changed(os.path.join(OUT, "sosho", f"{年}.md"), "\n".join(L) + "\n")
        print(f"{年}: {len(rows)}件")
    W.write_json_if_changed(os.path.join(OUT, "index.json"), {
        "_説明": "税務訴訟資料（課税関係判決）の目次。本文は collectors/fetch.py sosho <順号> で取り込む。",
        "_出典": TOP, "判決": allrows})
    print(f"計 {len(allrows)}件")
    return 0


if __name__ == "__main__":
    sys.exit(main())

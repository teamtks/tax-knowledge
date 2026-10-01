#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国税庁「タックスアンサー」を収集する。

タックスアンサーは、制度の概要を平易に説明したものである。
**条文・通達の代わりにはならない。**根拠に使うのは、そこに書かれた「根拠法令等」のほうである。
用途は、制度の全体像を掴むことと、根拠法令等をたどる入口にすることである。

> [!danger] 基準日に注意
> 各ページは「[令和◯年◯月◯日現在法令等]」の時点で書かれている。
> 例: No.5408（少額減価償却資産）は令和7年4月1日現在のまま、上限を30万円と書いている。
> 現行は令和8年4月1日施行の改正で40万円である。**基準日より後の改正は反映されていない。**

取り込むもの
------------
    taxanswer/<分野>/<番号>.md
    taxanswer/index.json        番号・題・分野・基準日・根拠法令等

使い方
------
    python collectors/nta_taxanswer.py
    python collectors/nta_taxanswer.py hojin shohi
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _web as W                                            # noqa: E402

ROOT = W.ROOT
OUT = os.path.join(ROOT, "taxanswer")
INDEX = "https://www.nta.go.jp/taxes/shiraberu/taxanswer/code/index.htm"
_基準日 = re.compile(r"\[((?:令和|平成)\s*\d+年\s*\d+月\s*\d+日)現在法令等\]")


def 一覧() -> list[tuple[str, str, str, str]]:
    h, f = W.get(INDEX)
    out, seen = [], set()
    for u, t in W.links(h, f):
        m = re.search(r"/taxanswer/([a-z_]+)/(\d+(?:_\d+)?)\.htm$", u)
        if m and u not in seen:
            seen.add(u)
            out.append((m.group(1), m.group(2), u, t))
    return out


def 読む(分野: str, 番号: str, url: str) -> dict:
    h, final = W.get(url)
    題 = W.title(h)
    text = W.strip_tags(W.nta_body(h))
    # パンくずと重複した題を落とす。本文は基準日の行から始まる
    m = _基準日.search(text)
    基準日 = re.sub(r"\s", "", m.group(1)) if m else ""
    if m:
        text = text[m.start():]
    text = re.sub(r"\n{3,}", "\n\n", text)
    for s in ("対象税目", "概要", "対象者または対象物", "手続", "計算方法", "要件", "適用時期",
              "根拠法令等", "関連リンク", "関連コード", "お問い合わせ先"):
        text = re.sub(rf"\n{s}\n", f"\n\n## {s}\n", text)
    根拠 = ""
    mm = re.search(r"## 根拠法令等\n(.*?)(?:\n## |\Z)", text, re.S)
    if mm:
        根拠 = re.sub(r"\s+", " ", mm.group(1)).strip()
    税目 = ""
    mt = re.search(r"## 対象税目\n\s*(.+)", text)
    if mt:
        税目 = mt.group(1).strip()
    md = "\n".join([
        f"# タックスアンサー {題}", "",
        f"- 基準日: **{基準日 or '記載なし'}** 現在の法令等",
        f"- 対象税目: {税目}",
        f"- 原典: {final}", "",
        "> [!warning] タックスアンサーは条文・通達の代わりにならない",
        "> 根拠に使うのは「根拠法令等」の条文・通達である。**基準日より後の改正は反映されていない**ことがある。",
        "", W.出典(final), "", text, ""])
    path = os.path.join(OUT, 分野, f"{番号}.md")
    st = W.write_if_changed(path, md)
    return {"番号": 番号, "分野": 分野, "題": 題, "基準日": 基準日, "税目": 税目, "根拠法令等": 根拠[:300],
            "path": os.path.relpath(path, ROOT).replace("\\", "/"), "url": final, "_状態": st}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("分野", nargs="*")
    a = ap.parse_args()
    rows, 数 = [], {"作成": 0, "更新": 0, "不変": 0, "失敗": 0}
    for 分野, 番号, url, _t in 一覧():
        if a.分野 and 分野 not in a.分野:
            continue
        try:
            r = 読む(分野, 番号, url)
            数[r.pop("_状態")] += 1
            rows.append(r)
        except Exception as ex:                             # noqa: BLE001
            数["失敗"] += 1
            print(f"  !! {url}: {ex}")
    W.write_json_if_changed(os.path.join(OUT, "index.json"), {
        "_説明": "国税庁 タックスアンサーの一覧。基準日は各ページが前提とする法令の時点。",
        "_出典": INDEX, "項目": sorted(rows, key=lambda r: (r["分野"], r["番号"]))})
    print(f"タックスアンサー {len(rows)}件  作成{数['作成']} 更新{数['更新']} 不変{数['不変']} 失敗{数['失敗']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

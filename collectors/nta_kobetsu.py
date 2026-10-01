#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国税庁「個別通達」を収集する。

基本通達（法基通・消基通など）は条文に沿った一般的な解釈だが、個別通達は
**特定の取引・制度についての取扱い**を定めたものである。
例: 「相当の地代を支払っている場合等の借地権等についての相続税及び贈与税の取扱いについて」
（昭和60年直資2-58）は、個人地主・法人借地人の借地権の扱いを決めている。
決算レビューの同族間取引で、基本通達だけでは答えが出ない場面で使う。

取り込むもの
------------
    kobetsu/<分野>/<ID>.md       1通達（複数ページなら1ページ1ファイル）
    kobetsu/index.json           一覧（題・分野・URL・パス）

「一部改正」の告知ページ（/kaisei/）は取らない。改正後の本文が各通達のページに反映されるためである。

使い方
------
    python collectors/nta_kobetsu.py
    python collectors/nta_kobetsu.py sozoku hojin
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _web as W                                            # noqa: E402
import fetch as F                                           # noqa: E402

ROOT = W.ROOT
OUT = os.path.join(ROOT, "kobetsu")
CONFIG = os.path.join(ROOT, "config", "kobetsu.json")


def 目次(menu: str) -> list[tuple[str, str]]:
    h, f = W.get(menu)
    out, seen = [], set()
    for u, t in W.links(h, f):
        if "/law/tsutatsu/kobetsu/" not in u or "/kaisei/" in u:
            continue
        if not re.search(r"/\d{4,8}/(?:\d{2}|index)\.html?$", u):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append((u, t))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("分野", nargs="*")
    a = ap.parse_args()
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    idx_p = os.path.join(OUT, "index.json")
    idx = {}
    if os.path.exists(idx_p):
        with open(idx_p, encoding="utf-8") as f:
            idx = {r["url"]: r for r in json.load(f).get("通達", [])}
    for m in cfg["目次"]:
        if not m.get("有効", True) or (a.分野 and m["分野"] not in a.分野):
            continue
        items = 目次(m["url"])
        書いた = 失敗 = 0
        for u, t in items:
            try:
                paths = F.page(u, 種別="個別通達", 名称=t, 記録=False)
                書いた += len(paths)
                idx[u] = {"題": t, "分野": m["名称"], "url": u, "paths": paths}
            except Exception as ex:                         # noqa: BLE001
                失敗 += 1
                print(f"  !! {u}: {ex}")
        print(f"{m['名称']}: 通達{len(items)} ページ{書いた} 失敗{失敗}")
    W.write_json_if_changed(idx_p, {
        "_説明": "国税庁 個別通達の一覧。",
        "_出典": "https://www.nta.go.jp/law/tsutatsu/menu.htm",
        "通達": sorted(idx.values(), key=lambda r: (r["分野"], r["url"]))})
    return 0


if __name__ == "__main__":
    sys.exit(main())

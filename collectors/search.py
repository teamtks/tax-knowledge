#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""保管庫を横断して検索する。

    python collectors/search.py 借地権 無償返還
    python collectors/search.py 少額減価償却 --種類 質疑応答事例,裁決
    python collectors/search.py 短期前払費用 --本文      # 題に無くても本文を探す

並び順は「題に語がすべて入っているもの」→「題・補足に一部」→「本文にだけ」。
種類の順（法令 → 通達 → 個別通達 → 質疑応答事例 → 裁決 → 判決 → タックスアンサー）は、
**根拠としての強さ**の順である。タックスアンサーは入口であって根拠ではない。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_強さ = {"法令": 0, "通達": 1, "個別通達": 2, "質疑応答事例": 3, "裁決": 4, "判決": 5,
         "タックスアンサー": 6, "その他": 7}


def _n(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("語", nargs="+")
    ap.add_argument("--種類", help="カンマ区切り（法令,通達,個別通達,質疑応答事例,裁決,判決,タックスアンサー）")
    ap.add_argument("--本文", action="store_true", help="本文も探す（遅い）")
    ap.add_argument("-n", type=int, default=30)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    p = os.path.join(ROOT, "catalog.json")
    if not os.path.exists(p):
        print("catalog.json がありません。python collectors/catalog.py を先に実行してください。")
        return 2
    with open(p, encoding="utf-8") as f:
        rows = json.load(f)["資料"]
    if a.種類:
        ok = set(a.種類.split(","))
        rows = [r for r in rows if r["種類"] in ok]
    語 = [_n(x) for x in a.語]
    hits = []
    for r in rows:
        題 = _n(r["題"] + " " + r.get("番号", ""))
        補 = _n(r.get("補足", "") + " " + r.get("出典名", ""))
        全題 = all(w in 題 for w in 語)
        一部 = sum(1 for w in 語 if w in 題 or w in 補)
        if 全題:
            score = 0
        elif 一部 == len(語):
            score = 1
        elif 一部:
            score = 2 + (len(語) - 一部)
        else:
            if not a.本文 or not r.get("path"):
                continue
            fp = os.path.join(ROOT, r["path"])
            if not os.path.exists(fp):
                continue
            with open(fp, encoding="utf-8") as f:
                t = _n(f.read())
            if not all(w in t for w in 語):
                continue
            score = 9
        hits.append((score, _強さ.get(r["種類"], 9), r))
    hits.sort(key=lambda x: (x[0], x[1]))
    if not hits:
        print("見つかりません。" + ("" if a.本文 else "--本文 を付けると本文も探します。"))
        print("保管庫に無ければ取りに行く: python collectors/fetch.py page <URL> / law <法令名>")
        return 1
    for score, _, r in hits[:a.n]:
        日 = f"［{r['基準日']}］" if r.get("基準日") else ""
        場所 = r["path"] or f"（本文未取得: {r.get('取得', '')}）"
        print(f"{r['種類']:<8} {r['出典名'][:10]:<10} {r.get('番号', '')[:14]:<14} {r['題'][:60]} {日}")
        print(f"         → {場所}")
    if len(hits) > a.n:
        print(f"…ほか {len(hits) - a.n}件（-n で増やせます）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

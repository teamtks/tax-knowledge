#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""保管庫の全資料を1つの索引にまとめる。

資料が数千件になると、「どこに何があるか」を知っていることが、資料を持っていることと同じくらい大事になる。
条文・通達・質疑応答事例・裁決・タックスアンサー・個別通達・判決を横断して引けるよう、
題・見出し・基準日・パスを1つのファイルに並べる。

    catalog.json   横断索引（collectors/search.py が読む）
    CATALOG.md     件数と、資料ごとの使い方

使い方
------
    python collectors/catalog.py
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _web as W                                            # noqa: E402

ROOT = W.ROOT


def _load(p):
    p = os.path.join(ROOT, p)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def 集める() -> list[dict]:
    out = []
    # 法令（条文の見出し）
    for mp in sorted(glob.glob(os.path.join(ROOT, "law", "*", "_meta.json"))):
        with open(mp, encoding="utf-8") as f:
            m = json.load(f)
        d = os.path.relpath(os.path.dirname(mp), ROOT).replace("\\", "/")
        for a in m.get("articles", []):
            out.append({"種類": "法令", "出典名": m.get("略称") or m["law_title"],
                        "番号": a["title"], "題": a.get("caption", ""),
                        "補足": a.get("breadcrumb", ""), "基準日": m.get("amendment_enforcement_date", ""),
                        "path": f"{d}/{a['file']}"})
    # 基本通達
    for mp in sorted(glob.glob(os.path.join(ROOT, "tsutatsu", "*", "_meta.json"))):
        with open(mp, encoding="utf-8") as f:
            m = json.load(f)
        d = os.path.relpath(os.path.dirname(mp), ROOT).replace("\\", "/")
        for it in m.get("items", []):
            out.append({"種類": "通達", "出典名": m.get("略称", m.get("名称", "")),
                        "番号": it["num"], "題": it.get("caption", ""), "補足": it.get("heading", ""),
                        "基準日": "", "path": f"{d}/{it['file']}"})
    j = _load("kobetsu/index.json")
    索引済, 親 = set(), {}
    for r in (j or {}).get("通達", []):
        索引済.update(r.get("paths", []))
        for p in r.get("paths", []):
            親.setdefault("-".join(os.path.basename(p).split("-")[:2]), r["題"])
        for p in r.get("paths", [])[:1]:
            out.append({"種類": "個別通達", "出典名": r["分野"], "番号": "", "題": r["題"],
                        "補足": "", "基準日": "", "path": p})
    # fetch.py page で個別に取り込んだページ（目次から1階層下のページなど）は索引に載らない。
    # 題（親の通達名を前に付ける）と、本文の見出し（「(資本的支出後の耐用年数)」など）で引けるようにする。
    for f in sorted(glob.glob(os.path.join(ROOT, "kobetsu", "*", "*.md"))):
        p = os.path.relpath(f, ROOT).replace("\\", "/")
        if p in 索引済:
            continue
        with open(f, encoding="utf-8") as fh:
            本文 = fh.read()
        題 = 本文.split("\n", 1)[0].lstrip("# ").strip()
        上 = 親.get("-".join(os.path.basename(f).split("-")[:2]), "")
        見出し = re.findall(r"^[（(]([^）)\n]{2,40})[）)]\s*$", 本文, re.M)
        out.append({"種類": "個別通達", "出典名": p.split("/")[1],
                    "番号": "", "題": (f"{上} / {題}" if 上 and 上 not in 題 else 題),
                    "補足": "、".join(見出し)[:600], "基準日": "", "path": p})
    j = _load("qa/index.json")
    for r in (j or {}).get("事例", []):
        out.append({"種類": "質疑応答事例", "出典名": r["税目"], "番号": r["id"], "題": r["題"],
                    "補足": f"{r['分類']}｜{r.get('関係法令通達', '')}", "基準日": r.get("基準日", ""),
                    "path": r["path"]})
    j = _load("taxanswer/index.json")
    for r in (j or {}).get("項目", []):
        out.append({"種類": "タックスアンサー", "出典名": r.get("税目", ""), "番号": r["番号"], "題": r["題"],
                    "補足": r.get("根拠法令等", ""), "基準日": r.get("基準日", ""), "path": r["path"]})
    j = _load("saiketsu/index.json")
    for r in (j or {}).get("要旨", []):
        out.append({"種類": "裁決", "出典名": r["税法"], "番号": r.get("事例集", ""), "題": r["題"],
                    "補足": f"{r['分類']}｜結論 {r.get('結論', '') or '—'}", "基準日": r.get("裁決日", ""),
                    "path": r["path"], "全文": r.get("全文", "")})
    j = _load("hanrei/index.json")
    for r in (j or {}).get("判決", []):
        本文 = f"hanrei/sosho/{r['年']}/{r['順号']}.md"
        out.append({"種類": "判決", "出典名": r["裁判所"], "番号": f"順号{r['順号']}", "題": r["事件"],
                    "補足": f"{r['税目']}｜{r['結果']}・{r['上訴']}", "基準日": r["判決日"],
                    "path": 本文 if os.path.exists(os.path.join(ROOT, 本文)) else "",
                    "取得": f"python collectors/fetch.py sosho {r['順号']}"})
    for p in sorted(glob.glob(os.path.join(ROOT, "nta_pages", "**", "*.md"), recursive=True)):
        with open(p, encoding="utf-8") as f:
            first = f.readline().lstrip("# ").strip()
        out.append({"種類": "その他", "出典名": "国税庁", "番号": "", "題": first, "補足": "",
                    "基準日": "", "path": os.path.relpath(p, ROOT).replace("\\", "/")})
    return out


def main() -> int:
    rows = 集める()
    W.write_json_if_changed(os.path.join(ROOT, "catalog.json"), {
        "_説明": "保管庫の横断索引。collectors/search.py で検索する。", "件数": len(rows), "資料": rows})
    from collections import Counter
    c = Counter(r["種類"] for r in rows)
    本文あり = sum(1 for r in rows if r["種類"] == "判決" and r["path"])
    L = ["<!-- このファイルは collectors/catalog.py が生成する。手で編集しないこと。 -->", "",
         "# 保管庫の中身", "",
         "| 種類 | 件数 | 置き場所 | 使い方 |", "|---|--:|---|---|",
         f"| 法令（条） | {c['法令']:,} | `law/` | 根拠の本体。**条文はここから引く** |",
         f"| 基本通達 | {c['通達']:,} | `tsutatsu/` | 条文の一般的な解釈 |",
         f"| 個別通達 | {c['個別通達']:,} | `kobetsu/` | 特定の取引の取扱い（借地権・評価など） |",
         f"| 質疑応答事例 | {c['質疑応答事例']:,} | `qa/` | 具体的な事実への当てはめ。**基準日を必ず見る** |",
         f"| タックスアンサー | {c['タックスアンサー']:,} | `taxanswer/` | 制度の概要と根拠法令等への入口。**根拠には使わない** |",
         f"| 公表裁決事例要旨 | {c['裁決']:,} | `saiketsu/` | 処分が維持されたか（棄却）取り消されたか |",
         f"| 判決（税務訴訟資料） | {c['判決']:,} | `hanrei/` | 目次は全件。本文は {本文あり}件（必要時に取得） |",
         f"| その他 | {c['その他']:,} | `nta_pages/` | 必要になって取り込んだページ |",
         "", f"**計 {len(rows):,}件**", "",
         "## 探し方", "",
         "```", "python collectors/search.py 借地権 無償返還", "python collectors/search.py 少額減価償却 --種類 質疑応答事例,裁決", "```", "",
         "## 無いものは取りに行く", "",
         "```",
         "python collectors/fetch.py page <国税庁・審判所のURL>   # 個別通達・質疑応答・裁決全文など",
         "python collectors/fetch.py law <法令名>                 # e-Gov から法令を丸ごと",
         "python collectors/fetch.py sosho <順号>                 # 判決の本文",
         "```", "",
         "取りに行ったものは `config/wanted.json` に記録され、毎週の定期収集で最新に保たれる。", ""]
    W.write_if_changed(os.path.join(ROOT, "CATALOG.md"), "\n".join(L))
    print(f"索引 {len(rows):,}件  " + "  ".join(f"{k}{v:,}" for k, v in c.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

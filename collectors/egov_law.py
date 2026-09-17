#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e-Gov法令API v2 から法令を取得し、条単位の Markdown として保存する。

設計上の約束
------------
1. このスクリプトは AI を使わない。取得と整形だけを行う。
   したがって GitHub Actions のスケジュール実行だけで完結し、人もAIも介入しない。

2. 標準ライブラリのみを使う。pip install を不要にして、
   各職員PC・GitHub Actions のどちらでも同じ結果が出るようにする。

3. 取得日時をファイルに書き込まない。git のコミット日時が取得日時である。
   本文が変わっていないファイルは書き換えない（履歴を無駄に太らせないため）。

4. 要約も解釈も一切行わない。保存するのは条文原文だけである。
   AIが書いた要約を人の検証なしに保存してはならない、という方針に基づく。

5. 出力は毎回ゼロから組み立てる。したがって生成物が消えても次の実行で元に戻る。
   誤って条文ファイルを削除しても、収集が一度走れば復旧する。

出力
----
    law/<law_id>/_meta.json                 法令ID・題名・法令番号・リビジョンID・施行日・条一覧
    law/<law_id>/README.md                  条番号と見出しの索引（人が見る用）
    law/<law_id>/articles/At_<条番号>.md     本則の条文（1条1ファイル）
    law/<law_id>/suppl/<改正法令番号>.md     附則（改正法ごとに1ファイル）
    law/<law_id>/appendix.md                別表その他
    index.json                              収集した全法令の一覧

使い方
------
    python collectors/egov_law.py                    # config の有効な法令を全部収集
    python collectors/egov_law.py --law 340AC0000000034
    python collectors/egov_law.py --check            # 改正の有無だけ調べる（本文は取得しない）
    python collectors/egov_law.py --resolve 会社法    # 法令IDを調べる

出典
----
    e-Gov法令検索（デジタル庁） https://laws.e-gov.go.jp/
    法令は著作権法13条1号により著作権の目的とならない。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://laws.e-gov.go.jp/api/2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAW_DIR = os.path.join(ROOT, "law")
CONFIG = os.path.join(ROOT, "config", "laws.json")

USER_AGENT = "tax-knowledge-collector/1.0 (+https://laws.e-gov.go.jp/)"
SLEEP = 0.5          # APIへの連続アクセスを避ける
RETRY = 3


# ---------------------------------------------------------------- API

def api_get(path, params=None, timeout=120):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(RETRY):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:          # noqa: BLE001  ネットワーク起因は全部リトライ
            last = e
            if attempt < RETRY - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError("APIの取得に失敗しました: %s / %s" % (url, last))


def resolve_law_id(title):
    """法令名から law_id を引く。完全一致が1件でなければ候補を表示して終わる。"""
    d = api_get("/laws", {"law_title": title, "limit": 50})
    exact = [x for x in d["laws"] if x["revision_info"]["law_title"] == title]
    if len(exact) == 1:
        info = exact[0]
        return info["law_info"]["law_id"], info
    print("完全一致が %d 件でした。候補を表示します。" % len(exact))
    for x in d["laws"][:20]:
        print("  %s  %s  (%s 施行)" % (
            x["law_info"]["law_id"],
            x["revision_info"]["law_title"],
            x["revision_info"]["amendment_enforcement_date"]))
    return None, None


def fetch_revision_info(law_id, elm_hint=None):
    """本文をほぼ取らずにリビジョン情報だけを取る（改正検知用）。

    `/laws?law_id=` は law_data と異なる版を返すことがある（PreviousEnforced が混じる）ため
    使ってはならない。law_data に elm を付けると条1つ分の小さな応答になり、
    revision_info は本文全体を取ったときと同じものが入る（1法令あたり2〜5KB）。
    """
    params = {"response_format": "json"}
    if elm_hint:
        params["elm"] = elm_hint
    d = api_get("/law_data/%s" % law_id, params, timeout=60)
    return d["revision_info"]


def fetch_law_data(law_id):
    return api_get("/law_data/%s" % law_id, {"response_format": "json"})


# ---------------------------------------------------------------- JSONツリーの操作

def kids(node):
    if not isinstance(node, dict):
        return []
    return node.get("children") or []


def dict_kids(node, tag=None):
    out = []
    for c in kids(node):
        if isinstance(c, dict) and (tag is None or c.get("tag") == tag):
            out.append(c)
    return out


def find_first(node, tag):
    if isinstance(node, dict):
        if node.get("tag") == tag:
            return node
        for c in kids(node):
            r = find_first(c, tag)
            if r is not None:
                return r
    elif isinstance(node, list):
        for c in node:
            r = find_first(c, tag)
            if r is not None:
                return r
    return None


# ルビの読み（Rt）は本文に混ぜない。条文の文字列として不要なため。
_SKIP = {"Rt", "RubyTxt"}


def node_text(node):
    """ノード配下の文字列を連結する。未知のタグが来ても落ちないようにしてある。"""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    tag = node.get("tag")
    if tag in _SKIP:
        return ""
    if tag == "TableRow":
        cells = [node_text(c).strip() for c in dict_kids(node)]
        return "| " + " | ".join(cells) + " |\n"
    if tag in ("Table", "TableStruct"):
        return "\n" + "".join(node_text(c) for c in kids(node)) + "\n"
    # 号が2列組（左＝要件、右＝帰結）になっている条文がある。
    # そのまま連結すると「〜場合当該〜した日」と読めなくなるため、区切りを入れる。
    cols = dict_kids(node, "Column")
    if len(cols) >= 2:
        return "　→　".join(node_text(c).strip() for c in cols)
    return "".join(node_text(c) for c in kids(node))


def child_text(node, tag):
    c = None
    for k in dict_kids(node, tag):
        c = k
        break
    return node_text(c).strip() if c is not None else ""


# ---------------------------------------------------------------- Markdown 化

def render_items(parent, level=0):
    """号（Item）と イロハ（Subitem1〜3）を箇条書きにする。"""
    out = []
    specs = [("Item", "ItemTitle", "ItemSentence"),
             ("Subitem1", "Subitem1Title", "Subitem1Sentence"),
             ("Subitem2", "Subitem2Title", "Subitem2Sentence"),
             ("Subitem3", "Subitem3Title", "Subitem3Sentence")]
    if level >= len(specs):
        return out
    tag, title_tag, sent_tag = specs[level]
    for it in dict_kids(parent, tag):
        num = child_text(it, title_tag)
        body = child_text(it, sent_tag)
        indent = "  " * level
        head = (num + "　") if num else ""
        out.append("%s- %s%s" % (indent, head, body.strip()))
        out.extend(render_items(it, level + 1))
    return out


def render_paragraph(p):
    out = []
    cap = child_text(p, "ParagraphCaption")
    if cap:
        out.append("**%s**" % cap)
        out.append("")
    num = child_text(p, "ParagraphNum")
    body = child_text(p, "ParagraphSentence").strip()
    prefix = ("**%s**　" % num) if num else ""
    if body or prefix:
        out.append(prefix + body)
        out.append("")
    items = render_items(p)
    if items:
        out.extend(items)
        out.append("")
    # 表・様式など、項の直下にぶら下がるその他の構造
    for c in dict_kids(p):
        if c.get("tag") in ("TableStruct", "List", "FigStruct", "StyleStruct"):
            t = node_text(c).strip()
            if t:
                out.append(t)
                out.append("")
    return out


def render_article(art, breadcrumb=""):
    title = child_text(art, "ArticleTitle") or ("第%s条" % art.get("attr", {}).get("Num", ""))
    cap = child_text(art, "ArticleCaption")
    out = ["# %s%s" % (title, ("　" + cap) if cap else ""), ""]
    if breadcrumb:
        out.append("> %s" % breadcrumb)
        out.append("")
    for p in dict_kids(art, "Paragraph"):
        out.extend(render_paragraph(p))
    if not dict_kids(art, "Paragraph"):
        t = node_text(art).strip()
        if t:
            out.append(t)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def walk_articles(node, trail=None, acc=None):
    """本則を歩いて (条ノード, 編章節のパンくず) を集める。"""
    if acc is None:
        acc = []
    if trail is None:
        trail = []
    if not isinstance(node, dict):
        return acc
    tag = node.get("tag")
    if tag == "Article":
        acc.append((node, " > ".join(trail)))
        return acc
    label_tags = {"Part": "PartTitle", "Chapter": "ChapterTitle",
                  "Section": "SectionTitle", "Subsection": "SubsectionTitle",
                  "Division": "DivisionTitle"}
    if tag in label_tags:
        t = child_text(node, label_tags[tag])
        trail = trail + ([t] if t else [])
    for c in dict_kids(node):
        walk_articles(c, trail, acc)
    return acc


# ---------------------------------------------------------------- ファイル入出力

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(s, fallback="untitled"):
    s = _BAD.sub("_", (s or "").strip())
    s = s.strip(" .")
    return s or fallback


def write_if_changed(path, text):
    """本文が同じなら書かない。git の履歴を無駄に増やさないため。"""
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
    """削除された条（改正で削られた条）のファイルを消す。"""
    removed = []
    if not os.path.isdir(dirpath):
        return removed
    for name in sorted(os.listdir(dirpath)):
        if name not in keep:
            os.remove(os.path.join(dirpath, name))
            removed.append(name)
    return removed


# ---------------------------------------------------------------- 1法令の収集

def collect_law(entry, verbose=True):
    law_id = entry["law_id"]
    out_dir = os.path.join(LAW_DIR, law_id)
    meta_path = os.path.join(out_dir, "_meta.json")

    old_rev = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                old_rev = json.load(f).get("law_revision_id")
        except Exception:
            old_rev = None

    data = fetch_law_data(law_id)
    li = data["law_info"]
    ri = data["revision_info"]
    body = find_first(data["law_full_text"], "LawBody")
    if body is None:
        raise RuntimeError("LawBody が見つかりません: %s" % law_id)

    stats = {"created": 0, "updated": 0, "unchanged": 0}

    # --- 本則の条文 ---
    main = find_first(body, "MainProvision")
    articles_meta = []
    keep_articles = set()
    if main is not None:
        for art, trail in walk_articles(main):
            num = art.get("attr", {}).get("Num", "")
            # 条番号には "136:137"（第百三十六条から第百三十七条まで＝削除条のまとめ）のように
            # コロンを含むものがある。Windows ではファイル名に使えないため必ず通す。
            fname = "At_%s.md" % safe_name(num.replace(":", "-"), "unknown")
            keep_articles.add(fname)
            rel = "articles/" + fname
            st = write_if_changed(os.path.join(out_dir, "articles", fname),
                                  render_article(art, trail))
            stats[st] += 1
            articles_meta.append({
                "num": num,
                "title": child_text(art, "ArticleTitle"),
                "caption": child_text(art, "ArticleCaption"),
                "elm": "Mp-At_%s" % num,
                "file": rel,
                "breadcrumb": trail,
            })

    # --- 附則（改正法ごとに1ファイル） ---
    suppl_meta = []
    keep_suppl = set()
    seen = {}
    for sp in dict_kids(body, "SupplProvision"):
        attr = sp.get("attr", {})
        amend = attr.get("AmendLawNum", "")
        label = child_text(sp, "SupplProvisionLabel")
        base = safe_name(amend or "制定附則")
        n = seen.get(base, 0)
        seen[base] = n + 1
        fname = base + (".md" if n == 0 else "_%d.md" % (n + 1))
        keep_suppl.add(fname)

        head = ["# %s" % (label or "附則"), ""]
        if amend:
            head.append("> 改正法令: %s" % amend)
        if attr.get("Extract") == "true":
            head.append("> ※ この附則は抜粋である（e-Gov上の Extract 属性）")
        if len(head) > 2:
            head.append("")
        parts = list(head)
        for art in [a for a, _ in walk_articles(sp)]:
            parts.append(render_article(art))
            parts.append("")
        if not [a for a, _ in walk_articles(sp)]:
            t = node_text(sp).strip()
            if t:
                parts.append(t)
        text = "\n".join(parts).rstrip() + "\n"
        st = write_if_changed(os.path.join(out_dir, "suppl", fname), text)
        stats[st] += 1
        suppl_meta.append({"amend_law_num": amend, "label": label,
                           "file": "suppl/" + fname})

    # --- 別表その他（本則・附則・目次以外の全て） ---
    other = []
    for c in dict_kids(body):
        if c.get("tag") in ("MainProvision", "SupplProvision", "TOC", "LawTitle", "EnactStatement"):
            continue
        t = node_text(c).strip()
        if t:
            other.append("## %s %s" % (c.get("tag"), c.get("attr", {}).get("Num", "")))
            other.append("")
            other.append(t)
            other.append("")
    if other:
        text = "\n".join(["# 別表・様式その他", ""] + other).rstrip() + "\n"
        stats[write_if_changed(os.path.join(out_dir, "appendix.md"), text)] += 1
        has_appendix = True
    else:
        p = os.path.join(out_dir, "appendix.md")
        if os.path.exists(p):
            os.remove(p)
        has_appendix = False

    # --- 改正で削除された条のファイルを消す ---
    removed = prune(os.path.join(out_dir, "articles"), keep_articles)
    removed += prune(os.path.join(out_dir, "suppl"), keep_suppl)

    # --- 整合検査（条文を無言で落とさないための歯止め） ---
    # 条番号に使えない文字が混じるなどでファイルが作られなかった場合、
    # 黙って条文が欠けたまま完了してしまう。ここで必ず止める。
    if len(articles_meta) != len(keep_articles):
        raise RuntimeError(
            "%s: 条番号の異なる条が同じファイル名になりました（条 %d / ファイル名 %d）。"
            % (law_id, len(articles_meta), len(keep_articles)))
    for sub, expected in (("articles", len(keep_articles)), ("suppl", len(keep_suppl))):
        d = os.path.join(out_dir, sub)
        actual = len(os.listdir(d)) if os.path.isdir(d) else 0
        if actual != expected:
            raise RuntimeError(
                "%s: %s のファイル数が合いません（期待 %d / 実際 %d）。"
                "条番号に使えない文字が含まれている可能性があります。"
                % (law_id, sub, expected, actual))

    # --- メタ情報 ---
    meta = {
        "law_id": law_id,
        "law_title": ri.get("law_title"),
        "略称": entry.get("略称", ""),
        "law_num": li.get("law_num"),
        "promulgation_date": li.get("promulgation_date"),
        "law_revision_id": ri.get("law_revision_id"),
        "amendment_enforcement_date": ri.get("amendment_enforcement_date"),
        "amendment_promulgate_date": ri.get("amendment_promulgate_date"),
        "amendment_law_num": ri.get("amendment_law_num"),
        "amendment_law_title": ri.get("amendment_law_title"),
        "current_revision_status": ri.get("current_revision_status"),
        "article_count": len(articles_meta),
        "suppl_count": len(suppl_meta),
        "has_appendix": has_appendix,
        "articles": articles_meta,
        "suppl": suppl_meta,
    }
    stats[write_if_changed(meta_path, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")] += 1

    # --- 人が見る索引 ---
    stats[write_if_changed(os.path.join(out_dir, "README.md"), render_law_readme(meta))] += 1

    changed = (old_rev != meta["law_revision_id"])
    if verbose:
        print("  %s (%s)  条 %d / 附則 %d  作成%d 更新%d 不変%d%s%s" % (
            meta["law_title"], law_id, len(articles_meta), len(suppl_meta),
            stats["created"], stats["updated"], stats["unchanged"],
            "  ★改正あり" if changed and old_rev else "",
            "  削除%d" % len(removed) if removed else ""))
    return meta, stats, changed, removed


def render_law_readme(meta):
    L = []
    L.append("# %s" % meta["law_title"])
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append("| 法令番号 | %s |" % meta["law_num"])
    L.append("| 法令ID | `%s` |" % meta["law_id"])
    L.append("| 公布日 | %s |" % meta["promulgation_date"])
    L.append("| **この版の施行日** | **%s** |" % meta["amendment_enforcement_date"])
    L.append("| 直近改正 | %s（%s） |" % (meta.get("amendment_law_title") or "―",
                                          meta.get("amendment_law_num") or "―"))
    L.append("| リビジョンID | `%s` |" % meta["law_revision_id"])
    L.append("| 本則の条数 | %d |" % meta["article_count"])
    L.append("| 附則 | %d件 |" % meta["suppl_count"])
    L.append("")
    L.append("> 出典: [e-Gov法令検索](https://laws.e-gov.go.jp/law/%s)（デジタル庁）" % meta["law_id"])
    L.append("> 取得日時は git のコミット日時を参照すること。ファイルには記録しない。")
    L.append("")
    L.append("## 本則")
    L.append("")
    L.append("| 条 | 見出し | ファイル |")
    L.append("|---|---|---|")
    for a in meta["articles"]:
        L.append("| %s | %s | [%s](%s) |" % (
            a["title"] or a["num"],
            (a["caption"] or "").replace("|", "／"),
            os.path.basename(a["file"]), a["file"]))
    L.append("")
    if meta["suppl"]:
        L.append("## 附則")
        L.append("")
        L.append("| 改正法令 | ファイル |")
        L.append("|---|---|")
        for s in meta["suppl"]:
            L.append("| %s | [%s](%s) |" % (
                (s["amend_law_num"] or s["label"] or "制定時").replace("|", "／"),
                os.path.basename(s["file"]), s["file"]))
        L.append("")
    if meta["has_appendix"]:
        L.append("## その他")
        L.append("")
        L.append("- [別表・様式その他](appendix.md)")
        L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 全体

def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    return [e for e in cfg["laws"] if e.get("有効") and e.get("law_id")]


def cmd_check(entries):
    """本文を取らずに改正の有無だけ調べる。"""
    print("改正検知（本文は取得しません）")
    changed = []
    for e in entries:
        law_id = e["law_id"]
        meta_path = os.path.join(LAW_DIR, law_id, "_meta.json")
        old = None
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                old = json.load(f)
        if old is None:
            print("  %-22s 未取得" % e["法令名"])
            changed.append((e, None, None, None))
            continue
        # 取得済みの条番号を1つ借りて、応答を小さくする
        elm = old["articles"][0]["elm"] if old.get("articles") else None
        ri = fetch_revision_info(law_id, elm)
        cur = ri.get("law_revision_id")
        if old.get("law_revision_id") != cur:
            print("  %-22s ★改正あり  施行 %s → %s  (%s)" % (
                e["法令名"], old.get("amendment_enforcement_date"),
                ri.get("amendment_enforcement_date"),
                ri.get("amendment_law_title") or ri.get("amendment_law_num") or "―"))
            changed.append((e, old.get("law_revision_id"), cur, ri))
        else:
            print("  %-22s 変更なし    (施行 %s)" % (e["法令名"], ri.get("amendment_enforcement_date")))
        time.sleep(SLEEP)
    print("\n改正のあった法令: %d / %d" % (len(changed), len(entries)))
    return changed


def load_all_metas():
    """law/*/_meta.json を全部読む。

    索引は「今回収集した分」ではなく「ディスク上にある全部」から作る。
    --law で1法令だけ収集したときに、他の法令が索引から消えてしまうため。
    """
    out = []
    if not os.path.isdir(LAW_DIR):
        return out
    for law_id in sorted(os.listdir(LAW_DIR)):
        p = os.path.join(LAW_DIR, law_id, "_meta.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def write_index(metas):
    idx = {
        "_説明": "収集済みの法令一覧。取得日時は記録しない（git のコミット日時が取得日時）。",
        "_出典": "e-Gov法令検索（デジタル庁） https://laws.e-gov.go.jp/",
        "laws": [{
            "law_id": m["law_id"],
            "law_title": m["law_title"],
            "略称": m.get("略称", ""),
            "law_num": m["law_num"],
            "law_revision_id": m["law_revision_id"],
            "amendment_enforcement_date": m["amendment_enforcement_date"],
            "article_count": m["article_count"],
            "path": "law/%s/" % m["law_id"],
        } for m in sorted(metas, key=lambda x: x["law_id"])],
    }
    return write_if_changed(os.path.join(ROOT, "index.json"),
                            json.dumps(idx, ensure_ascii=False, indent=2) + "\n")


def render_root_readme(metas):
    L = []
    L.append("<!-- このファイルは collectors/egov_law.py が生成する。手で編集しないこと。 -->")
    L.append("")
    L.append("| 法令 | 略称 | 施行日 | 本則条数 | 索引 |")
    L.append("|---|---|---|--:|---|")
    for m in sorted(metas, key=lambda x: x["law_id"]):
        L.append("| %s | %s | %s | %d | [開く](law/%s/) |" % (
            m["law_title"], m.get("略称", ""), m["amendment_enforcement_date"],
            m["article_count"], m["law_id"]))
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description="e-Gov法令API v2 から法令を収集する")
    ap.add_argument("--law", action="append", help="law_id を指定（複数可）")
    ap.add_argument("--check", action="store_true", help="改正の有無だけ調べる")
    ap.add_argument("--resolve", help="法令名から law_id を調べる")
    ap.add_argument("--summary-file", help="リビジョンが変わった法令名をこのファイルに書く（コミットメッセージ用）")
    args = ap.parse_args()

    if args.resolve:
        law_id, info = resolve_law_id(args.resolve)
        if law_id:
            print('"%s": "%s",   # %s 施行' % (
                args.resolve, law_id, info["revision_info"]["amendment_enforcement_date"]))
        return 0

    entries = load_config()
    if args.law:
        entries = [e for e in entries if e["law_id"] in args.law] or \
                  [{"法令名": i, "law_id": i, "略称": ""} for i in args.law]

    if args.check:
        changed = cmd_check(entries)
        return 1 if changed else 0

    print("収集対象: %d 法令" % len(entries))
    metas, total, failed, amended = [], {"created": 0, "updated": 0, "unchanged": 0}, [], []
    for e in entries:
        try:
            meta, stats, changed, _ = collect_law(e)
            metas.append(meta)
            for k in total:
                total[k] += stats[k]
            if changed:
                amended.append(meta["law_title"])
        except Exception as ex:                      # noqa: BLE001
            print("  !! %s の取得に失敗: %s" % (e["法令名"], ex))
            failed.append(e["法令名"])
        time.sleep(SLEEP)

    all_metas = load_all_metas()
    if all_metas:
        write_index(all_metas)
        write_if_changed(os.path.join(ROOT, "LAWS.md"), render_root_readme(all_metas))

    print("\n完了  作成 %d / 更新 %d / 不変 %d" % (total["created"], total["updated"], total["unchanged"]))
    if amended:
        print("リビジョンが変わった法令: " + "、".join(amended))
    if args.summary_file:
        with open(args.summary_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("、".join(amended) if amended else "")
    if failed:
        print("失敗: " + "、".join(failed))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

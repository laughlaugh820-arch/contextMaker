#!/usr/bin/env python3
"""analysis/ の抽出結果を、LLM のコンテキストに貼れる Markdown に組み直す。

出力先は context/。作品カード、作家プロファイル、技法カタログの3系統を作る。
context/guide/ は手で書くものなので、このスクリプトは触らない。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = REPO_ROOT / "analysis"
CONTEXT = REPO_ROOT / "context"

SPARK = "▁▂▃▄▅▆▇█"

# 作家プロファイルで平均を取る指標
PROFILE_KEYS = [
    ("sentence_length_mean", "平均文長", "字"),
    ("sentence_length_stdev", "文長のばらつき", ""),
    ("dialogue_ratio", "会話率", ""),
    ("kanji_ratio", "漢字率", ""),
    ("comma_per_sentence", "1文あたり読点", "個"),
    ("dash_per_1000", "ダッシュ密度", "/千字"),
    ("ellipsis_per_1000", "三点リーダ密度", "/千字"),
    ("exclaim_per_1000", "感嘆符密度", "/千字"),
]


def sparkline(values: list[float]) -> str:
    """数値の並びを1行のグラフにする。緊張曲線を一目で比べるため。"""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return SPARK[0] * len(values)
    return "".join(SPARK[min(int((v - lo) / (hi - lo) * (len(SPARK) - 1)), len(SPARK) - 1)]
                   for v in values)


def load_analyses() -> list[dict]:
    works = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ANALYSIS.glob("*/*.json"))]
    if not works:
        sys.exit("analysis/ が空。先に tools/analyze.py を実行すること。")
    return sorted(works, key=lambda w: (w["author"], w["title"]))


def safe_name(text: str) -> str:
    return text.replace("/", "_").replace(" ", "_")


# --------------------------------------------------------------------------- 作品カード

def work_card(work: dict) -> str:
    s = work["stats"]
    curve = work["tension_curve"]
    lines = [
        f"# {work['author']}『{work['title']}』",
        "",
        f"- 作品ID {work['work_id']} / [図書カード]({work['card']}) / 本文 `{work['path']}/plain.txt`",
        f"- {s['characters']:,}字、{s['paragraphs']:,}段落、{s['sentences']:,}文、{s['orthography']}",
        "",
        "## 文体の数値",
        "",
        "| 指標 | 値 |",
        "| --- | --- |",
        f"| 平均文長 | {s['sentence_length_mean']}字（中央値 {s['sentence_length_median']}、"
        f"ばらつき {s['sentence_length_stdev']}、最長 {s['sentence_length_max']}） |",
        f"| 会話率 | {s['dialogue_ratio']}（括弧付きの語り {s['quoted_narration_ratio']}、"
        f"括弧1箇所の平均 {s['quote_span_mean']}字） |",
        f"| 漢字率 | {s['kanji_ratio']} / ひらがな {s['hiragana_ratio']} / カタカナ {s['katakana_ratio']} |",
        f"| 1文あたり読点 | {s['comma_per_sentence']}個 |",
        f"| ダッシュ / 三点リーダ | {s['dash_per_1000']} / {s['ellipsis_per_1000']}（千字あたり） |",
        f"| 感嘆符 / 疑問符 | {s['exclaim_per_1000']} / {s['question_per_1000']}（千字あたり） |",
        "",
    ]

    if work["headings"]:
        lines += ["## 章構成", "",
                  "| 章 | 文字数 |", "| --- | --- |"]
        lines += [f"| {h['label']} | {h.get('characters', 0):,} |" for h in work["headings"][:40]]
        if len(work["headings"]) > 40:
            lines.append(f"| …他 {len(work['headings']) - 40} 章 | |")
        lines.append("")

    if curve:
        lines += [
            "## 緊張曲線（本文を10等分）",
            "",
            "| 区間 | " + " | ".join(str(c["segment"]) for c in curve) + " |",
            "| --- | " + " | ".join("---" for _ in curve) + " |",
            "| 平均文長 | " + " | ".join(str(c["sentence_length_mean"]) for c in curve) + " |",
            "| 会話率 | " + " | ".join(str(c["dialogue_ratio"]) for c in curve) + " |",
            "| 感嘆・疑問 | " + " | ".join(str(c["exclaim_question_per_1000"]) for c in curve) + " |",
            "",
            f"- 文長 `{sparkline([c['sentence_length_mean'] for c in curve])}`",
            f"- 会話 `{sparkline([c['dialogue_ratio'] for c in curve])}`",
            f"- 感嘆・疑問 `{sparkline([c['exclaim_question_per_1000'] for c in curve])}`",
            "",
        ]

    lines += ["## 書き出し", ""]
    lines += [f"> {t}" for t in work["opening"]]
    lines += ["", "## 結び", ""]
    lines += [f"> {t}" for t in work["closing"]]
    lines.append("")

    if work["similes"]:
        lines += ["## 比喩", ""]
        for item in work["similes"][:12]:
            lines.append(f"- [{'/'.join(item['markers'])}] {item['text']}")
        lines.append("")

    if work["reduplications"]:
        top = list(work["reduplications"].items())[:20]
        lines += ["## 畳語", "",
                  "、".join(f"{w}（{c}）" for w, c in top), ""]

    if work["repetitions"]:
        lines += ["## 畳みかけ", ""]
        for rep in work["repetitions"][:5]:
            lines.append(f"- 「{rep['head']}…」が {rep['count']} 文続く")
            for sample in rep["sample"][:2]:
                lines.append(f"  > {sample}")
        lines.append("")

    if work["vocabulary"]:
        lines += ["## 頻出語", "",
                  "、".join(f"{w}（{c}）" for w, c in list(work["vocabulary"].items())[:25]), ""]

    return "\n".join(lines)


# --------------------------------------------------------------------------- 作家プロファイル

def author_profile(author: str, works: list[dict], corpus_mean: dict) -> str:
    lines = [f"# {author}", "",
             f"収録 {len(works)} 作品。",
             "",
             "## 作品",
             "",
             "| 作品 | 字数 | 平均文長 | 会話率 | 正書法 |",
             "| --- | --- | --- | --- | --- |"]
    for w in sorted(works, key=lambda w: -w["stats"]["characters"]):
        s = w["stats"]
        lines.append(f"| [{w['title']}](../works/{safe_name(author)}_{safe_name(w['title'])}.md) "
                     f"| {s['characters']:,} | {s['sentence_length_mean']} "
                     f"| {s['dialogue_ratio']} | {s['orthography']} |")
    lines += ["", "## 文体の平均と、コーパス全体との差", "",
              "| 指標 | この作家 | 全体 | 差 |", "| --- | --- | --- | --- |"]
    for key, label, unit in PROFILE_KEYS:
        mine = statistics.fmean(w["stats"][key] for w in works)
        whole = corpus_mean[key]
        diff = mine - whole
        arrow = "↑" if diff > whole * 0.15 else ("↓" if diff < -whole * 0.15 else "→")
        lines.append(f"| {label} | {mine:.2f}{unit} | {whole:.2f}{unit} | {arrow} {diff:+.2f} |")
    lines.append("")

    lines += ["## 緊張曲線", "", "| 作品 | 文長 | 会話 | 感嘆・疑問 |", "| --- | --- | --- | --- |"]
    for w in works:
        curve = w["tension_curve"]
        if not curve:
            continue
        lines.append(f"| {w['title']} "
                     f"| `{sparkline([c['sentence_length_mean'] for c in curve])}` "
                     f"| `{sparkline([c['dialogue_ratio'] for c in curve])}` "
                     f"| `{sparkline([c['exclaim_question_per_1000'] for c in curve])}` |")
    lines.append("")

    lines += ["## 書き出し", ""]
    for w in works:
        if w["opening"]:
            lines.append(f"- 『{w['title']}』 {w['opening'][0]}")
    lines += ["", "## 結び", ""]
    for w in works:
        if w["closing"]:
            lines.append(f"- 『{w['title']}』 {w['closing'][-1]}")
    lines.append("")

    similes = [s for w in works for s in w["similes"]]
    if similes:
        random.Random(0).shuffle(similes)
        lines += ["## 比喩", ""]
        lines += [f"- [{'/'.join(s['markers'])}] {s['text']}" for s in similes[:20]]
        lines.append("")

    redup: defaultdict[str, int] = defaultdict(int)
    for w in works:
        for word, count in w["reduplications"].items():
            redup[word] += count
    if redup:
        top = sorted(redup.items(), key=lambda kv: -kv[1])[:30]
        lines += ["## 畳語", "", "、".join(f"{w}（{c}）" for w, c in top), ""]

    return "\n".join(lines)


# --------------------------------------------------------------------------- 技法カタログ

def similes_catalog(works: list[dict]) -> str:
    by_marker: defaultdict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for w in works:
        for item in w["similes"]:
            for marker in item["markers"]:
                by_marker[marker].append((w["author"], w["title"], item["text"]))

    lines = ["# 比喩カタログ", "",
             "直喩の目印を含む文を、目印の種類ごとに集めたもの。",
             "抽出は文字列の一致によるので、慣用句が混ざることがある。", ""]
    for marker in sorted(by_marker, key=lambda m: -len(by_marker[m])):
        items = by_marker[marker]
        lines += [f"## 「{marker}」型（{len(items)} 例）", ""]
        random.Random(1).shuffle(items)
        for author, title, text in items[:25]:
            lines.append(f"- {text}  \n  <small>{author}『{title}』</small>")
        lines.append("")
    return "\n".join(lines)


def openings_catalog(works: list[dict]) -> str:
    lines = ["# 書き出しカタログ", "",
             "各作品の最初の3文。長さ・視点・時制の入り方を比べるために並べたもの。", ""]
    for w in sorted(works, key=lambda w: w["stats"]["sentence_length_mean"]):
        s = w["stats"]
        lines += [f"## {w['author']}『{w['title']}』",
                  f"<small>1文目 {len(w['opening'][0]) if w['opening'] else 0}字 / "
                  f"作品の平均文長 {s['sentence_length_mean']}字 / {s['orthography']}</small>", ""]
        lines += [f"> {t}" for t in w["opening"]]
        lines.append("")
    return "\n".join(lines)


def closings_catalog(works: list[dict]) -> str:
    lines = ["# 結びカタログ", "", "各作品の最後の3文。", ""]
    for w in works:
        lines += [f"## {w['author']}『{w['title']}』", ""]
        lines += [f"> {t}" for t in w["closing"]]
        lines.append("")
    return "\n".join(lines)


def onomatopoeia_catalog(works: list[dict]) -> str:
    total: defaultdict[str, int] = defaultdict(int)
    owners: defaultdict[str, set] = defaultdict(set)
    for w in works:
        for word, count in w["reduplications"].items():
            total[word] += count
            owners[word].add(w["author"])

    lines = ["# 畳語（擬音語・擬態語）カタログ", "",
             "ABAB 型・AABB 型の繰り返しを集めたもの。形態素解析を使わない近似なので、",
             "擬音語・擬態語でない畳語も混ざる。", "",
             "| 語 | 総出現 | 使った作家 |", "| --- | --- | --- |"]
    for word, count in sorted(total.items(), key=lambda kv: -kv[1])[:150]:
        lines.append(f"| {word} | {count} | {'、'.join(sorted(owners[word]))} |")
    lines.append("")
    return "\n".join(lines)


def structure_catalog(works: list[dict]) -> str:
    lines = ["# 構成カタログ", "",
             "本文を10等分し、区間ごとの会話率・平均文長・感嘆疑問密度を並べたもの。",
             "`▁`が低く`█`が高い。各行は作品の中での相対値なので、作品間で高さは比べられない。", "",
             "| 作品 | 字数 | 章数 | 会話 | 文長 | 感嘆・疑問 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for w in sorted(works, key=lambda w: -w["stats"]["characters"]):
        curve = w["tension_curve"]
        if not curve:
            continue
        lines.append(
            f"| {w['author']}『{w['title']}』 | {w['stats']['characters']:,} "
            f"| {len(w['headings'])} "
            f"| `{sparkline([c['dialogue_ratio'] for c in curve])}` "
            f"| `{sparkline([c['sentence_length_mean'] for c in curve])}` "
            f"| `{sparkline([c['exclaim_question_per_1000'] for c in curve])}` |")
    lines += ["", "## 章の配分", ""]
    for w in works:
        if len(w["headings"]) >= 3:
            sizes = [h.get("characters", 0) for h in w["headings"]]
            lines.append(f"- {w['author']}『{w['title']}』 {len(sizes)}章 "
                         f"`{sparkline(sizes)}` "
                         f"（最短 {min(sizes):,} / 最長 {max(sizes):,}字）")
    lines.append("")
    return "\n".join(lines)


def index_page(works: list[dict], by_author: dict) -> str:
    total_chars = sum(w["stats"]["characters"] for w in works)
    lines = ["# context — 抽出結果のまとめ", "",
             f"{len(by_author)} 作家 {len(works)} 作品、{total_chars:,}字から抽出したもの。",
             "`tools/build_context.py` が生成する（`guide/` を除く）。", "",
             "## 読む順",
             "",
             "1. [guide/story_craft.md](guide/story_craft.md) — 文体の作法。手で書いたもの",
             "2. [guide/story_structure.md](guide/story_structure.md) — 展開の作法。手で書いたもの",
             "3. [techniques/](techniques/) — 技法ごとの用例集",
             "4. [authors/](authors/) — 作家ごとの文体プロファイル",
             "5. [works/](works/) — 作品ごとのカード",
             "",
             "## 技法カタログ",
             "",
             "- [比喩](techniques/similes.md)",
             "- [書き出し](techniques/openings.md)",
             "- [結び](techniques/closings.md)",
             "- [畳語（擬音語・擬態語）](techniques/onomatopoeia.md)",
             "- [構成](techniques/structure.md)",
             "",
             "## 作家",
             "",
             "| 作家 | 作品数 | 字数 | 平均文長 | 会話率 |",
             "| --- | --- | --- | --- | --- |"]
    for author, items in sorted(by_author.items(), key=lambda kv: -sum(
            w["stats"]["characters"] for w in kv[1])):
        chars = sum(w["stats"]["characters"] for w in items)
        lines.append(f"| [{author}](authors/{safe_name(author)}.md) | {len(items)} | {chars:,} "
                     f"| {statistics.fmean(w['stats']['sentence_length_mean'] for w in items):.1f} "
                     f"| {statistics.fmean(w['stats']['dialogue_ratio'] for w in items):.2f} |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 実行

def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(CONTEXT), help="出力先 (既定: context/)")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()

    works = load_analyses()
    by_author: defaultdict[str, list[dict]] = defaultdict(list)
    for w in works:
        by_author[w["author"]].append(w)

    corpus_mean = {key: statistics.fmean(w["stats"][key] for w in works)
                   for key, _, _ in PROFILE_KEYS}

    for w in works:
        write(out / "works" / f"{safe_name(w['author'])}_{safe_name(w['title'])}.md", work_card(w))
    for author, items in by_author.items():
        write(out / "authors" / f"{safe_name(author)}.md",
              author_profile(author, items, corpus_mean))

    write(out / "techniques" / "similes.md", similes_catalog(works))
    write(out / "techniques" / "openings.md", openings_catalog(works))
    write(out / "techniques" / "closings.md", closings_catalog(works))
    write(out / "techniques" / "onomatopoeia.md", onomatopoeia_catalog(works))
    write(out / "techniques" / "structure.md", structure_catalog(works))
    write(out / "README.md", index_page(works, by_author))

    print(f"{len(works)} 作品 / {len(by_author)} 作家分を {out.relative_to(REPO_ROOT)} に生成した")
    return 0


if __name__ == "__main__":
    sys.exit(main())

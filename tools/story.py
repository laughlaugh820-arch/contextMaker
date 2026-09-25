#!/usr/bin/env python3
"""抽出した技法をもとに、オリジナルの物語を書かせる。

  compose   プロンプトを組み立てて表示・保存する（APIを使わない）
  generate  組み立てたプロンプトを Claude に投げ、stories/ に保存する

compose は analysis/ の数値と context/guide/story_craft.md を材料にする。
コーパスから借りるのは文の設計・比喩の作り方・緊張の配置といった抽象的な層だけで、
固有名詞や筋は渡さない。模倣ではなくオリジナルを書かせるため。
"""

from __future__ import annotations

import argparse
import json
import re
import os
import random
import shutil
import subprocess
import time
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze as A  # noqa: E402  計測は抽出ツールと同じ関数で行う

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = REPO_ROOT / "analysis"
DISTRIBUTION = ANALYSIS / "corpus_distribution.json"
MODERN = ANALYSIS / "author_片岡義男.json"
# 片岡義男の作品のうち、小説でないもの（エッセイ）。文体の目標値から外す
NONFICTION = {"56823"}
GUIDE_DIR = REPO_ROOT / "context" / "guide"
# 書き手の矜持と理念（ペルソナ）。--persona で名前かパスを渡したときだけプロンプトに入る。
# 経歴や登場人物の設定ではなく「どういう考え方で書くか」。反映しない生成と一対比較で比べる
# （context/persona/README.md）
PERSONA_DIR = REPO_ROOT / "context" / "persona"
# 渡す順。文体の作法 → 展開の作法
GUIDE_FILES = ["story_craft.md", "story_structure.md"]

# --plot で選ぶ展開の型。詳細は context/guide/story_structure.md
PLOT_TYPES = {
    "一撃": "発端で状況を置き、承でその状況を掘り下げ、本文の8〜9割の位置で一度だけ決定的な転換"
            "（逆転・露見・事故・気づき・選択のいずれか）を起こし、転から末尾までは1割以内で閉じる。",
    "反復": "同じ種類の出来事を三度、少しずつ形を変えて繰り返す（外側→内側、物理→人→内面、"
            "軽い→重い）。三度目のあとに転換が来て、反復が積んだものが一気に意味を変える。"
            "転換は8〜9割の位置。",
    "露見": "語り手または視点人物が知らないことを、相手の語り（告白・手紙・証言）を通じて少しずつ知る。"
            "核心が明かされるのは6〜8割の位置で、そのあと語り手の受け止めを短く置いて閉じる。"
            "読者は視点人物と同じ速度で知る。",
    "枠": "外側の語り手が、内側の語り手の話を聞く二重構造。外枠は最初と最後に短く置き"
          "（合わせて2割以内）、内側の話が本体。内側の話の終わりが外枠に何かを残して閉じる。",
    "心境": "出来事はほとんど起こさない。語り手の感覚と気分の推移だけで進み、ただ一つの小さな行為"
            "（何かを置く、見る、買う）を本文の7〜9割の位置に置いて、それを転換として扱う。"
            "結は行為のあとの数文で閉じる。",
}
STORIES = REPO_ROOT / "stories"

MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Claude Code CLI 経由で書かせるときの追記。CLI は道具を使えるエージェントなので、
# ファイルを作りにいかず標準出力に本文を出すよう明示する。
CLI_SYSTEM_SUFFIX = ("\n\nファイルの作成や編集は一切せず、"
                     "作品の本文だけをそのまま出力すること。")


def load_works() -> list[dict]:
    works = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(p for p in ANALYSIS.glob("*/*.json") if p.parent.name.isdigit())]
    if not works:
        sys.exit("analysis/ が空。先に tools/analyze.py を実行すること。")
    return works


def author_summary(author: str, works: list[dict]) -> str:
    mine = [w for w in works if w["author"] == author]
    if not mine:
        names = "、".join(sorted({w["author"] for w in works}))
        sys.exit(f"作家「{author}」はコーパスにない。\n収録: {names}")

    mean = lambda key: statistics.fmean(w["stats"][key] for w in mine)
    spread = mean("sentence_length_stdev") / mean("sentence_length_mean")
    lines = [
        f"### 参照する文体: {author}（収録 {len(mine)} 作品）",
        "",
        f"- 平均文長 {mean('sentence_length_mean'):.0f}字、ばらつき {spread:.2f}"
        f"（1.0を超えると緩急が激しい、0.5前後だと均質）",
        f"- 会話率 {mean('dialogue_ratio'):.2f}、1文あたり読点 {mean('comma_per_sentence'):.1f}個",
        f"- 漢字率 {mean('kanji_ratio'):.2f}",
        f"- ダッシュ {mean('dash_per_1000'):.1f} / 三点リーダ {mean('ellipsis_per_1000'):.1f}（千字あたり）",
        "",
        "この数値そのものを目標にする必要はないが、"
        "文の長さの振れ方と記号の使い方はこの傾向に寄せること。",
        "",
    ]
    return "\n".join(lines)


def simile_examples(works: list[dict], author: str | None, count: int, rng: random.Random) -> str:
    pool = [(w["author"], s["text"]) for w in works
            if not author or w["author"] == author
            for s in w["similes"]]
    if not pool:
        return ""
    rng.shuffle(pool)
    lines = ["### 比喩の作り方（コーパスからの実例）", "",
             "喩える先が手で触れられる具体物になっている例を挙げる。",
             "この文をそのまま使ってはいけない。作り方だけを見ること。", ""]
    lines += [f"- {text}" for _, text in pool[:count]]
    lines.append("")
    return "\n".join(lines)


def structure_reference(works: list[dict], work_id: str | None) -> str:
    if not work_id:
        return ""
    match = next((w for w in works if w["work_id"] == work_id), None)
    if match is None:
        sys.exit(f"作品ID {work_id} は analysis/ にない。")
    curve = match["tension_curve"]
    if not curve:
        return ""
    return "\n".join([
        "### 参照する緊張の配置",
        "",
        "ある既存作品から、会話率と感嘆・疑問の密度の推移だけを取り出したもの。"
        "筋や題材は一切関係がない。緩急の置き方だけを真似ること。",
        "",
        "| 区間 | " + " | ".join(str(c["segment"]) for c in curve) + " |",
        "| --- | " + " | ".join("---" for _ in curve) + " |",
        "| 会話率 | " + " | ".join(f"{c['dialogue_ratio']:.2f}" for c in curve) + " |",
        "| 感嘆・疑問 | " + " | ".join(f"{c['exclaim_question_per_1000']:.1f}" for c in curve) + " |",
        "| 平均文長 | " + " | ".join(f"{c['sentence_length_mean']:.0f}" for c in curve) + " |",
        "",
    ])


def list_personas() -> list[str]:
    """context/persona/ にある人物の名前。説明（README）と雛形（_ で始まるもの）は除く。"""
    return sorted(p.stem for p in PERSONA_DIR.glob("*.md")
                  if p.stem != "README" and not p.stem.startswith("_"))


def resolve_persona(spec: str | None) -> tuple[str, str] | None:
    """--persona の指定を (名前, 本文) に解決する。名前なら context/persona/<名前>.md、
    そうでなければファイルのパスとして読む。無ければ、ある名前を挙げて止まる。"""
    if not spec:
        return None
    candidates = [PERSONA_DIR / f"{spec}.md", Path(spec).expanduser()]
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        names = "、".join(list_personas()) or "（なし）"
        sys.exit(f"ペルソナ「{spec}」が見つからない。\n"
                 f"context/persona/ にある名前: {names}\n"
                 f"自分で書いたものは Markdown のパスで渡す（雛形は context/persona/_template.md）。")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        sys.exit(f"ペルソナ {path} が空。")
    return path.stem, text


def persona_section(persona: tuple[str, str]) -> str:
    """書き手の矜持と理念を渡す節。資料の見出し（1行目の # 行）は名前と重なるので落とす。"""
    name, text = persona
    body = "\n".join(l for l in text.split("\n") if not l.startswith("# ")).strip()
    return "\n".join([
        "# 書き手",
        "",
        f"この作品は、次の矜持と理念を持つ書き手が書く（{name}）。"
        "経歴や登場人物の設定ではなく、考え方の指定。",
        "",
        body,
        "",
        "矜持と理念は、説明ではなく選択に出す。",
        "",
        "- 書き手は本文に出てこない。「私はこう思う」と語らず、人物のどれかに代弁もさせない",
        "- 何を描き何を省くか、誰に寄るか、何を美しいと扱うか、どこで終えるかに出す",
        "- 「書かないこと」と「恥と思うこと」は守る。テーマがそれを求めても別の道を探す",
        "- 教訓や主張を書かない。理念は判断に出て、文には出ない",
        "",
    ])


SYSTEM_PROMPT = """あなたは日本語で短編小説を書く。

読者に読ませるための作品を書くのであって、技法の解説や制作意図の説明はしない。
与えられた作法は近代日本文学のコーパスから統計的に抽出したものだが、
数値を満たすことが目的ではない。作品として成立することが目的で、
作法はそのための手段として使う。"""


def build_prompt(args: argparse.Namespace, works: list[dict]) -> str:
    rng = random.Random(args.seed)
    guides = [(GUIDE_DIR / name) for name in GUIDE_FILES]
    if getattr(args, "novel", False):
        guides.append(LONG_GUIDE)
    guide = "\n\n---\n\n".join(g.read_text(encoding="utf-8") for g in guides if g.exists())

    parts = [
        "# 依頼",
        "",
        f"次のテーマで短編小説を書いてほしい。",
        "",
        f"**テーマ**: {args.theme}",
        f"**目標の長さ**: {args.length:,}字前後",
        "",
    ]
    persona = resolve_persona(getattr(args, "persona", None))
    if persona:
        parts.append(persona_section(persona))
    parts += [
        "# 参照資料",
        "",
        "以下は近代日本文学 18作家43作品から抽出した作法と実例。",
        "文章の設計に使うための材料で、筋や人物の供給源ではない。",
        "",
    ]
    if args.author:
        parts.append(author_summary(args.author, works))
    parts.append(simile_examples(works, args.author, args.similes, rng))
    parts.append(structure_reference(works, args.structure))

    if args.plot:
        parts += ["### 展開の型", "",
                  f"**{args.plot}型**で書く。{PLOT_TYPES[args.plot]}", ""]

    if guide:
        parts += ["### 作法", "", guide, ""]

    rules = [
        "**オリジナルであること**。参照資料に出てくる固有名詞（人名・地名・作品名）を使わない。"
        "既存作品の筋をなぞらない。借りるのは文の長さの設計、比喩の作り方、"
        "視点の移し方、会話の配分という抽象的な層だけ。",
        "**表記は新字新仮名**。歴史的仮名遣いや旧字体は使わない。",
        *(["**現代の口語で書く**。語彙も現代のものを使い、文語的な言い回し（である調の多用、古風な漢語）を避ける。"]
          if getattr(args, "baseline", None) == "modern" else []),
        f"**長さを守る**。{args.length:,}字前後で、{int(args.length * 0.9):,}字を下回らない。",
        "**書き出しの型を決める**。断定・情景・関係宣言のいずれかで入り、世界設定の説明から始めない。",
        "**結びは説明で閉じない**。事実を一つ置く、動作で示す、物や風景に視点を預ける、のいずれか。",
        "**結びは短く**。転換の帰結が出たあと、末尾までは本文の1割以内（5,000字なら数文〜2段落）。"
        "帰結のあとで状況や気持ちを振り返り直さない。",
        "**比喩は形式ではなく喩える先で決める**。「〜のように」で構わない。"
        "喩える先は手で触れられる具体物にし、密度は千字に1〜2つ、多くても3つまで。",
        "**緊張は記号ではなく出来事で作る**。感嘆符・疑問符・ダッシュ・三点リーダは使わなくてよい。"
        "使うなら密度を決めて一貫させる。",
    ]
    if persona:
        rules.append("**書き手の矜持と理念で書く**。上の一線と「書かないこと」を、"
                     "何を描くか・誰に寄るか・何を美しいと扱うか・どこで終えるかの選択に出す。"
                     "書き手が本文に顔を出したり、人物に理念を代弁させたりしない。")
    if args.avoid:
        rules.append("**次の題材・仕掛けは使わない**: " + "、".join(args.avoid) + "。"
                     "これらは同じテーマでモデルが最初に思いつく定型なので、別の核を探すこと。")
    parts += ["# 守ること", ""]
    parts += [f"{i}. {rule}" for i, rule in enumerate(rules, 1)]
    parts.append("")
    parts += [
        "# 出力の形式",
        "",
        "1行目にタイトルのみを書き、空行を1つ置いて本文を始める。",
        "見出し・注釈・あとがき・技法の説明は書かない。本文だけを書く。",
        "",
    ]
    return "\n".join(p for p in parts if p is not None)


# --------------------------------------------------------------------------- 節ごとの生成

# 各節が全体に占める割合。context/guide/story_structure.md §1 の配分に合わせる。
# 承は単位ごとに分けるので、ここでは承全体の割合を持つ。
SECTION_SHARES = {"発端": 0.10, "承": 0.70, "転": 0.12, "結": 0.05}
# 承をいくつの単位に割るか。字数で決める（ガイド §8: 3,000字なら二度まで、反復三度は5,000字から）
def unit_count(length: int) -> int:
    return 2 if length < 5000 else (3 if length < 9000 else 4)


OUTLINE_SCHEMA = """{
  "title": "題名",
  "premise": "一行で、誰が何を欠いているか",
  "sections": [
    {"name": "発端", "chars": 800, "content": "この節で起きること。3〜4文で具体的に"},
    {"name": "承1", "chars": 1400, "content": "…"},
    {"name": "承2", "chars": 1400, "content": "…"},
    {"name": "承3", "chars": 1400, "content": "…"},
    {"name": "転", "chars": 960, "content": "何が転換するか。類型も書く"},
    {"name": "結", "chars": 400, "content": "帰結と、どう閉じるか"}
  ],
  "direction": "承の単位が何の順に並ぶか（外→内、軽→重、下→上、など）",
  "knowledge_gap": "読者が知っていて人物が知らないこと（無ければ null）"
}"""


def section_plan(length: int) -> list[tuple[str, int]]:
    """節の名前と目標字数。承は単位に割る。"""
    units = unit_count(length)
    plan = [("発端", round(length * SECTION_SHARES["発端"]))]
    per_unit = round(length * SECTION_SHARES["承"] / units)
    plan += [(f"承{i + 1}", per_unit) for i in range(units)]
    plan.append(("転", round(length * SECTION_SHARES["転"])))
    plan.append(("結", round(length * SECTION_SHARES["結"])))
    return plan


def outline_prompt(args: argparse.Namespace, base: str) -> str:
    plan = section_plan(args.length)
    lines = [base, "", "# いまの依頼: 構成表だけを作る", "",
             "本文はまだ書かない。上の作法にしたがって、次の形の JSON だけを返す。",
             "節の名前と字数は指定どおりにし、`content` だけを埋める。", "",
             "| 節 | 目標字数 |", "| --- | --- |"]
    lines += [f"| {name} | {chars:,} |" for name, chars in plan]
    lines += ["", "```json", OUTLINE_SCHEMA, "```", "",
              "`sections` は上の表のとおりの名前と字数で並べること。",
              "各 `content` は、その節で実際に起きる出来事を3〜4文で具体的に書く。",
              "抽象的な要約（「主人公が苦悩する」）ではなく、誰が何をするかを書く。", ""]
    return "\n".join(lines)


def section_prompt(base: str, outline: dict, index: int, written: list[str]) -> str:
    sections = outline["sections"]
    current = sections[index]
    lines = [base, "", f"# いまの依頼: 「{current['name']}」の節だけを書く", "",
             f"題名『{outline.get('title', '')}』  {outline.get('premise', '')}", "",
             "## 全体の構成", "", "| 節 | 字数 | 内容 |", "| --- | --- | --- |"]
    for i, sec in enumerate(sections):
        mark = "← いまここ" if i == index else ("済" if i < index else "")
        lines.append(f"| {sec['name']} {mark} | {sec['chars']:,} | {sec['content']} |")
    if outline.get("direction"):
        lines.append("")
        lines.append(f"承の方向: {outline['direction']}")
    if outline.get("knowledge_gap"):
        lines.append(f"読者が知っていて人物が知らないこと: {outline['knowledge_gap']}")

    if written:
        lines += ["", "## ここまでに書かれた本文", "", "\n\n".join(written), ""]
        lines += ["上の続きを書く。すでに書いた文を繰り返さない。"]
    lines += ["", "## 守ること", "",
              f"- **{current['chars']:,}字前後**で書く。{int(current['chars'] * 0.9):,}字を下回らない",
              f"- この節（{current['name']}）の内容だけを書く。先の節の出来事を先取りしない",
              "- 題名、節の名前、見出し、説明は書かない。本文だけを出力する",
              "- 前の節から文体・語り手・時制を変えない",
              ]
    if index == len(sections) - 1:
        lines.append("- これが最後の節。結びは説明で閉じず、事実・動作・風景のいずれかで置く")
    lines.append("")
    return "\n".join(lines)


def parse_outline(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        sys.exit("構成表の JSON が返ってこなかった。")
    return json.loads(m.group(0))


def generate_sectioned(args: argparse.Namespace, base: str, call) -> tuple[str, dict]:
    """構成表を作ってから節ごとに書かせ、繋ぐ。長い作品で1回の応答が足りないときに使う。"""
    print("[構成表を作る]", file=sys.stderr)
    outline_text, usage = call(outline_prompt(args, base))
    outline = parse_outline(outline_text)
    plan = section_plan(args.length)
    # 節の名前と字数はこちらの指定を正とする（モデルが変えてくることがある）
    for sec, (name, chars) in zip(outline.get("sections", []), plan):
        sec["name"], sec["chars"] = name, chars
    if len(outline.get("sections", [])) != len(plan):
        sys.exit(f"構成表の節数が合わない（{len(outline.get('sections', []))} / {len(plan)}）")
    for sec in outline["sections"]:
        print(f"  {sec['name']:<4} {sec['chars']:>6,}字  {sec['content'][:50]}", file=sys.stderr)

    written: list[str] = []
    for i, sec in enumerate(outline["sections"]):
        text, usage = call(section_prompt(base, outline, i, written))
        body = text.strip()
        written.append(body)
        print(f"[{sec['name']}] {len(body):,}字（目標 {sec['chars']:,}）", file=sys.stderr)
    title = outline.get("title") or "無題"
    return title + "\n\n" + "\n\n".join(written), {**usage, "outline": outline}


# --------------------------------------------------------------------------- 長編モード

CHAPTER_NUMERAL = re.compile(r"^[一二三四五六七八九十]{1,3}$")
LONG_GUIDE = GUIDE_DIR / "long_structure.md"


def kanji_number(n: int) -> str:
    digits = "〇一二三四五六七八九"
    if n < 10:
        return digits[n]
    tens, ones = divmod(n, 10)
    return ("" if tens == 1 else digits[tens]) + "十" + (digits[ones] if ones else "")


def novel_chapter_count(length: int) -> int:
    """章の数。1章3,500字前後を目安にする（長編ガイドの章立ての作品は中央で1章9,380字だが、
    生成の1回の応答に収まる長さに抑える）。"""
    return min(20, max(6, round(length / 3500)))


NOVEL_OUTLINE_SCHEMA = """{
  "title": "題名",
  "premise": "一行で、誰が何を欠いているか",
  "chapters": [
    {"chars": 3300, "content": "この章で起きること。3〜4文で具体的に", "dialogue": "多い | 普通 | 少ない", "ending": "地の文 | 短文 | 会話"}
  ],
  "confrontation_chapter": 5,
  "turn_chapter": 7
}"""


def novel_outline_prompt(args: argparse.Namespace, base: str) -> str:
    n = novel_chapter_count(args.length)
    per = round(args.length / n)
    lines = [base, "", "# いまの依頼: 長編の章立て表だけを作る", "",
             "本文はまだ書かない。長編の組み立て（long_structure.md）にしたがって、次の形の JSON だけを返す。", "",
             f"- 章は **{n}章**。全体で {args.length:,}字前後",
             f"- 1章の長さは {per:,}字を中心に、{round(per * 0.65):,}〜{round(per * 1.35):,}字の範囲でばらつかせる。すべて同じ長さにしない",
             # 長編18本の「会話が最も多い章の位置」は章の始まりの位置で測っている（中央63%）。
             # 章番号は「本文の6割あたりから始まる章」になるよう int(n*0.6)+1 にする。
             # 初版は round(n*0.6) で、9章なら5章目（始まり45%）になり、基準の帯から外れた。
             f"- 人物同士がぶつかる章（会話が最も多くなる章）を、本文の6割あたりから始まる章"
             f"（{int(n * 0.6) + 1}章目）に置く。その章番号を confrontation_chapter に書く",
             f"- 決定的な転換が起きる章を、本文の8割あたりから始まる章（{int(n * 0.8) + 1}章目）に置く。"
             "その章番号を turn_chapter に書く",
             "- 最後の章は会話を減らし、語りで静かに閉じる（dialogue を「少ない」にする）",
             "- 章の終わり方（ending）は地の文と短文を中心にし、会話で切る章は多くても3割にする",
             "- 各章の content は、その章で実際に起きる出来事を、誰が何をするかで具体的に書く", "",
             "```json", NOVEL_OUTLINE_SCHEMA, "```", ""]
    return "\n".join(lines)


def normalize_chapter_lengths(chapters: list[dict], length: int) -> None:
    """章の字数を、合計が目標になるように整え、極端な長さを抑える。"""
    n = len(chapters)
    per = length / n
    raw = [min(max(int(c.get("chars") or per), per * 0.65), per * 1.35) for c in chapters]
    scale = length / sum(raw)
    for c, r in zip(chapters, raw):
        c["chars"] = int(round(r * scale / 10) * 10)


def novel_chapter_prompt(base: str, outline: dict, index: int, written: list[str]) -> str:
    chapters = outline["chapters"]
    current = chapters[index]
    lines = [base, "", f"# いまの依頼: 第{kanji_number(index + 1)}章だけを書く", "",
             f"題名『{outline.get('title', '')}』  {outline.get('premise', '')}", "",
             "## 章立て", "", "| 章 | 字数 | 会話 | 章の終わり | 内容 |", "| --- | --- | --- | --- | --- |"]
    for i, c in enumerate(chapters):
        mark = " ← いまここ" if i == index else (" 済" if i < index else "")
        lines.append(f"| {kanji_number(i + 1)}{mark} | {c['chars']:,} | {c.get('dialogue', '')} "
                     f"| {c.get('ending', '')} | {c.get('content', '')} |")
    if written:
        # 初版は直近12,000字だけを渡していて、『七番の席』で第一章の設定（父が辞めたのは秋）を
        # 第六章が知らずに「辞めた春」と書く矛盾が出た。40,000字までは全文を渡し、それを超える
        # 長さでは、設定の多くが決まる第一章の全文と直近20,000字を渡す。
        full = "\n\n".join(written)
        if len(full) <= 40000:
            context, label = full, "ここまでに書かれた本文"
        else:
            tail = full[-20000:]
            context = written[0] + "\n\n（中略）\n\n" + tail
            label = "ここまでに書かれた本文（第一章と直近の部分）"
        lines += ["", f"## {label}", "", context, "",
                  "上の続きを書く。すでに書いた文を繰り返さない。"
                  "人物の経歴、時期、場所など、すでに書いた事実と食い違わないようにする。"]
    lines += ["", "## 守ること", "",
              f"- **{current['chars']:,}字前後**で書く。{int(current['chars'] * 0.9):,}字を下回らない",
              f"- この章の内容だけを書く。先の章の出来事を先取りしない",
              f"- 会話の量は「{current.get('dialogue', '普通')}」、章の終わり方は「{current.get('ending', '地の文')}」にする",
              "- 章の番号、題名、見出し、説明は書かない。本文だけを出力する",
              "- 前の章から文体・語り手・時制を変えない"]
    if index == len(chapters) - 1:
        lines.append("- これが最後の章。会話を減らし、説明で閉じず、事実・動作・風景のいずれかで静かに終える")
    lines.append("")
    return "\n".join(lines)


def extend_prompt(base: str, chapter_text: str, target: int) -> str:
    return "\n".join([base, "", "# いまの依頼: 章を書き直して長さを整える", "",
                       f"次の章は目標 {target:,}字に対して {len(chapter_text):,}字しかない。"
                       f"筋・出来事・人物・章の終わり方は変えずに、描写と会話を厚くして {target:,}字前後に書き直す。",
                       "書き直した章の本文だけを出力する。", "", "## 章の本文", "", chapter_text, ""])


def generate_novel(args: argparse.Namespace, base: str, call) -> tuple[str, dict]:
    """章立て表を作ってから章ごとに書く。短い章は一度だけ書き直して長さを整える。"""
    print("[長編の章立て表を作る]", file=sys.stderr)
    outline_text, usage = call(novel_outline_prompt(args, base))
    outline = parse_outline(outline_text)
    chapters = outline.get("chapters") or []
    if len(chapters) < 3:
        sys.exit(f"章立て表の章が少なすぎる（{len(chapters)}章）")
    normalize_chapter_lengths(chapters, args.length)
    for i, c in enumerate(chapters):
        print(f"  {kanji_number(i + 1):>3} {c['chars']:>6,}字 会話{c.get('dialogue', '?')} 終{c.get('ending', '?')}  "
              f"{(c.get('content') or '')[:40]}", file=sys.stderr)

    written: list[str] = []
    for i, c in enumerate(chapters):
        text, usage = call(novel_chapter_prompt(base, outline, i, written))
        body = text.strip()
        if len(body) < c["chars"] * 0.8:
            longer, usage = call(extend_prompt(base, body, c["chars"]))
            if len(longer.strip()) > len(body):
                body = longer.strip()
        written.append(body)
        print(f"[第{kanji_number(i + 1)}章] {len(body):,}字（目標 {c['chars']:,}）", file=sys.stderr)

    title = outline.get("title") or "無題"
    parts = [title, ""]
    for i, body in enumerate(written):
        parts += [kanji_number(i + 1), "", body, ""]
    return "\n".join(parts).strip() + "\n", {**usage, "outline": outline}


# --------------------------------------------------------------------------- 計測と書き直し

# 生成後に測る指標と、目標に対して許す幅（下限比, 上限比）。
# 会話率だけは比ではなく絶対差で見る（0.05 の 1.3 倍は意味が無いので）。
CHECKS = [
    ("characters", "字数", "字", (0.9, 1.2)),
    ("sentence_length_mean", "平均文長", "字", (0.75, 1.3)),
    ("sentence_length_cv", "文長の振れ幅", "", (0.7, 1.4)),
    ("simile_per_1000", "直喩の密度（千字あたり）", "", (0.5, 1.6)),
    ("dialogue_ratio", "会話率", "", None),
]
DIALOGUE_TOLERANCE = 0.10

# 展開の型ごとに帯を差し替える。心境型は会話がほぼ無く、感覚描写（直喩）が濃い。
# 『檸檬』は会話率0.02・直喩1.8、『桜の樹の下には』は0.00・6.8。
# 現代の小説一般の目標値を当てると、『鉢の底』は会話率0.02を上げろ、直喩2.16を下げろと
# 書き直しを求められた（review/batch_2026-09-24）。会話は下限なし、直喩は上限を3倍まで広げる。
PLOT_BANDS = {
    "心境": {"dialogue_ratio": "no_floor", "simile_per_1000": (0.5, 3.0)},
}


def measure_story(text: str) -> dict:
    """生成物を抽出ツールと同じ基準で測る。1行目のタイトルは除く。"""
    body = text.strip().split("\n", 1)[1].strip() if "\n" in text.strip() else text.strip()
    # 長編モードの章見出し（「一」「十二」のような漢数字だけの行）は文として数えない
    body = "\n".join(l for l in body.split("\n") if not CHAPTER_NUMERAL.match(l.strip()))
    paragraphs = A.split_paragraphs(body)
    sentences = A.sentences_of(body)
    stats = A.basic_stats(body, paragraphs, sentences)
    similes = A.extract_similes(sentences, limit=10**6)
    return {
        "characters": len(body),
        "sentence_length_mean": stats["sentence_length_mean"],
        "sentence_length_cv": round(stats["sentence_length_stdev"] / stats["sentence_length_mean"], 2)
        if stats["sentence_length_mean"] else 0.0,
        "simile_per_1000": round(len(similes) / len(body) * 1000, 2) if body else 0.0,
        "simile_count": len(similes),
        "dialogue_ratio": stats["dialogue_ratio"],
    }


def targets_for(args: argparse.Namespace, works: list[dict]) -> dict:
    """目標値。--author があればその作家、なければ --baseline で選んだ群の中央値。

    fiction（既定）: library/ の小説（名作コーパス＋戦後の長編）
    modern:         片岡義男の小説（1970〜90年代、数値のみ保存）
    all:            ミラーの無作為2,000作品。随筆・評論を含むので文が長めに出る

    初版は all を既定にしていたが、ずれの主因が時代ではなくジャンル（随筆・評論の混入）だと
    分かったので、小説だけの群を既定にした（docs/ERA_STYLE.md）。
    """
    targets = {"characters": args.length}
    baseline = getattr(args, "baseline", None) or "fiction"
    if args.author:
        mine = [w["stats"] for w in works if w["author"] == args.author]
        source = f"{args.author} の平均"
        rows = mine
        agg = statistics.fmean
    elif baseline == "all" and DISTRIBUTION.exists():
        dist = json.loads(DISTRIBUTION.read_text(encoding="utf-8"))
        targets.update({
            "sentence_length_mean": dist["sentence_length_mean"]["median"],
            "sentence_length_cv": dist["sentence_length_cv"]["median"],
            "simile_per_1000": dist["simile_per_1000"]["median"],
            "dialogue_ratio": dist["dialogue_ratio"]["median"],
        })
        targets["source"] = f"無作為 {dist['n']:,} 作品の中央値（随筆・評論を含む）"
        return targets
    elif baseline == "modern" and MODERN.exists():
        rows = [w for w in json.loads(MODERN.read_text(encoding="utf-8"))["works"]
                if w["work_id"] not in NONFICTION]
        # author_*.json は作品ごとに sentence_length_cv を持つ
        source = f"片岡義男の小説 {len(rows)} 作品の中央値"
        agg = statistics.median
    else:
        rows = [w["stats"] for w in works]
        source = f"小説 {len(rows)} 作品の中央値"
        agg = statistics.median

    def cv(row: dict) -> float:
        if "sentence_length_cv" in row:
            return row["sentence_length_cv"]
        return row["sentence_length_stdev"] / row["sentence_length_mean"]

    targets.update({
        "sentence_length_mean": agg(r["sentence_length_mean"] for r in rows),
        "sentence_length_cv": agg(cv(r) for r in rows),
        "simile_per_1000": agg(r.get("simile_per_1000", 0) for r in rows),
        "dialogue_ratio": agg(r["dialogue_ratio"] for r in rows),
    })
    targets["source"] = source
    return targets


def check_story(metrics: dict, targets: dict, plot: str | None = None) -> list[dict]:
    """目標から外れた指標を返す。plot（展開の型）があれば PLOT_BANDS で帯を差し替える。"""
    violations = []
    overrides = PLOT_BANDS.get(plot or "", {})
    for key, label, unit, band in CHECKS:
        if key not in targets:
            continue
        current, target = metrics[key], targets[key]
        band = overrides.get(key, band)
        if band == "no_floor":
            low, high = 0.0, target + DIALOGUE_TOLERANCE
        elif band is None:
            low, high = max(0.0, target - DIALOGUE_TOLERANCE), target + DIALOGUE_TOLERANCE
        else:
            low, high = target * band[0], target * band[1]
        if not (low <= current <= high):
            # 帯の端から5%以内の外れは「境界」。『返事』は平均文長 26.7 対 下限 27.31 で
            # 外れ扱いになったが、読者は読んでいて気づかなかった
            margin = (low - current) / low if current < low else (current - high) / high
            violations.append({"key": key, "label": label, "unit": unit,
                               "current": current, "target": target,
                               "low": round(low, 2), "high": round(high, 2),
                               "marginal": margin <= 0.05,
                               "direction": "上げる" if current < low else "下げる"})
    return violations


# 指標ごとに、どう直せばそう動くかの具体的な指示
REVISION_ADVICE = {
    ("characters", "上げる"): "新しい出来事や人物は足さない。承の各単位の描写と会話を厚くして伸ばす。転の位置は本文の8割前後を保つ。",
    ("characters", "下げる"): "筋は変えず、承の描写を削って縮める。転から結までは削らない。",
    ("sentence_length_mean", "上げる"): "短い文を読点でつないで一文に二つの動作や観察を入れる。会話文はそのままでよい。",
    ("sentence_length_mean", "下げる"): "長い文を二つに分ける。一文に一つの動作か観察にする。",
    ("sentence_length_cv", "上げる"): "長い文と短い文を隣り合わせる。段落の最後を短い断定で落とす。",
    ("sentence_length_cv", "下げる"): "極端に長い文を分け、極端に短い文を前後とつなぐ。",
    ("simile_per_1000", "上げる"): "描写の要所に直喩を足す。喩える先は手で触れられる具体物にする。",
    ("simile_per_1000", "下げる"): "直喩を減らす。残すのは喩える先が具体物で、その場面にしか使えないものだけ。",
    ("dialogue_ratio", "上げる"): "地の文で説明している人物の意図を、会話に置き換える。",
    ("dialogue_ratio", "下げる"): "会話の一部を地の文の要約か動作に置き換える。",
}


def revision_prompt(draft: str, violations: list[dict], targets: dict) -> str:
    lines = ["# 書き直しの依頼", "",
             "以下の原稿を計測したところ、目標から外れている指標がある。"
             "指摘した指標だけを直し、題名・筋・人物・転の位置・結びの型は変えない。", "",
             f"目標の出どころ: {targets['source']}", "",
             "| 指標 | 現在 | 目標 | 許容範囲 | 方向 |", "| --- | --- | --- | --- | --- |"]
    for v in violations:
        fmt = (lambda x: f"{x:,.0f}") if v["key"] == "characters" else (lambda x: f"{x:.2f}")
        lines.append(f"| {v['label']} | {fmt(v['current'])}{v['unit']} | {fmt(v['target'])}{v['unit']} "
                     f"| {fmt(v['low'])}〜{fmt(v['high'])} | {v['direction']} |")
    lines += ["", "## 直し方", ""]
    for v in violations:
        lines.append(f"- **{v['label']}を{v['direction']}**: {REVISION_ADVICE[(v['key'], v['direction'])]}")
    lines += ["", "## 出力の形式", "",
              "書き直した全文を出力する。1行目にタイトルのみ、空行を1つ置いて本文。",
              "変更点の説明や前置きは書かない。", "",
              "# 原稿", "", draft.strip(), ""]
    return "\n".join(lines)


def format_metrics(metrics: dict, violations: list[dict]) -> str:
    bad = {v["key"] for v in violations}
    parts = []
    for key, label, unit, _ in CHECKS:
        value = metrics[key]
        shown = f"{value:,.0f}" if key == "characters" else f"{value:.2f}"
        parts.append(f"{label} {shown}{unit}" + ("←" if key in bad else ""))
    return " / ".join(parts)


# --------------------------------------------------------------------------- 生成

def resolve_backend(choice: str) -> str:
    """どの経路で書かせるかを決める。

    api: ANTHROPIC_API_KEY を使って Claude API を直接呼ぶ（従量課金）
    cli: Claude Code の CLI に投げる（APIキー不要。Claude Code の契約を使う）
    """
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    has_cli = shutil.which("claude") is not None

    if choice == "api":
        if not has_key:
            sys.exit("ANTHROPIC_API_KEY が設定されていない。"
                     "--backend cli なら Claude Code 経由でキー無しに実行できる。")
        return "api"
    if choice == "cli":
        if not has_cli:
            sys.exit("claude コマンドが見つからない。Claude Code を入れるか、"
                     "ANTHROPIC_API_KEY を設定して --backend api を使うこと。")
        return "cli"

    if has_key:
        return "api"
    if has_cli:
        return "cli"
    sys.exit("実行経路がない。ANTHROPIC_API_KEY を設定するか、Claude Code を入れること。\n"
             "プロンプトだけ欲しい場合は compose を使う。")


def call_claude_cli(prompt: str, model: str, timeout: int) -> tuple[str, dict]:
    """Claude Code の CLI に投げる。APIキーを持たない環境向けの経路。"""
    command = ["claude", "-p", "--model", model,
               "--system-prompt", SYSTEM_PROMPT + CLI_SYSTEM_SUFFIX]
    # API の過負荷（529）などの一時的な失敗は、間を置いて再試行する
    text, last_error = "", ""
    for attempt in range(4):
        try:
            done = subprocess.run(command, input=prompt, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last_error = f"{timeout} 秒で終わらなかった"
        else:
            out = done.stdout.strip()
            if done.returncode == 0 and out and not out.startswith("API Error"):
                text = out
                break
            last_error = (out or done.stderr.strip())[:300]
        wait = 30 * (attempt + 1)
        print(f"[claude コマンドが失敗: {last_error[:80]} / {wait}秒後に再試行]", file=sys.stderr)
        time.sleep(wait)
    if not text:
        sys.exit(f"claude コマンドが4回とも失敗した: {last_error}")
    print(text)
    return text, {"backend": "claude-cli", "model": model}


def call_claude(prompt: str, effort: str, max_tokens: int) -> tuple[str, dict]:
    try:
        import anthropic
    except ImportError:
        sys.exit("anthropic パッケージが無い。`pip install anthropic` を実行するか、"
                 "compose でプロンプトだけ出力すること。")

    client = anthropic.Anthropic()
    try:
        # 長い出力になるので streaming。途中で HTTP タイムアウトに当たらないため。
        # fallbacks は、安全性の判定で拒否されたときに別モデルで同じ要求を引き継がせる指定。
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for chunk in stream.text_stream:
                print(chunk, end="", flush=True)
            message = stream.get_final_message()
        print()
    except TypeError as exc:
        # 認証情報が1つも無いとき、SDK はリクエスト時に TypeError を投げる
        if "authentication method" not in str(exc):
            raise
        sys.exit("認証情報が見つからない。ANTHROPIC_API_KEY を設定すること。\n"
                 "APIを使わずプロンプトだけ欲しい場合は compose を使う。")
    except anthropic.AuthenticationError:
        sys.exit("\n認証に失敗した。ANTHROPIC_API_KEY を設定すること。")
    except anthropic.RateLimitError:
        sys.exit("\nレート制限に当たった。しばらく待って再実行すること。")
    except anthropic.APIStatusError as exc:
        sys.exit(f"\nAPIがエラーを返した（{exc.status_code}）: {exc.message}")
    except anthropic.APIConnectionError as exc:
        sys.exit(f"\nAPIに接続できない: {exc}")

    if message.stop_reason == "refusal":
        sys.exit("\n生成が拒否された（stop_reason=refusal）。テーマを変えて試すこと。")

    text = "".join(b.text for b in message.content if b.type == "text")
    usage = {
        "backend": "api",
        "model": message.model,
        "stop_reason": message.stop_reason,
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }
    return text, usage


def slugify(text: str, limit: int = 24) -> str:
    cleaned = "".join(ch for ch in text if ch not in '\\/:*?"<>|#＃\n\t ')
    return cleaned[:limit] or "story"


# --------------------------------------------------------------------------- 実行

def cmd_compose(args: argparse.Namespace) -> int:
    prompt = build_prompt(args, load_works())
    if args.out:
        out = Path(args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt, encoding="utf-8")
        print(f"{len(prompt):,}字のプロンプトを {out} に書き出した", file=sys.stderr)
    else:
        print(prompt)
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    backend = resolve_backend(args.backend)
    works = load_works()
    prompt = build_prompt(args, works)
    targets = targets_for(args, works)
    label = MODEL if backend == "api" else args.cli_model
    print(f"[{backend} / {label} / プロンプト {len(prompt):,}字 / 目標: {targets['source']}]\n", file=sys.stderr)

    def call(text_prompt: str) -> tuple[str, dict]:
        if backend == "cli":
            return call_claude_cli(text_prompt, args.cli_model, args.cli_timeout)
        return call_claude(text_prompt, args.effort, args.max_tokens)

    # 1回目は通常の生成。以後は計測して外れた指標だけを直させる
    rounds: list[dict] = []
    if args.novel:
        text, usage = generate_novel(args, prompt, call)
        # 長編は全文を書き直させると出力が途切れるので、章ごとの長さ調整だけにして計測のみ行う
        args.revise = 0
    elif args.sectioned:
        text, usage = generate_sectioned(args, prompt, call)
    else:
        text, usage = call(prompt)
    for round_no in range(args.revise + 1):
        metrics = measure_story(text)
        violations = check_story(metrics, targets, args.plot)
        rounds.append({"round": round_no, "text": text, "metrics": metrics,
                       "violations": violations, "usage": usage})
        print(f"\n[第{round_no}稿] {format_metrics(metrics, violations)}", file=sys.stderr)
        if not violations or round_no == args.revise:
            break
        print(f"[書き直し {round_no + 1}/{args.revise}: "
              + "、".join(f"{v['label']}を{v['direction']}" for v in violations) + "]\n", file=sys.stderr)
        text, usage = call(revision_prompt(text, violations, targets))

    # 外れた指標が最も少ない稿を採用。同数なら新しいほう
    best = min(rounds, key=lambda r: (len(r["violations"]), -r["round"]))
    final = best["text"].strip()
    # モデルが題名を見出し記法（「# 題名」）で返すことがあるので、記号を落として1行目を揃える
    if final:
        first, _, rest = final.partition("\n")
        first = first.lstrip("#＃ 　").strip()
        final = first + ("\n" + rest if rest else "")
    title = final.split("\n", 1)[0].strip() if final else "無題"

    dest = STORIES / f"{date.today().isoformat()}_{slugify(title)}"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "story.md").write_text(final.strip() + "\n", encoding="utf-8")
    (dest / "prompt.md").write_text(prompt, encoding="utf-8")
    # 渡したペルソナの写し。資料を後で直しても、この生成に何を渡したかが残る
    persona = resolve_persona(args.persona)
    if persona:
        (dest / "persona.md").write_text(persona[1] + "\n", encoding="utf-8")
    for r in rounds:
        if len(rounds) > 1:
            (dest / f"draft_{r['round']}.md").write_text(r["text"].strip() + "\n", encoding="utf-8")
    (dest / "meta.json").write_text(json.dumps({
        "theme": args.theme,
        "author_reference": args.author,
        "structure_reference": args.structure,
        "avoid": args.avoid,
        "plot": args.plot,
        "persona": persona[0] if persona else None,
        "sectioned": args.sectioned,
        "novel": args.novel,
        "length_target": args.length,
        "effort": args.effort,
        "seed": args.seed,
        "targets": targets,
        "rounds": [{"round": r["round"], "metrics": r["metrics"],
                    "violations": [v["label"] + "を" + v["direction"] for v in r["violations"]],
                    **r["usage"]} for r in rounds],
        "adopted_round": best["round"],
        "characters": len(final.strip()),
        "generated_on": date.today().isoformat(),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n-> {dest.relative_to(REPO_ROOT)}（第{best['round']}稿を採用、"
          f"外れた指標 {len(best['violations'])}）", file=sys.stderr)
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("theme", help="書かせたいテーマ")
    parser.add_argument("--author", help="文体の参照先にする作家名（コーパス収録のもの）")
    parser.add_argument("--structure", help="緊張の配置を借りる作品ID")
    parser.add_argument("--length", type=int, default=4000, help="目標の長さ（字、既定 4000）")
    parser.add_argument("--similes", type=int, default=20, help="渡す比喩の実例数（既定 20）")
    parser.add_argument("--seed", type=int, default=0, help="実例を選ぶ乱数の種")
    parser.add_argument("--plot", choices=sorted(PLOT_TYPES),
                        help="展開の型（一撃／反復／露見／枠／心境）。context/guide/story_structure.md 参照")
    parser.add_argument("--baseline", choices=["fiction", "modern", "all"], default="fiction",
                        help="文体の目標値をどの群から取るか。fiction=小説（既定）／modern=片岡義男の小説／"
                             "all=無作為2,000作品（随筆・評論を含む）")
    parser.add_argument("--avoid", nargs="*", default=[],
                        help="使わせない題材・仕掛け（例: --avoid 髪の毛 祖父の遺品）")
    parser.add_argument("--persona",
                        help="書き手の矜持と理念を反映させる。context/persona/ の名前か Markdown のパス。"
                             "指定しなければ作法だけで書かせる（従来どおり）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_compose = sub.add_parser("compose", help="プロンプトを組み立てる（APIを使わない）")
    add_common(p_compose)
    p_compose.add_argument("--out", help="プロンプトの保存先")
    p_compose.set_defaults(func=cmd_compose)

    p_gen = sub.add_parser("generate", help="Claude に書かせて stories/ に保存する")
    add_common(p_gen)
    p_gen.add_argument("--effort", default="high",
                       choices=["low", "medium", "high", "xhigh", "max"],
                       help="思考の深さ（既定 high）")
    p_gen.add_argument("--max-tokens", type=int, default=64000)
    p_gen.add_argument("--backend", default="auto", choices=["auto", "api", "cli"],
                       help="api=Claude API（要 ANTHROPIC_API_KEY）／"
                            "cli=Claude Code 経由（キー不要）／auto=使える方（既定）")
    p_gen.add_argument("--cli-model", default="opus", help="cli のときのモデル（既定 opus）")
    p_gen.add_argument("--cli-timeout", type=int, default=900,
                       help="cli の待ち時間（秒、既定 900）")
    p_gen.add_argument("--novel", action="store_true",
                       help="長編モード。章立て表を作ってから章ごとに書く（context/guide/long_structure.md）")
    p_gen.add_argument("--sectioned", action="store_true",
                       help="構成表を作ってから節ごとに書かせる。長い作品向け（1回の生成は目標の7割ほどしか書かないため）")
    p_gen.add_argument("--revise", type=int, default=2,
                       help="計測して外れた指標を直させる回数の上限（既定 2、0 で無効）")
    p_gen.set_defaults(func=cmd_generate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

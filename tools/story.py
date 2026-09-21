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
import os
import random
import statistics
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = REPO_ROOT / "analysis"
GUIDE = REPO_ROOT / "context" / "guide" / "story_craft.md"
STORIES = REPO_ROOT / "stories"

MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def load_works() -> list[dict]:
    works = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ANALYSIS.glob("*/*.json"))]
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


SYSTEM_PROMPT = """あなたは日本語で短編小説を書く。

読者に読ませるための作品を書くのであって、技法の解説や制作意図の説明はしない。
与えられた作法は近代日本文学のコーパスから統計的に抽出したものだが、
数値を満たすことが目的ではない。作品として成立することが目的で、
作法はそのための手段として使う。"""


def build_prompt(args: argparse.Namespace, works: list[dict]) -> str:
    rng = random.Random(args.seed)
    guide = GUIDE.read_text(encoding="utf-8") if GUIDE.exists() else ""

    parts = [
        "# 依頼",
        "",
        f"次のテーマで短編小説を書いてほしい。",
        "",
        f"**テーマ**: {args.theme}",
        f"**目標の長さ**: {args.length:,}字前後",
        "",
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

    if guide:
        parts += ["### 作法", "", guide, ""]

    parts += [
        "# 守ること",
        "",
        "1. **オリジナルであること**。参照資料に出てくる固有名詞（人名・地名・作品名）を使わない。"
        "既存作品の筋をなぞらない。借りるのは文の長さの設計、比喩の作り方、"
        "視点の移し方、緊張の配置という抽象的な層だけ。",
        "2. **表記は新字新仮名**。歴史的仮名遣いや旧字体は使わない。",
        "3. **書き出しは短く切る**。1文目は25字前後。世界設定の説明から始めない。",
        "4. **結びは説明しない**。最終文は平均より短く、解決を書ききらずに事実を一つ置いて終える。",
        "5. **比喩は「ようだ」に頼りすぎない**。喩える先は手で触れられる具体物にする。",
        "6. ダッシュ `――` と三点リーダ `……` は、どちらか一方に絞る。",
        "",
        "# 出力の形式",
        "",
        "1行目にタイトルのみを書き、空行を1つ置いて本文を始める。",
        "見出し・注釈・あとがき・技法の説明は書かない。本文だけを書く。",
        "",
    ]
    return "\n".join(p for p in parts if p is not None)


# --------------------------------------------------------------------------- 生成

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
        "model": message.model,
        "stop_reason": message.stop_reason,
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }
    return text, usage


def slugify(text: str, limit: int = 24) -> str:
    cleaned = "".join(ch for ch in text if ch not in '\\/:*?"<>|\n\t ')
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
    prompt = build_prompt(args, load_works())
    print(f"[{MODEL} / effort={args.effort} / プロンプト {len(prompt):,}字]\n", file=sys.stderr)

    text, usage = call_claude(prompt, args.effort, args.max_tokens)
    title = text.strip().split("\n", 1)[0].strip() if text.strip() else "無題"

    dest = STORIES / f"{date.today().isoformat()}_{slugify(title)}"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "story.md").write_text(text.strip() + "\n", encoding="utf-8")
    (dest / "prompt.md").write_text(prompt, encoding="utf-8")
    (dest / "meta.json").write_text(json.dumps({
        "theme": args.theme,
        "author_reference": args.author,
        "structure_reference": args.structure,
        "length_target": args.length,
        "effort": args.effort,
        "seed": args.seed,
        "characters": len(text.strip()),
        "generated_on": date.today().isoformat(),
        **usage,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n-> {dest.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("theme", help="書かせたいテーマ")
    parser.add_argument("--author", help="文体の参照先にする作家名（コーパス収録のもの）")
    parser.add_argument("--structure", help="緊張の配置を借りる作品ID")
    parser.add_argument("--length", type=int, default=4000, help="目標の長さ（字、既定 4000）")
    parser.add_argument("--similes", type=int, default=20, help="渡す比喩の実例数（既定 20）")
    parser.add_argument("--seed", type=int, default=0, help="実例を選ぶ乱数の種")


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
    p_gen.set_defaults(func=cmd_generate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

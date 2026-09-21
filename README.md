# contextMaker

青空文庫の作品を保存し、物語の構成・比喩・文章技法を抽出して、
その抽出結果をもとにオリジナルの物語を書かせるための一式。

```
青空文庫ミラー ──▶ library/  ──▶ analysis/ ──▶ context/ ──▶ stories/
                aozora.py   analyze.py  build_context.py  story.py
                 取得・整形    技法の抽出     LLM用の資料     物語の生成
```

各段は独立したスクリプトで、前段の出力ファイルだけを入力にする。
Python 3.9 以降。物語の生成以外は標準ライブラリのみで動く。

現在 18作家43作品（約150万字）を収録している。

---

## 1. 取得 — `tools/aozora.py`

青空文庫本家（`www.aozora.gr.jp`）を直接クロールせず、テキスト版ミラー
[aozorahack/aozorabunko_text](https://github.com/aozorahack/aozorabunko_text) を
`raw.githubusercontent.com` 経由で参照する。
本家はボランティア運営でサーバに余裕がないため、まとまった数を取るときに本家を叩かずに済ませる。
ミラーは zip を展開済みなので、取得側で zip を扱う必要もない。

```console
# タイトル・著者名で探す
$ python3 tools/aozora.py search 銀河鉄道
456	宮沢賢治	銀河鉄道の夜

$ python3 tools/aozora.py search --author 太宰 --limit 5

# 作品IDを指定して取得・保存する（複数可）
$ python3 tools/aozora.py fetch 456 127
宮沢賢治『銀河鉄道の夜』-> library/000081/456_銀河鉄道の夜
芥川龍之介『羅生門』-> library/000879/127_羅生門

# ルビを消さずに 漢字(かんじ) の形で残す
$ python3 tools/aozora.py fetch 456 --keep-ruby
```

作品ごとに `library/<人物ID>/<作品ID>_<タイトル>/` を作り、3ファイルを置く。

| ファイル | 内容 |
| --- | --- |
| `original.txt` | 青空文庫形式のテキストを UTF-8 に変換しただけのもの。ルビ・注記はそのまま |
| `plain.txt` | 本文のみ。ルビ `《》`、ルビ開始記号 `｜`、入力者注 `［＃…］`、冒頭の記号説明、奥付を除去 |
| `meta.json` | タイトル・著者・取得元URL・図書カードURL・底本情報・文字数・取得日 |

元のテキストは Shift_JIS（実体は cp932）なので、変換は cp932 を優先して行う。

`index/catalog.tsv` にミラー収録の全 17,436 作品の一覧がある
（`work_id` / `person_id` / `title` / `subtitle` / `author` / `path`）。
更新するときはミラーをクローンして作り直す。

```console
$ git clone --depth 1 https://github.com/aozorahack/aozorabunko_text.git /tmp/azmirror
$ python3 tools/aozora.py build-catalog --mirror /tmp/azmirror
```

## 2. 抽出 — `tools/analyze.py`

`library/*/plain.txt` を読み、`analysis/<人物ID>/<作品ID>.json` に書き出す。
形態素解析器は使わず、文字種と記号の並びだけで判定する（環境に依存させないため）。
そのぶん語彙の抽出は精度が低く、結果は「傾向」として読むもの。

```console
$ python3 tools/analyze.py           # library/ 以下すべて
$ python3 tools/analyze.py --work 623  # 作品IDを指定
```

抽出するもの。

- **基本統計** — 文長の平均／中央値／標準偏差／最大、漢字・仮名比率、読点密度、
  ダッシュ・三点リーダ・感嘆符・疑問符の密度
- **会話** — 鉤括弧の占有率。200字を超える括弧は「括弧付きの語り」として会話と分ける
  （泉鏡花『高野聖』は語り全体が括弧に入る枠物語で、分けないと会話率 0.92 と誤読する）
- **正書法** — 旧字体と歴史的仮名遣いの出現率から新字新仮名／旧字旧仮名を判定
- **章構成** — 見出し行の検出と各章の文字数。見出しは1字の「文」として統計を歪めるので本文集計から外す
- **緊張曲線** — 本文を10等分し、区間ごとの会話率・平均文長・感嘆疑問密度・ダッシュ密度
- **比喩** — 「ようだ」「ごとし」「まるで」など8分類の目印を含む文と、作品内での位置
- **畳語**（擬音語・擬態語の近似）、**書き出し・結び**、**畳みかけの反復**、**頻出語**

## 3. コンテキスト生成 — `tools/build_context.py`

`analysis/` を集約して `context/` に Markdown を出す。LLM のコンテキストにそのまま貼れる粒度。

```console
$ python3 tools/build_context.py
```

- `context/works/` — 作品カード（統計、章構成、緊張曲線、書き出し・結び、比喩例）
- `context/authors/` — 作家プロファイル（コーパス全体との差、緊張曲線の一覧）
- `context/techniques/` — 比喩・書き出し・結び・畳語・構成のカタログ
- `context/guide/story_craft.md` — **手で書いた創作ガイド**。生成物ではない

生成されるのは上3つで、`guide/` はスクリプトが触らない。
抽出結果を読んで作法として言語化したもので、このリポジトリで一番中身があるのはここ。

## 4. 物語の生成 — `tools/story.py`

`analysis/` の数値と創作ガイドからプロンプトを組み立て、Claude に書かせる。

```console
# プロンプトを組み立てるだけ（APIキー不要）
$ python3 tools/story.py compose "古い時計店に持ち込まれた、動かない懐中時計" \
    --author 梶井基次郎 --structure 127 --length 3000

# Claude に書かせて stories/ に保存する
$ pip install anthropic
$ export ANTHROPIC_API_KEY=...
$ python3 tools/story.py generate "冬の停留所で待つ人" --author 芥川龍之介 --length 4000
```

| オプション | 効果 |
| --- | --- |
| `--author` | その作家の文体の数値と比喩の実例をプロンプトに差し込む |
| `--structure <作品ID>` | その作品の緊張の配置（会話率・感嘆疑問密度の推移）だけを表で渡す。筋や題材は渡さない |
| `--length` | 目標の文字数（既定 4000） |
| `--similes` | 渡す比喩の実例数（既定 20） |
| `--effort` | 思考の深さ `low`〜`max`（既定 `high`、generate のみ） |

`generate` は `claude-opus-5` を streaming で呼び、`stories/<日付>_<タイトル>/` に
本文・使ったプロンプト・メタ情報（モデル、トークン数、種）を保存する。
安全性の判定で拒否されたとき別モデルに引き継ぐ `fallbacks` を既定で有効にしてある。

コーパスから借りるのは文の設計・比喩の作り方・緊張の配置という抽象的な層だけで、
固有名詞や筋は渡さない。プロンプトでも流用を明示的に禁じている。

`stories/sample_prompt/` に compose の出力例がある。

---

## 権利について

`library/` 以下のテキストは
[「青空文庫収録ファイルの取り扱い規準」](https://www.aozora.gr.jp/guide/kijyunn.html)
の下で利用すること。大半は著作権保護期間が終了した作品だが、
保護期間中でクリエイティブ・コモンズ・ライセンス等により再配布されているファイルも含まれる。
作品ごとの条件は `meta.json` の `card`（図書カード）に記載の「利用に関する注記」で確認できる。

設計の経緯と各段の仕様は [docs/PLAN.md](docs/PLAN.md) にある。

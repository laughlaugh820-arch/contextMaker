# compose の出力例

`tools/story.py compose` が組み立てたプロンプトそのもの。APIキーが無くても作れる。

```console
$ python3 tools/story.py compose \
    "古い時計店に持ち込まれた、動かない懐中時計" \
    --author 梶井基次郎 --structure 127 --length 3000 \
    --out stories/sample_prompt/prompt.md
```

- `--author 梶井基次郎` — 文体の数値（平均文長34字、ばらつき0.70、会話率0.01、
  ダッシュ3.8/千字）と、その作家の比喩の実例を差し込む
- `--structure 127` — 芥川龍之介『羅生門』の緊張の配置（会話率と感嘆疑問密度の推移）
  だけを表として渡す。筋や題材は渡さない

`generate` を使うと、このプロンプトを Claude に投げて
`stories/<日付>_<タイトル>/` に本文・プロンプト・メタ情報を保存する。

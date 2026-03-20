# ゲーム設定ファイルの追加方法

このフォルダにJSONファイルを追加すると、AIコメント太郎のGUIに自動でゲームが追加されます。

## ファイル名のルール
- ファイル名は英数字・アンダースコアのみ（例: `apex_legends.json`）
- 拡張子は必ず `.json`

## JSONの構造

```json
{
  "title": "ゲームの日本語名（GUIに表示される名前）",
  "title_en": "英語名",
  "window_titles": ["ウィンドウタイトルの候補1", "候補2"],
  "ui_description": "画面UIの説明（Geminiに渡されます）",
  "skip_scenes": [
    "スキップすべき画面の説明1",
    "スキップすべき画面の説明2"
  ],
  "special_logic": {
    "map_screen": {
      "enabled": false,
      "description": "マップ画面の特別処理の説明（不要な場合はenabledをfalseに）"
    }
  },
  "comment_hints": [
    "コメント生成のヒント1",
    "コメント生成のヒント2"
  ]
}
```

## 項目説明

| 項目 | 必須 | 説明 |
|---|---|---|
| title | ✅ | GUIのプルダウンに表示されるゲーム名 |
| title_en | | 英語名（任意） |
| window_titles | ✅ | ゲームウィンドウのタイトル候補（部分一致） |
| ui_description | ✅ | HPバー・残り人数など画面UIの説明 |
| skip_scenes | ✅ | コメントをスキップすべき画面の説明 |
| special_logic | | ゲーム固有の特別処理（不要なら省略可） |
| comment_hints | | コメント生成時のヒント（任意） |

## 追加例（Apex Legends）

`apex_legends.json` というファイル名で作成し、このフォルダに置くだけで次回起動時に自動追加されます。

## 注意事項
- JSONの形式が正しくないと読み込みに失敗します
- 追加後はアプリを再起動してください

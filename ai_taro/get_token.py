"""
Twitch OAuthトークン取得ヘルパー
botアカウントのOAuthトークンを取得するための手順を案内します。
"""

import webbrowser
import sys


def main():
    print("=" * 60)
    print("Twitch OAuthトークン取得ガイド")
    print("=" * 60)
    print()
    print("このスクリプトは、botアカウントのOAuthトークンを取得するための")
    print("手順を案内します。")
    print()
    print("【重要】以下の手順は、botアカウントでブラウザにログインした状態で行ってください。")
    print("（メインアカウントではなく、bot用に作成したアカウントでログイン）")
    print()

    print("手順1: Twitch Token Generator を開きます")
    print("  URL: https://twitchtokengenerator.com/")
    print()

    input("Enterキーを押すと、ブラウザでページを開きます...")
    try:
        webbrowser.open("https://twitchtokengenerator.com/")
    except Exception:
        print("ブラウザを自動で開けませんでした。上記URLを手動で開いてください。")

    print()
    print("手順2: ページが開いたら以下の操作を行ってください:")
    print("  1. 「Bot Chat Token」ボタンをクリック")
    print("  2. Twitchのログイン画面が出たら、botアカウントでログイン")
    print("  3. アクセスを許可")
    print("  4. 表示された「Access Token」をコピー")
    print()
    print("手順3: コピーしたトークンを config.py に貼り付けます")
    print("  config.py の BOT_TOKEN の行を以下のように変更してください:")
    print()
    print('  BOT_TOKEN = "oauth:ここにコピーしたトークンを貼り付け"')
    print()
    print("  ※ 「oauth:」は自動で付いていない場合は手動で追加してください")
    print()

    token = input("取得したトークンをここに貼り付けてください（省略可）: ").strip()

    if token:
        # oauth:プレフィックスの処理
        if not token.startswith("oauth:"):
            token = f"oauth:{token}"

        print()
        print("config.py に以下の行をコピーして貼り付けてください:")
        print()
        print(f'BOT_TOKEN = "{token}"')
        print()

        # 自動書き込みの確認
        update = input("config.py を自動で更新しますか？ (y/N): ").strip().lower()
        if update == "y":
            try:
                with open("config.py", "r", encoding="utf-8") as f:
                    content = f.read()

                # トークンを置換
                import re
                new_content = re.sub(
                    r'BOT_TOKEN\s*=\s*"[^"]*"',
                    f'BOT_TOKEN = "{token}"',
                    content
                )

                with open("config.py", "w", encoding="utf-8") as f:
                    f.write(new_content)

                print("✓ config.py を更新しました！")
            except Exception as e:
                print(f"✗ config.py の更新に失敗しました: {e}")
                print("  手動で更新してください。")

    print()
    print("次のステップ:")
    print("  1. config.py の BOT_NICK にbotアカウントのユーザー名を設定")
    print("  2. config.py の CHANNEL_NAME にあなたのチャンネル名を設定")
    print("  3. python test_ollama.py でセットアップを確認")
    print("  4. python main.py でbotを起動")
    print()


if __name__ == "__main__":
    main()

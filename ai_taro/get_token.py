"""
Twitch OAuthトークン取得ヘルパー
botアカウントのOAuthトークンを取得するための手順を案内します。
"""

import webbrowser
import re
import os


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

    token = input("取得したトークンをここに貼り付けてください（省略可）: ").strip()

    if token:
        # oauth:プレフィックスを除去（secrets.pyにはトークン文字列のみ保存）
        token = token.replace("oauth:", "").strip()

        print()
        print("taro_secrets.py に以下の行をコピーして貼り付けてください:")
        print()
        print(f'BOT_TOKEN = "{token}"')
        print()

        # secrets.pyに自動書き込み
        secrets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taro_secrets.py")
        if not os.path.exists(secrets_path):
            secrets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.py")
        if os.path.exists(secrets_path):
            update = input("設定ファイルを自動で更新しますか？ (y/N): ").strip().lower()
            if update == "y":
                try:
                    with open(secrets_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    new_content = re.sub(
                        r'BOT_TOKEN\s*=\s*"[^"]*"',
                        f'BOT_TOKEN = "{token}"',
                        content
                    )

                    with open(secrets_path, "w", encoding="utf-8") as f:
                        f.write(new_content)

                    print("✓ 設定ファイルを更新しました！")
                except Exception as e:
                    print(f"✗ 設定ファイルの更新に失敗しました: {e}")
                    print("  手動で更新してください。")
        else:
            print("taro_secrets.py が見つかりません。secrets_sample.py をコピーして作成してください。")

    print()
    print("次のステップ:")
    print("  GUIを起動して設定タブから各項目を確認・保存してください。")
    print()


if __name__ == "__main__":
    main()

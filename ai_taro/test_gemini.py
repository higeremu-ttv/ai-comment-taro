"""
Gemini API 接続テストスクリプト
config.py の GEMINI_API_KEY が正しく設定されているか確認します。
実行方法: python test_gemini.py
"""

import sys
import os

# このスクリプトのディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_gemini_connection():
    """Gemini APIへの接続をテストする"""
    print("=" * 50)
    print("Gemini API 接続テスト")
    print("=" * 50)

    # config.pyを読み込む
    try:
        import config
        print(f"✓ config.py を読み込みました")
    except ImportError:
        print("✗ config.py が見つかりません")
        return False

    # APIキーの確認
    api_key = getattr(config, 'GEMINI_API_KEY', '')
    if not api_key or api_key == 'your_gemini_api_key_here':
        print("✗ GEMINI_API_KEY が設定されていません")
        print("  config.py を開いて GEMINI_API_KEY を設定してください")
        print("  取得先: https://aistudio.google.com/app/apikey")
        return False
    print(f"✓ APIキーが設定されています: {api_key[:8]}...")

    # google-generativeaiのインポート確認
    try:
        import google.generativeai as genai
        print(f"✓ google-generativeai ライブラリが見つかりました")
    except ImportError:
        print("✗ google-generativeai ライブラリが見つかりません")
        print("  pip install google-generativeai を実行してください")
        return False

    # APIへの接続テスト
    print("\nGemini APIに接続中...")
    try:
        genai.configure(api_key=api_key)
        model_name = getattr(config, 'GEMINI_MODEL', 'gemini-1.5-flash')
        model = genai.GenerativeModel(model_name)

        response = model.generate_content(
            "「テスト成功」と日本語で返答してください。それ以外は何も書かないでください。"
        )

        if response and response.text:
            print(f"✓ Gemini API接続成功！")
            print(f"  モデル: {model_name}")
            print(f"  テスト応答: {response.text.strip()}")
            return True
        else:
            print("✗ Gemini APIからの応答が空です")
            return False

    except Exception as e:
        error_str = str(e)
        if "API_KEY" in error_str.upper() or "invalid" in error_str.lower():
            print(f"✗ APIキーが無効です: {e}")
            print("  https://aistudio.google.com/app/apikey で正しいキーを取得してください")
        elif "429" in error_str or "quota" in error_str.lower():
            print(f"✗ APIレート制限に達しています: {e}")
            print("  しばらく待ってから再試行してください")
        else:
            print(f"✗ Gemini API接続エラー: {e}")
        return False


def test_screen_capture():
    """スクリーンキャプチャのテスト"""
    print("\n" + "=" * 50)
    print("スクリーンキャプチャ テスト")
    print("=" * 50)

    try:
        import mss
        print("✓ mss ライブラリが見つかりました")
    except ImportError:
        print("✗ mss ライブラリが見つかりません")
        print("  pip install mss を実行してください")
        return False

    try:
        from PIL import Image
        print("✓ Pillow ライブラリが見つかりました")
    except ImportError:
        print("✗ Pillow ライブラリが見つかりません")
        print("  pip install Pillow を実行してください")
        return False

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)
            print(f"✓ スクリーンショット取得成功: {screenshot.size[0]}x{screenshot.size[1]}px")
        return True
    except Exception as e:
        print(f"✗ スクリーンショット取得エラー: {e}")
        return False


if __name__ == "__main__":
    success_gemini = test_gemini_connection()
    success_screen = test_screen_capture()

    print("\n" + "=" * 50)
    print("テスト結果まとめ")
    print("=" * 50)
    print(f"Gemini API接続: {'✓ 成功' if success_gemini else '✗ 失敗'}")
    print(f"スクリーンキャプチャ: {'✓ 成功' if success_screen else '✗ 失敗'}")

    if success_gemini:
        print("\n✓ Botを起動する準備ができています！")
        print("  start_bot.bat をダブルクリックしてBotを起動してください。")
    else:
        print("\n✗ 設定を確認してから再度テストしてください。")

    input("\nEnterキーを押して終了...")

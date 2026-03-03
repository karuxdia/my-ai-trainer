import streamlit as st
import google.generativeai as genai
import json
import gspread
from google.oauth2.service_account import Credentials
import datetime

# ==========================================
# 0. 各種設定エリア
# ==========================================
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]

MY_PROFILE = """
【基本情報】
- 身長: 173cm
- 体重: 84kg
- 体脂肪率: 15%
- 筋トレ歴: 7年（上級者〜エリートレベル）

【主要スタッツ（使用重量）】
- スクワット: 180kg × 10reps（パラレル）
- ベンチプレス: 120kg × 8reps
- ベントオーバーロー: 110kg × 10reps
- インクラインダンベルカール: 24kg（片手） × 10reps
"""
GYM_ENV = """
【フリーウエイト】
- パワーラック（耐荷重210kgまで）
- ダンベル（最大50kgまで）
- スミスマシン

【マシン・ケーブル】
- 45度レッグプレス
- ケーブルマシン（ラットプルダウン、シーテッドケーブルロー含む）
- ディップス/チンニングマシン
- インナーサイ / アウターサイ
- シーテッドレッグエクステンション
- ライイングレッグカール
- ペックフライ / リアデルト
- ショルダープレス
- チェストプレス
"""

genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# 1. スプレッドシート連携関数
# ==========================================
def get_gspread_client():
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    # JSONファイルではなく、金庫（secrets）のデータから認証情報を作成します
    credentials = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], 
        scopes=scopes
    )
    return gspread.authorize(credentials)

# ==========================================
# 2. プロンプト生成関数
# ==========================================
def create_prompt(target_parts, num_exercises, total_time_minutes, past_logs):
    parts_str = ", ".join(target_parts)

    prompt = f"""
    あなたはボディビル・パワーリフティングの知識を持つ、トップアスリート専属のAIトレーナーです。
    以下の「クライアント情報」「ジム環境」「過去のトレーニング実績」に基づき、本日の最適なトレーニングメニューを作成してください。

    ### クライアント情報
    {MY_PROFILE}

    ### 利用可能なジム環境
    {GYM_ENV}

    ### 本日のオーダー
    - ターゲット部位: {parts_str}
    - 合計種目数: {num_exercises}種目
    - トレーニング許容時間: {total_time_minutes}分以内

    ### 過去のトレーニング実績（直近の記録）
    {past_logs}

    ### メニュー作成のルール（最重要）
    1. **刺激の変化とマンネリ打破**: クライアントは毎回重量や回数を増やすだけの過負荷には耐えられません。過去の記録を参照し、前回と同じ部位でも「別の種目に変更する」「重量設定を少し下げてレップ数を増やす」「ネガティブ動作を意識させる」など、異なる角度から新鮮な刺激を与えるメニューを提案してください。
    2. **強度設定**: ウォームアップではなく、本番セットの提案をしてください。ただし、関節への負担を考慮し、必ずしもMAX重量を狙う必要はありません。
    3. **時間管理**: 指定された「{total_time_minutes}分」で全種目が完了するよう、セット数と推奨インターバルを調整してください。

    ### 出力形式（必ず以下のJSONフォーマットのみを出力すること。Markdownの装飾は許容しますが、中身はJSON配列にしてください。）
    ```json
    [
      {{
        "name": "種目名（使用器具も明記）",
        "weight_guide": "推奨設定（例: 100kg / または 限界重量の70%でゆっくりなど）",
        "sets": 推奨セット数,
        "reps": "推奨レップ数（例: 10-12）",
        "interval_sec": 推奨インターバル秒数（数値のみ, 例: 120）,
        "advice": "なぜこの種目・設定を選んだかのワンポイントアドバイス"
      }}
    ]
    ```
    """
    return prompt

# ==========================================
# ★追加: セット数増減用のコールバック関数
# ==========================================
def add_set(idx):
    st.session_state[f"sets_count_{idx}"] += 1

def sub_set(idx):
    if st.session_state[f"sets_count_{idx}"] > 1:
        st.session_state[f"sets_count_{idx}"] -= 1

# ==========================================
# 3. Streamlit UI 構築
# ==========================================
st.set_page_config(page_title="専属AIトレーナー", page_icon="💪")
st.title("💪 AI筋トレメニュー作成 ＆ 履歴記録アプリ")

# セッションステートの初期化
if "menu_data" not in st.session_state:
    st.session_state["menu_data"] = []
if "menu_generated" not in st.session_state:
    st.session_state["menu_generated"] = False

# --- サイドバー：条件入力 ---
st.sidebar.header("📝 今日のトレーニング条件")
target_parts = st.sidebar.multiselect(
    "ターゲット部位",
    ["胸", "背中", "脚", "肩前部", "肩中部", "肩後部", "腕", "腹筋"],
)
num_exercises = st.sidebar.slider("種目数", 1, 10, 4)
total_time = st.sidebar.slider("許容時間 (分)", 15, 120, 60)

# --- メニュー生成ボタン ---
if st.sidebar.button("メニュー作成 🔥", type="primary"):
    if not target_parts:
        st.error("部位を少なくとも1つ選択してください。")
    else:
        # メニュー再生成時に古い入力データをクリアする
        for key in list(st.session_state.keys()):
            if key.startswith("sets_count_") or key.startswith("weight_") or key.startswith("reps_") or key.startswith("interval_"):
                del st.session_state[key]

        with st.spinner("過去の履歴を分析し、新しい刺激を与えるメニューを考案中..."):
            try:
                client = get_gspread_client()
                sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                records = sheet.get_all_records()

                past_logs_str = "過去の記録なし"
                if records:
                    recent_records = records[-10:] # 直近10件を抽出
                    past_logs_str = "\n".join([
                        f"- 日付: {r.get('日付', '')} | 種目: {r.get('種目', '')} | 実績: {r.get('実績', '')} | インターバル: {r.get('インターバル', '')}" 
                        for r in recent_records
                    ])

                model = genai.GenerativeModel('gemini-2.5-flash')
                prompt = create_prompt(target_parts, num_exercises, total_time, past_logs_str)
                response = model.generate_content(prompt)

                text_content = response.text
                if "```json" in text_content:
                    text_content = text_content.split("```json")[1].split("```")[0].strip()
                elif "```" in text_content:
                    text_content = text_content.split("```")[1].split("```")[0].strip()

                menu_data = json.loads(text_content)
                st.session_state["menu_data"] = menu_data
                st.session_state["menu_generated"] = True
                st.success("メニューが完成しました！")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

# --- メイン画面：メニュー表示と結果入力 ---
if st.session_state["menu_generated"] and st.session_state["menu_data"]:
    st.header("📋 今日のトレーニングメニュー")

    logs = []

    for i, menu in enumerate(st.session_state["menu_data"]):
        st.subheader(f"🏋️‍♂️ {menu['name']}")
        st.markdown(f"**推奨設定**: {menu['weight_guide']} | **セット数**: {menu['sets']} | **レップ数**: {menu['reps']} | **休憩**: {menu['interval_sec']}秒")
        st.info(f"💡 AIからのアドバイス: {menu['advice']}")

        # セット数の初期化（デフォルト3）
        if f"sets_count_{i}" not in st.session_state:
            st.session_state[f"sets_count_{i}"] = 3

        # 追加・削除ボタンを横に並べる
        col_add, col_sub, _ = st.columns([1, 1, 2])
        with col_add:
            st.button("➕ セット追加", key=f"btn_add_{i}", on_click=add_set, args=(i,))
        with col_sub:
            st.button("➖ セット削除", key=f"btn_sub_{i}", on_click=sub_set, args=(i,))

        # 入力欄の生成
        for s in range(st.session_state[f"sets_count_{i}"]):
            col_w, col_r = st.columns(2)
            with col_w:
                # 0.0kgからスタート、2.5kg刻みで入力可能
                st.number_input(f"セット{s+1} 重量 (kg)", min_value=0.0, step=2.5, format="%.1f", key=f"weight_{i}_{s}")
            with col_r:
                st.number_input(f"セット{s+1} 回数", min_value=0, step=1, key=f"reps_{i}_{s}")

        # 全体のインターバル入力
        st.text_input("実際のインターバル (秒)", key=f"interval_{i}")

        # 保存用に種目名とインデックスを保持
        logs.append({
            "種目": menu["name"],
            "index": i
        })

        st.divider()

    # 全種目の記録が終わったあとの保存ボタン
    if st.button("全トレーニング完了・スプレッドシートへ記録保存 ✅", type="primary"):
        st.balloons()

        final_logs = []
        for log in logs:
            idx = log["index"]
            sets_results = []

            # 各セットの入力を「〇〇kg×〇〇回」の文字列に結合
            for s in range(st.session_state[f"sets_count_{idx}"]):
                w = st.session_state[f"weight_{idx}_{s}"]
                r = st.session_state[f"reps_{idx}_{s}"]
                if w > 0 or r > 0:
                    # 重量が整数の場合は小数点を消してスッキリ見せる
                    w_str = f"{int(w)}" if w.is_integer() else f"{w}"
                    sets_results.append(f"{w_str}kg×{r}回")

            # "100kg×10回 / 100kg×8回" のようにスラッシュで区切る
            achieved_result_str = " / ".join(sets_results) if sets_results else "記録なし"
            achieved_interval = st.session_state[f"interval_{idx}"]

            final_logs.append({
                "種目": log["種目"],
                "実績": achieved_result_str,
                "インターバル": achieved_interval
            })

        # スプレッドシートへ記録を書き込む処理
        try:
            client = get_gspread_client()
            sheet = client.open_by_key(SPREADSHEET_ID).sheet1

            today = datetime.date.today().strftime("%Y-%m-%d")
            rows_to_append = []

            for log in final_logs:
                rows_to_append.append([
                    today, 
                    log["種目"], 
                    log["実績"], 
                    log["インターバル"]
                ])

            # シートの末尾にまとめて追記
            sheet.append_rows(rows_to_append)
            st.success("📊 スプレッドシートに本日の記録を保存しました！次回はこの記録をもとにメニューが作成されます。")

        except Exception as e:
            st.error(f"スプレッドシートの保存に失敗しました: {e}")
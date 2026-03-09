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
# 1. スプレッドシート連携 ＆ 一時保存関数
# ==========================================
def get_gspread_client():
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    credentials = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], 
        scopes=scopes
    )
    return gspread.authorize(credentials)

def get_or_create_temp_sheet(client, spreadsheet_id):
    """Tempシートを取得。なければ自動作成する"""
    spreadsheet = client.open_by_key(spreadsheet_id)
    try:
        return spreadsheet.worksheet("Temp")
    except gspread.exceptions.WorksheetNotFound:
        # 見つからない場合は新しく作成（10行10列で十分）
        return spreadsheet.add_worksheet(title="Temp", rows=10, cols=10)

def get_progress_data():
    """現在の入力途中経過をセッションステートからかき集める"""
    progress = {}
    for key in st.session_state.keys():
        if key.startswith(("sets_count_", "weight_", "reps_", "interval_")):
            progress[key] = st.session_state[key]
    return progress

def save_to_temp():
    """Tempシートにメニューと途中経過を保存する"""
    try:
        client = get_gspread_client()
        sheet = get_or_create_temp_sheet(client, SPREADSHEET_ID)
        
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        menu_json = json.dumps(st.session_state.get("menu_data", []), ensure_ascii=False)
        progress_json = json.dumps(get_progress_data(), ensure_ascii=False)
        
        # A1: 日付, B1: メニューデータ, C1: 途中経過データ
        sheet.update("A1:C1", [[today_str, menu_json, progress_json]])
        return True
    except Exception as e:
        st.error(f"一時保存に失敗しました: {e}")
        return False

def load_from_temp():
    """Tempシートから今日のデータを読み込んで復元する"""
    try:
        client = get_gspread_client()
        sheet = get_or_create_temp_sheet(client, SPREADSHEET_ID)
        data = sheet.row_values(1)
        
        if not data or len(data) < 3:
            return None
            
        saved_date, menu_json, progress_json = data[0], data[1], data[2]
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        
        # 保存されたデータが今日のものであれば復元
        if saved_date == today_str:
            return {
                "menu_data": json.loads(menu_json),
                "progress_data": json.loads(progress_json)
            }
    except Exception:
        pass
    return None

def clear_temp():
    """完了時にTempシートのデータを消去する"""
    try:
        client = get_gspread_client()
        sheet = get_or_create_temp_sheet(client, SPREADSHEET_ID)
        sheet.clear()
    except Exception:
        pass

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
# 3. セット数増減用のコールバック関数
# ==========================================
def add_set(idx):
    st.session_state[f"sets_count_{idx}"] += 1

def sub_set(idx):
    if st.session_state[f"sets_count_{idx}"] > 1:
        st.session_state[f"sets_count_{idx}"] -= 1

# ==========================================
# 4. Streamlit UI 構築
# ==========================================
st.set_page_config(page_title="専属AIトレーナー", page_icon="💪")
st.title("💪 AI筋トレメニュー作成 ＆ 履歴記録アプリ")

st.markdown("""
<style>
    /* カラム（st.columns）がスマホで縦に折り返されるのを防ぐ */
    [data-testid="stHorizontalBlock"] {
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        align-items: center !important;
    }
    /* 各カラムが均等に縮むようにする */
    [data-testid="column"] {
        width: auto !important;
        flex: 1 1 0% !important;
        min-width: 0 !important;
        padding: 0 4px !important; /* スマホ用に少し隙間を詰める */
    }
</style>
""", unsafe_allow_html=True)

# --- アプリ起動時（リロード時）の自動復元処理 ---
if "initialized" not in st.session_state:
    st.session_state["initialized"] = True
    st.session_state["menu_generated"] = False
    st.session_state["menu_data"] = []
    
    # Tempシートを確認して、今日のデータがあれば復元
    temp_data = load_from_temp()
    if temp_data:
        st.session_state["menu_data"] = temp_data["menu_data"]
        st.session_state["menu_generated"] = True
        # 途中経過（重量や回数など）もセッションステートに戻す
        for k, v in temp_data["progress_data"].items():
            st.session_state[k] = v
        # リロードから復活したことを画面上部にそっと表示
        st.toast("💾 一時保存データから復元しました！", icon="✅")

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
            if key.startswith(("sets_count_", "weight_", "reps_", "interval_")):
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
                
                # ★追加: メニュー生成直後に初期状態をTempシートへ保存！
                save_to_temp()
                st.success("メニューが完成し、一時保存されました！")

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

        # セット数の初期化（復元されていなければデフォルト3）
        if f"sets_count_{i}" not in st.session_state:
            st.session_state[f"sets_count_{i}"] = 3

         # 追加・削除ボタンを横に並べる
        col_add, col_sub, _ = st.columns([1, 1, 2])
        with col_add:
            st.button("➕ セット追加", key=f"btn_add_{i}", on_click=add_set, args=(i,))
        with col_sub:
            st.button("➖ セット削除", key=f"btn_sub_{i}", on_click=sub_set, args=(i,))

        # 1. 見出し行を最初に1回だけ表示する
        col_set, col_w, col_r = st.columns([1, 2, 2])
        with col_set:
            st.markdown("**セット**")
        with col_w:
            st.markdown("**重量 (kg)**")
        with col_r:
            st.markdown("**回数**")

        # 2. 入力欄の生成（ラベルを隠して高さを詰める）
        for s in range(st.session_state[f"sets_count_{i}"]):
            col_set, col_w, col_r = st.columns([1, 2, 2])
            with col_set:
                # セット番号を縦の少し中央寄りに配置
                st.markdown(f"<div style='margin-top: 8px; text-align: center;'>{s+1}</div>", unsafe_allow_html=True)
            with col_w:
                # label_visibility="collapsed" で「セット1 重量」という文字を消し、余白を削る
                st.number_input(
                    f"セット{s+1} 重量", 
                    min_value=0.0, step=2.5, format="%.1f", value=None, 
                    key=f"weight_{i}_{s}",
                    label_visibility="collapsed"
                )
            with col_r:
                # 同様にラベルを隠す
                st.number_input(
                    f"セット{s+1} 回数", 
                    min_value=0, step=1, value=None, 
                    key=f"reps_{i}_{s}",
                    label_visibility="collapsed"
                )
        # 全体のインターバル入力
        st.text_input("実際のインターバル (秒)", key=f"interval_{i}")

        # 保存用に種目名とインデックスを保持
        logs.append({
            "種目": menu["name"],
            "index": i
        })

        st.markdown("") # ボタンの上の余白
        if st.button("💾 この種目までの経過を一時保存", key=f"save_btn_{i}"):
            with st.spinner("保存中..."):
                if save_to_temp():
                    # 画面上部にサッと通知を出して消えるようにする（画面がズレない）
                    st.toast(f"✅ 【{menu['name']}】までの経過を保存しました！", icon="💾")

        st.divider()

    # 全種目の記録が終わったあとの保存ボタン
    if st.button("全トレーニング完了・スプレッドシートへ記録保存 ✅", type="primary"):
        st.balloons()

        final_logs = []
        for log in logs:
            idx = log["index"]
            sets_results = []

            for s in range(st.session_state[f"sets_count_{idx}"]):
                w = st.session_state[f"weight_{idx}_{s}"]
                r = st.session_state[f"reps_{idx}_{s}"]
                
                # ★追加：未入力(None)のままだとエラーになるので、その場合は 0 として扱う
                w_val = w if w is not None else 0
                r_val = r if r is not None else 0

                # w と r を w_val と r_val に変更
                if w_val > 0 or r_val > 0:
                    w_str = f"{int(w_val)}" if w_val.is_integer() else f"{w_val}"
                    sets_results.append(f"{w_str}kg×{r_val}回")
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

            # ★追加: 本番保存が終わったらTempシートをクリアし、セッションをリセット
            clear_temp()
            st.session_state["menu_generated"] = False
            st.session_state["menu_data"] = []
            for key in list(st.session_state.keys()):
                if key.startswith(("sets_count_", "weight_", "reps_", "interval_")):
                    del st.session_state[key]
            
            # 再読み込みして初期画面に戻す
            st.rerun()

        except Exception as e:
            st.error(f"スプレッドシートの保存に失敗しました: {e}")

# pages/1_📝_여행_정보_입력.py

import streamlit as st
from datetime import date, timedelta
import time

st.set_page_config(page_title="여행 정보 입력", layout="centered")
st.title("📝 AI 여행 플래너 시작하기")
st.markdown("여행 계획을 시작하기 위해 아래 정보를 입력하고 버튼을 눌러주세요.")

# --- 세션 상태 초기화 (두 파일의 모든 키를 포함하여 충돌 방지) ---
# 이 페이지에서 사용할 키
if "destination" not in st.session_state: st.session_state.destination = ""
if "start_date" not in st.session_state: st.session_state.start_date = None
if "end_date" not in st.session_state: st.session_state.end_date = None
if "user_preferences" not in st.session_state: st.session_state.user_preferences = {}
if "activity_level" not in st.session_state: st.session_state.activity_level = 3
if "preferences_collected" not in st.session_state: st.session_state.preferences_collected = False

# AI 플래너 페이지에서 사용할 키 (미리 초기화)
if "dates" not in st.session_state: st.session_state.dates = ""
if "preference" not in st.session_state: st.session_state.preference = ""
if "total_days" not in st.session_state: st.session_state.total_days = 1
if "current_planning_day" not in st.session_state: st.session_state.current_planning_day = 1
if "itinerary" not in st.session_state: st.session_state.itinerary = []
if "messages" not in st.session_state: st.session_state.messages = []


# --- 메인 화면에 입력 UI 구성 ---
st.subheader("1. 기본 정보")
destination_input = st.text_input("목적지", value=st.session_state.destination, placeholder="예: 부산, 제주도")

col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("출발일", value=st.session_state.start_date or date.today(), min_value=date.today())
with col2:
    end_date = st.date_input("귀가일", value=st.session_state.end_date or (start_date + timedelta(days=1)), min_value=start_date)

st.subheader("2. 여행 스타일")
gathering_type = st.selectbox("모임 성격", ["가족", "친구", "연인", "혼자"])
travel_style = st.selectbox("선호 스타일", ["맛집 탐방", "힐링/휴양", "액티비티", "문화/역사", "자연 감상"])
activity_level = st.slider("하루 활동량 (계획할 장소 수)", 1, 5, st.session_state.activity_level, help="1: 여유롭게(하루 1곳), 5: 빡빡하게(하루 5곳)")

st.markdown("---")

# --- 정보 저장 버튼 및 로직 ---
if st.button("AI 플래너에게 정보 전달하고 시작하기", type="primary", use_container_width=True):
    if destination_input and start_date and end_date:
        # 1. 폼 데이터를 st.session_state에 먼저 저장
        st.session_state.destination = destination_input
        st.session_state.start_date = start_date
        st.session_state.end_date = end_date
        st.session_state.activity_level = activity_level # 활동량 저장
        st.session_state.user_preferences = { "gathering_type": gathering_type, "travel_style": travel_style }

        # 2. AI 플래너가 사용할 st.session_state 키에 맞춰 데이터 가공
        days = (end_date - start_date).days
        travel_dates_str = f"{start_date.strftime('%Y년 %m월 %d일')}부터 {days+1}일간"
        st.session_state.dates = travel_dates_str
        st.session_state.total_days = days + 1 # 총 여행일수 저장

        # 여러 정보를 하나의 자연어 문장으로 결합하여 'preference' 생성
        pref_list = [
            f"- 이번 여행은 '{gathering_type}'와(과) 함께 가는 여행입니다.",
            f"- 주된 여행 스타일은 '{travel_style}'입니다."
        ]
        st.session_state.preference = "\n".join(pref_list)

        # 3. 플래너 페이지로 전환하기 위한 상태 설정 및 초기화
        st.session_state.preferences_collected = True
        st.session_state.messages = []
        st.session_state.itinerary = []
        st.session_state.current_planning_day = 1

        with st.spinner("AI 플래너 페이지로 이동합니다..."):
            time.sleep(1)
            # st.switch_page는 Streamlit 1.33.0 이상 버전에서 지원됩니다.
            st.switch_page("pages/1_trip_planner.py")

    else:
        st.error("목적지와 날짜는 반드시 입력해야 합니다.")
# pages/1_trip_planner.py

import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage
from src.graph_flow import build_graph
import re
import json
from datetime import datetime
from fpdf import FPDF
import time
import os

# --- 1. 헬퍼 함수 ---
def normalize_to_string(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                texts.append(str(item.get('text', '')))
            else:
                texts.append(str(item))
        return "\n".join(texts)
    return str(content)

# --- 2. PDF 생성 함수 ---
from fpdf.enums import XPos, YPos

def create_itinerary_pdf(itinerary, destination, dates, weather, final_routes, total_days):
    pdf = FPDF()
    pdf.add_page()
    
    font_path = 'NanumGothic.ttf'
    bold_font_path = 'NanumGothicBold.ttf'
    
    has_korean_font = False
    try:
        if os.path.exists(font_path):
            pdf.add_font('NanumGothic', '', font_path)
            if os.path.exists(bold_font_path):
                pdf.add_font('NanumGothic', 'B', bold_font_path)
            else:
                pdf.add_font('NanumGothic', 'B', font_path)
            
            pdf.set_font('NanumGothic', '', 12)
            has_korean_font = True
        else:
            print("⚠️ [PDF 생성] 폰트 파일이 없습니다. 기본 폰트(Arial)를 사용합니다.")
            pdf.set_font('Arial', '', 12)
    except Exception as e:
        print(f"⚠️ [PDF 생성] 폰트 로드 에러: {e}")
        pdf.set_font('Arial', '', 12)

    pdf.set_font_size(24)
    pdf.cell(0, 20, text=f"{destination} 여행 계획", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')

    pdf.set_font_size(12)
    pdf.cell(0, 10, text=f"기간: {dates}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')

    if weather and weather.strip() and weather != '정보 없음':
        pdf.set_font_size(10)
        pdf.multi_cell(0, 5, text=f"날씨: {weather}", align='C')

    pdf.ln(10)

    try:
        sorted_itinerary = sorted(itinerary, key=lambda x: (int(x.get('day', 1)), x.get('start', '00:00')))
    except:
        sorted_itinerary = itinerary

    for day_num in range(1, total_days + 1):
        pdf.set_font_size(18)
        if has_korean_font: pdf.set_font('NanumGothic', 'B', 18)
        
        pdf.cell(0, 15, text=f"Day {day_num}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        
        pdf.set_font_size(11)
        if has_korean_font: pdf.set_font('NanumGothic', '', 11)

        items_today = [item for item in sorted_itinerary if int(item.get('day', 1)) == day_num]
        
        if not items_today:
            pdf.cell(0, 10, text="  - 계획된 일정이 없습니다.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(5)
            continue

        for item in items_today:
            item_type = item.get('type', 'activity')

            if item_type == 'move':
                pdf.set_text_color(100, 100, 100)
                pdf.set_font_size(10)
                move_text = f"      |  {item.get('start', '')} ~ {item.get('end', '')} ({item.get('duration_text', '')}) : {item.get('transport', '이동')}"
                pdf.cell(0, 8, text=move_text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_text_color(0, 0, 0)
                pdf.set_font_size(11)
            else:
                time_info = f"[{item.get('start', '시간 미정')}-{item.get('end', '')}]" if item.get('start') else "[시간 미정]"
                
                if has_korean_font: pdf.set_font('NanumGothic', 'B', 12)
                main_text = f"  ● {time_info} {item.get('name', '이름 없음')} ({item.get('category', item_type)})"
                pdf.cell(0, 8, text=main_text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                
                if item.get('description'):
                    if has_korean_font: pdf.set_font('NanumGothic', '', 10)
                    pdf.set_x(20)
                    pdf.multi_cell(0, 5, text=f"{item['description']}")
                    pdf.ln(2)
        pdf.ln(10)

    return bytes(pdf.output())

# --- 3. 페이지 설정 및 세션 초기화 ---
st.set_page_config(page_title="AI 여행 플래너", layout="centered")
st.title("💬 AI 여행 플래너")

with st.sidebar:
    st.header("질문 가이드")
    st.markdown("""
    - "근처 관광지 추천해줘"
    - "맛집 알려줘"
    - "일정 수정하고 싶어"
    - "경로 최적화해줘"
    - "PDF로 만들어줘"
    """)

if "preferences_collected" not in st.session_state:
    st.warning("⚠️ 정보 입력 페이지에서 먼저 여행 정보를 입력해주세요.")
    if st.button("정보 입력 페이지로 돌아가기"):
        st.switch_page("app.py") # 또는 정보 입력 페이지의 실제 경로
    st.stop()

# 세션 상태 초기화
if "messages" not in st.session_state: st.session_state.messages = []
if "itinerary" not in st.session_state: st.session_state.itinerary = []
if "show_pdf_button" not in st.session_state: st.session_state.show_pdf_button = False
if "current_weather" not in st.session_state: st.session_state.current_weather = ""

# --- 4. 그래프 로드 ---
# 🚨 [수정] st.cache_resource 제거
def get_graph_app():
    return build_graph()

# 각 세션에서 새 그래프를 빌드
APP = get_graph_app()

# --- 5. AI 에이전트 실행 ---
def run_ai_agent():
    # 🚨 [중요] 스레드 ID를 세션마다 고유하게 설정
    thread_id = st.session_state.session_id if 'session_id' in st.session_state else "streamlit_user"
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
    
    # 그래프에 전달할 현재 상태
    current_state = {
        "messages": st.session_state.messages,
        "itinerary": st.session_state.itinerary,
        "destination": st.session_state.get('destination', ''),
        "dates": st.session_state.get('dates', ''),
        "preference": st.session_state.get('preference', ''),
        "total_days": st.session_state.get('total_days', 1),
        "activity_level": st.session_state.get('activity_level', 3),
        "current_weather": st.session_state.get('current_weather', ''),
        "show_pdf_button": st.session_state.get('show_pdf_button', False),
        "current_anchor": st.session_state.get('current_anchor', st.session_state.get('destination', ''))
    }
    
    with st.spinner("AI가 생각 중입니다..."):
        # invoke의 결과를 response 변수에 저장
        response = APP.invoke(current_state, config=config)

    # 🚨 [수정] 그래프의 최종 상태를 세션 상태에 통째로 업데이트
    st.session_state.messages = response.get('messages', [])
    st.session_state.itinerary = response.get('itinerary', [])
    st.session_state.current_weather = response.get('current_weather', '')
    st.session_state.show_pdf_button = response.get('show_pdf_button', False)
    st.session_state.current_anchor = response.get('current_anchor', '')

# --- 6. 초기 실행 ---
if not st.session_state.messages:
    if 'session_id' not in st.session_state:
        st.session_state.session_id = str(time.time()) # 고유 세션 ID 생성

    initial_prompt = f"""
    안녕하세요! 방금 입력한 정보를 바탕으로 여행 계획을 시작해주세요.
    - 목적지: {st.session_state.get('destination')}
    - 여행 기간: {st.session_state.get('dates')} (총 {st.session_state.get('total_days')}일)
    - 하루 목표 활동량: {st.session_state.get('activity_level')}곳
    - 나의 여행 스타일: {st.session_state.get('preference')}
    
    이제 위 정보를 바탕으로 전체 여행 계획을 추천해주세요.
    """
    st.session_state.messages.append(HumanMessage(content=initial_prompt))
    run_ai_agent()
    st.rerun()

# --- 7. 화면 출력 ---
for msg in st.session_state.messages:
    if isinstance(msg, HumanMessage):
        st.chat_message("user").markdown(msg.content)
    elif isinstance(msg, AIMessage):
        safe_content = normalize_to_string(msg.content)
        
        # [ADD_PLACE] 등 내부 태그 제거 후 출력
        cleaned_text = re.sub(r"\[(ADD|REPLACE|DELETE)_PLACE\].*?\[/\1_PLACE\]", "", safe_content, flags=re.DOTALL)
        cleaned_text = re.sub(r"\[STATE_UPDATE:.*?\]", "", cleaned_text)
        
        if cleaned_text.strip():
            st.chat_message("assistant").markdown(cleaned_text.strip())

# --- 8. PDF 다운로드 ---
if st.session_state.show_pdf_button:
    pdf_bytes = create_itinerary_pdf(
        st.session_state.itinerary,
        st.session_state.destination,
        st.session_state.dates,
        st.session_state.current_weather,
        "", # final_routes는 더 이상 직접 파싱하지 않음
        st.session_state.total_days
    )
    if pdf_bytes:
        st.download_button(
            label="📄 여행 계획 PDF 다운로드",
            data=pdf_bytes,
            file_name=f"{st.session_state.destination}_여행계획.pdf",
            mime="application/pdf"
        )

# --- 9. 사용자 입력 ---
if user_input := st.chat_input("메시지를 입력하세요..."):
    st.session_state.messages.append(HumanMessage(content=user_input))
    st.chat_message("user").markdown(user_input)
    run_ai_agent()
    st.rerun()
import streamlit as st

# 1. 페이지 기본 설정
st.set_page_config(
    page_title="AI 여행 플래너 | 메인 홈",
    page_icon="✈️",
    layout="centered",
    initial_sidebar_state="expanded" # 사이드바를 기본으로 열어둠
)

# 2. 메인 화면 구성
st.title("AI 여행 계획 프로젝트 🗺️")
st.header("방문을 환영합니다!")

st.markdown(
    """
    이 프로젝트는 멀티 에이전트(Multi-Agent) 기반의 AI 여행 플래너입니다.
    
    **왼쪽 사이드바**에서 '여행 계획 에이전트' 메뉴를 선택하여
    대화형 여행 계획을 시작할 수 있습니다.
    
    ---
    
    ### 🚀 주요 기능
    * **여행 정보 수집:** 목적지, 날짜, 취향을 파악합니다.
    * **날씨 브리핑:** 여행 날짜의 날씨를 조회하여 계획에 참고하도록 돕습니다.
    * **RAG 기반 추천:** 관광지와 식당을 벡터 DB 기반으로 추천합니다.
    * **경로 최적화:** 확정된 관광지들의 최적 방문 순서를 제안합니다.
    """
)

# 3. 사이드바 안내 (선택 사항)
st.sidebar.success("👆 위 메뉴를 선택하여 시작하세요.")
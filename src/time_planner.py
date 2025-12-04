# src/time_planner.py (전체 코드)

from typing import List, Dict, Any
import json # 파이썬 기본 json 모듈
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import JsonOutputParser
from src.config import LLM

# --- 1. 출력 스키마 정의 ---
# LLM이 생성할 JSON 결과의 형태를 정의합니다.
class TimedItineraryItem(Dict):
    """시간 계획이 추가된 일정 항목의 스키마"""
    day: int
    type: str # '식당', '관광지', '카페' 등
    name: str
    description: str
    # [핵심] LLM이 계산하여 추가할 타임라인 정보
    estimated_start_time: str # "오전 10:00"
    estimated_end_time: str # "오전 12:00"
    estimated_duration_minutes: int # 120분 (다음 경로 최적화에 사용될 값)

class TimedItinerary(Dict):
    """전체 시간 계획이 포함된 일정 목록"""
    timed_itinerary: List[TimedItineraryItem]

# --- 2. 시간 계산 프롬프트 정의 ---
TIMELINE_SYSTEM_PROMPT = """당신은 '여행 일정 시간 계산 전문가'입니다.
주어진 여행 일정 목록을 분석하여, 각 항목에 합리적인 활동 시간(시작 시간, 종료 시간, 소요 시간)을 할당해야 합니다.

### 지침:
1.  **출력 형식:** 반드시 요청된 JSON 스키마(timed_itinerary 리스트)를 사용하여 결과만 출력해야 합니다. 서론/결론/설명을 붙이지 마세요.
2.  **시간 가정:**
    * 여행 시작은 **오전 10:00**로 가정합니다.
    * **점심 식사 시간**은 **오후 12:00 ~ 13:00** 사이, **저녁 식사 시간**은 **오후 18:00 ~ 19:30** 사이로 설정하세요.
    * 식당(점심/저녁) 소요 시간은 **90분(1.5시간)**, 카페는 **60분(1시간)**으로 설정하세요.
    * 관광지는 **120분(2시간)**을 기본으로 설정하되, 중요한 장소는 180분(3시간)을 할당할 수 있습니다.
3.  **흐름:** 이전 항목의 'estimated_end_time'이 다음 항목의 'estimated_start_time'이 되도록 하세요. (이동 시간은 나중에 경로 최적화 단계에서 계산되므로 여기서는 무시합니다.)
4.  **필수:** 모든 항목에는 `estimated_duration_minutes`를 정수(분 단위)로 계산하여 포함해야 합니다.
5.  **입력:** 'day', 'type', 'name', 'description'이 있는 `itinerary` 리스트를 입력으로 받습니다.

### JSON 스키마:
{schema}
"""

# --- 3. 시간 플래너 체인 구축 ---

def create_time_planner_chain():
    """시간 계산 및 할당을 위한 LLM 체인을 구축합니다."""
    
    # JSON 파싱 핸들러
    parser = JsonOutputParser(pydantic_object=TimedItinerary)

    # 프롬프트 생성
    prompt = ChatPromptTemplate.from_messages([
        ("system", TIMELINE_SYSTEM_PROMPT.format(schema=parser.get_format_instructions())),
        ("human", "아래 여행 일정에 대해 시간 계획을 할당하세요:\n{itinerary_json_str}")
    ])
    
    # JSON 문자열을 입력으로 받는 RunnableLambda
    def itinerary_to_json_str(state: Dict[str, Any]) -> str:
        """state의 itinerary 리스트를 JSON 문자열로 변환합니다."""
        # 람다 함수에서 itinerary 키를 직접 받지 않고 state 딕셔너리를 받도록 조정
        return json.dumps(state['itinerary'], ensure_ascii=False, indent=2)

    # 체인 정의: JSON 문자열로 변환 -> LLM 호출 -> JSON 파싱
    time_planner_chain = (
        RunnableLambda(itinerary_to_json_str)
        .with_config(run_name="Itinerary_Serializer")
        | prompt
        | LLM.with_structured_output(parser.pydantic_object) # 구조화된 JSON 출력 강제
        | parser
    )
    
    # 이 체인은 { 'itinerary': List[Dict] }를 입력으로 받고 { 'timed_itinerary': List[TimedItineraryItem] }을 출력합니다.
    return time_planner_chain

# --- 4. 에이전트 도구 함수 정의 (tools.py에 등록될 함수) ---

def plan_itinerary_timeline(itinerary_json_str: str) -> str:
    """
    [Task 4] 주어진 전체 여행 일정(JSON 문자열)을 분석하여, 각 항목에 대해 
    합리적인 시작/종료 시간을 할당한 후 JSON 문자열로 반환합니다. 
    (이 결과는 경로 최적화 도구의 입력으로 사용됩니다.)
    """
    print(f"\n--- [DEBUG TIME PLANNER] 시간 계획 시작 ---")
    
    try:
        # JSON 문자열을 파이썬 리스트 객체로 변환
        itinerary = json.loads(itinerary_json_str)
    except json.JSONDecodeError:
        print("ERROR: 입력된 itinerary JSON 문자열 파싱 실패.")
        return "오류: 여행 일정 JSON 데이터를 읽을 수 없습니다."

    # 날짜와 시간에 따라 정렬하여 순서대로 계획해야 합니다.
    sorted_itinerary = sorted(itinerary, key=lambda x: x['day'])
    
    chain = create_time_planner_chain()
    
    try:
        # 체인 실행: 입력은 { 'itinerary': List[Dict] } 형식의 딕셔너리
        result = chain.invoke({"itinerary": sorted_itinerary})
        
        # LLM의 JSON 객체 응답을 다시 문자열로 변환하여 에이전트에게 전달
        final_json_str = json.dumps(result, ensure_ascii=False, indent=2)
        
        print(f"DEBUG: 생성된 시간 계획 JSON:\n{final_json_str}")
        return final_json_str
        
    except Exception as e:
        print(f"!!!!!!!!!! [DEBUG] 시간 계획 체인 오류 !!!!!!!!!!")
        print(f"DEBUG: Error details: {e}")
        return "오류: 여행 시간 계획을 계산하는 데 실패했습니다."
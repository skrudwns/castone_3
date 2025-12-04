# src/time_planner.py

from typing import List, Union, Dict
import json
from langchain_core.prompts import ChatPromptTemplate
# 🚨 [수정] 최신 LangChain 호환성을 위해 표준 pydantic 사용
from pydantic import BaseModel, Field 
from src.config import LLM

# --- 1. 출력 스키마 정의 ---
class TimedItineraryItem(BaseModel):
    day: int = Field(description="여행 일차")
    type: str = Field(description="장소 유형")
    name: str = Field(description="장소 이름")
    description: str = Field(description="장소 설명")
    estimated_start_time: str = Field(description="시작 시간 (예: 10:00)")
    estimated_end_time: str = Field(description="종료 시간 (예: 12:00)")
    estimated_duration_minutes: int = Field(description="소요 시간(분)")

class TimedItinerary(BaseModel):
    timed_itinerary: List[TimedItineraryItem] = Field(description="시간 정보가 할당된 전체 일정 리스트")

# --- 2. 프롬프트 ---
TIMELINE_SYSTEM_PROMPT = """당신은 '여행 일정 시간 계산 전문가'입니다.
주어진 여행 일정 목록을 분석하여, 각 항목에 합리적인 활동 시간(시작, 종료, 소요 시간)을 할당하세요.
시작은 10:00, 점심은 12:00~13:00, 저녁은 18:00~19:30 사이로 가정합니다.
"""

def create_time_planner_chain():
    prompt = ChatPromptTemplate.from_messages([
        ("system", TIMELINE_SYSTEM_PROMPT),
        ("human", "아래 여행 일정에 대해 시간 계획을 할당하세요:\n{itinerary_json_str}")
    ])
    # 최신 LangChain에서는 표준 Pydantic 모델을 지원합니다.
    chain = prompt | LLM.with_structured_output(TimedItinerary)
    return chain

# --- 3. 구현 함수 (이름: plan) ---
def plan(itinerary_input: Union[str, List[Dict]]) -> str:
    print(f"\n--- [DEBUG TIME PLANNER] 시간 계획 시작 ---")
    
    # 입력값 전처리 (리스트/문자열 모두 처리)
    try:
        if isinstance(itinerary_input, str):
            itinerary_data = json.loads(itinerary_input)
        else:
            itinerary_data = itinerary_input
            
    except json.JSONDecodeError:
        return "오류: 여행 일정 데이터 형식이 올바르지 않습니다."

    # 날짜순 정렬
    try:
        sorted_itinerary = sorted(itinerary_data, key=lambda x: x.get('day', 1))
    except:
        sorted_itinerary = itinerary_data

    chain = create_time_planner_chain()
    
    try:
        # LLM 호출
        result_obj = chain.invoke({"itinerary_json_str": json.dumps(sorted_itinerary, ensure_ascii=False)})
        
        # Pydantic v2의 경우 model_dump(), v1의 경우 dict()를 사용
        # 호환성을 위해 try-except로 처리하거나 dict() 사용
        try:
            final_list = [item.model_dump() for item in result_obj.timed_itinerary]
        except AttributeError:
            final_list = [item.dict() for item in result_obj.timed_itinerary]
            
        final_json_str = json.dumps(final_list, ensure_ascii=False, indent=2)
        
        print(f"DEBUG: 생성된 시간 계획 JSON:\n{final_json_str}")
        return final_json_str
        
    except Exception as e:
        print(f"DEBUG: Error details: {e}")
        return f"오류: 여행 시간 계획 실패 ({e})"
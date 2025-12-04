# src/graph_flow.py

from typing import TypedDict, Annotated, List, Literal, Dict, Any
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from src.config import LLM
from src.tools import AVAILABLE_TOOLS, TOOLS
import re 
import json

from langgraph.checkpoint.memory import MemorySaver 

CHECKPOINTER = MemorySaver()

def normalize_content_to_str(content: Any) -> str:
    if content is None: return ""
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text": parts.append(str(part["text"]))
            else: parts.append(str(part))
        return "\n".join(parts)
    if isinstance(content, dict): return json.dumps(content, ensure_ascii=False)
    return str(content)

# --- 1. 상태 정의 ---
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    current_weather: str
    itinerary: List[Dict]
    destination: str
    dates: str
    preference: str
    total_days: int
    activity_level: int
    current_planning_day: int
    show_pdf_button: bool
    next_node: Literal["InfoCollectorAgent", "WeatherAgent", "AttractionAgent", "RestaurantAgent", "DayTransitionAgent", "ConfirmationAgent", "PDFCreationAgent", "end_node"]

# --- 2. 전문 에이전트 정의 ---
def create_specialist_agent(system_prompt: str):
    prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("placeholder", "{messages}")])
    llm_with_tools = LLM.bind_tools(TOOLS)
    chain = prompt | llm_with_tools
    def agent_node(state: AgentState):
        state_summary = f"""
--- 현재 계획 상태 ---
날씨: {state.get('current_weather', '아직 모름')}
전체 확정 일정: {state.get('itinerary', [])}
여행지: {state.get('destination', '아직 모름')}
날짜: {state.get('dates', '아직 모름')}
취향: {state.get('preference', '아직 모름')}
총 여행일: {state.get('total_days', 1)}일
하루 목표 활동량: {state.get('activity_level', 3)}곳
현재 계획 중인 날짜: {state.get('current_planning_day', 1)}일차
---
"""
        current_messages = [HumanMessage(content=state_summary)] + state['messages']
        response = chain.invoke({"messages": current_messages})
        
        itinerary = state.get('itinerary', []).copy()
        content = normalize_content_to_str(getattr(response, "content", ""))

        final_itinerary_match = re.search(r"\[FINAL_ITINERARY_JSON\](.*)\[/FINAL_ITINERARY_JSON\]", content, re.DOTALL)
        if final_itinerary_match:
            try:
                itinerary = json.loads(final_itinerary_match.group(1).strip())
                print(f"DEBUG: SupervisorAgent가 최종 정리한 itinerary:\n{itinerary}")
            except Exception as e:
                print(f"ERROR: JSON 파싱 실패: {e}")

        match = re.search(r"'(.*?)'을/를 (\d+)일차 (관광지|식당|카페) 계획에 추가합니다", content)
        if match:
            name, day, type = match.groups()
            new_item = {'day': int(day), 'type': type, 'name': name, 'description': ''}
            if new_item not in itinerary: itinerary.append(new_item)

        show_pdf_button = state.get('show_pdf_button', False)
        if "[STATE_UPDATE: show_pdf_button=True]" in content: show_pdf_button = True

        return {"messages": [response], "itinerary": itinerary, "show_pdf_button": show_pdf_button}
    return agent_node

# --- 3. Supervisor (라우터) 정의 ---
def supervisor_router(state: AgentState):
    print("--- (Supervisor) 다음 작업 결정 ---")
    
    if not state.get('messages') or not state['messages']: return "InfoCollectorAgent"
    last_message = state['messages'][-1]
    
    # 1. ToolMessage 우선 처리 (브리핑)
    if isinstance(last_message, ToolMessage):
        print("Router -> SupervisorAgent (ToolMessage 결과 브리핑 요청)")
        return "SupervisorAgent" 

    # 2. 필수 정보 확인
    required_info = ['destination', 'dates', 'total_days', 'activity_level']
    if not all(state.get(key) for key in required_info): return "InfoCollectorAgent"
    if not state.get('current_weather'): return "WeatherAgent"

    # 3. [다음 턴 로직] 날씨/취향 완료 후 '다음 대화'가 들어오면 식당 추천으로 연결
    # (이번 턴은 END로 끝나서 사용자 입력을 기다리고, 사용자가 말하면 이 로직이 작동함)
    if state.get('current_weather') and state.get('preference') and not state.get('itinerary'):
        # 사용자가 날씨 브리핑을 듣고 "좋아", "추천해줘" 등 반응을 보이면 바로 식당 추천 시작
        print("Router -> RestaurantAgent (날씨/취향 확인됨 -> 식당 추천 시작)")
        return "RestaurantAgent"
    
    # 4. 하루 목표 및 전환
    current_day = state.get("current_planning_day", 1)
    activity_level = state.get("activity_level", 3)
    places_for_current_day = [p for p in state.get("itinerary", []) if p.get('day') == current_day]

    if len(places_for_current_day) >= activity_level:
        if current_day < state.get("total_days", 1): return "DayTransitionAgent"
        else: return "ConfirmationAgent"
            
    # 5. 대화 기반 라우팅
    if isinstance(last_message, HumanMessage):
        content = last_message.content.lower()
        if any(k in content for k in ["pdf", "파일", "정리"]): return "SupervisorAgent"
        if any(k in content for k in ["최적화", "순서", "경로"]): return "SupervisorAgent"
        if any(k in content for k in ["식당", "맛집", "카페", "먹고"]): return "RestaurantAgent"
        if any(k in content for k in ["관광", "장소", "구경"]): return "AttractionAgent"

    return "SupervisorAgent"

# --- 4. 에이전트 생성 ---
pdf_creation_prompt = "당신은 'PDF 문서 생성 전문가'입니다. 여행 계획이 완료되면 사용자에게 PDF 다운로드 버튼을 안내하세요. 응답 끝에 [STATE_UPDATE: show_pdf_button=True]를 포함하세요."
PDFCreationAgent = create_specialist_agent(pdf_creation_prompt)

supervisor_prompt = """당신은 AI 여행 플래너 팀의 '슈퍼바이저'입니다.
당신은 전문가 팀(날씨, 관광, 식당)을 관리하고, 사용자와의 상호작용을 총괄하며, 계획의 전체적인 흐름을 책임집니다.

### 주요 임무
1.  **계획 추가 확인:** 사용자가 장소를 선택하면(예: "1번 경희궁으로 할래"), **현재 계획 중인 날짜(`current_planning_day`)** 와 장소 유형('관광지' 또는 '식당')을 명시하여 확인 메시지를 반환하세요. 이 메시지는 시스템이 다음 행동을 결정하는 중요한 신호입니다.
    *   (관광지 예시): "네, '경희궁'을 **1일차 관광지** 계획에 추가합니다."
    *   (식당 예시): "좋습니다. '금화왕돈까스'를 **1일차 식당** 계획에 추가합니다."
2.  **하루 단위 경로 최적화:** 사용자가 "경로 최적화", "1일차 순서 짜줘" 등을 요청하면, 요청받은 날짜(`current_planning_day`)에 해당하는 장소 목록만 `itinerary`에서 추출하여 `optimize_and_get_routes` 도구를 호출하세요. 식당과 관광지를 모두 포함해야 합니다.
3.  **도구 결과 브리핑:** 모든 도구의 결과를 사용자에게 친절하게 요약하여 제시하고, 다음 행동(장소 선택, 취향 질문 등)을 유도하세요.
4.  **취향 질문:** `get_weather_forecast` 도구 결과를 받으면, 날씨 정보를 브리핑하고 날씨에 맞는 활동 '취향'을 질문하세요.
5.  **일반 대화:** 그 외 사용자의 일반적인 질문이나 애매한 요청에 응답합니다.
### 추가 임무 (★★매우 중요★★)
6.  **PDF용 콘텐츠 생성 및 최종 데이터 정리:** 사용자가 "pdf 만들어줘", "파일로 정리해줘" 등의 요청을 하면, 당신의 가장 중요한 임무가 시작됩니다.
    a. **전체 대화 기록(`messages`)과 현재 일정(`itinerary`)을 종합적으로 분석**하세요.
    b. 각 장소가 **왜 추천되었는지, 어떤 특징이 언급되었는지 대화의 맥락에서 파악**하세요. (예: "친구와 가기 좋은", "인테리어가 멋진", "비빔밥이 유명한")
    c. 이 맥락을 바탕으로, 각 장소에 대한 **1~2줄의 매력적인 설명(`description`)을 작성**하세요.
    d. 작성이 끝나면, **아래와 같은 JSON 형식을 사용하여, 설명이 추가된 '최종 itinerary' 전체를 반드시 출력**해야 합니다. 이 형식은 시스템이 PDF에 내용을 쓰는 유일한 방법입니다.

    --- 최종 데이터 출력 형식 (이 안에 최종 itinerary를 넣으세요) ---
    [FINAL_ITINERARY_JSON]
    [
      {{"day": 1, "type": "식당", "name": "경리단길", "description": "통이 터지도록 듬뿍 담아주는 비빔밥이 유명한 한식당입니다."}},
      {{"day": 1, "type": "카페", "name": "8IGHTY4OUR 카페", "description": "친구와 함께 방문하기 좋으며, 멋진 인테리어와 편안한 공간이 특징입니다."}},
      {{"day": 2, "type": "관광지", "name": "경복궁", "description": "한국의 역사를 느낄 수 있는 아름다운 궁궐입니다."}}
    ]
    [/FINAL_ITINERARY_JSON]
    ---

    e. 위 데이터 출력이 끝난 후, 사용자에게 "네, 대화 내용을 바탕으로 여행 계획을 상세하게 정리했습니다. 잠시 후 PDF 다운로드 버튼이 나타납니다." 라고 간단히 응답하세요.
"""
SupervisorAgent = create_specialist_agent(supervisor_prompt)

def day_transition_agent_node(state: AgentState):
    prompt = f"당신은 '플랜 전환 안내자'입니다. {state.get('current_planning_day')}일차 목표를 달성했습니다. 다음 날로 넘어갈까요? 응답 끝에 [STATE_UPDATE: increment_day=True]를 포함하세요."
    response = LLM.invoke(prompt)
    return {"messages": [response]}
DayTransitionAgent = day_transition_agent_node

confirmation_prompt = "당신은 '일정 확인 전문가'입니다. 모든 계획이 완료되었습니다. 이대로 확정할까요?"
ConfirmationAgent = create_specialist_agent(confirmation_prompt)

infocollector_prompt = "당신은 '정보 수집가'입니다. 목적지, 날짜, 인원, 스타일을 파악하세요."
InfoCollectorAgent = create_specialist_agent(infocollector_prompt)

attraction_prompt = "당신은 '관광지 전문가'입니다. search_attractions_and_reviews 도구를 사용하여 관광지를 추천하세요."
AttractionAgent = create_specialist_agent(attraction_prompt)

restaurant_prompt = "당신은 '식당 전문가'입니다. search_attractions_and_reviews 도구를 사용하여 맛집을 추천하세요."
RestaurantAgent = create_specialist_agent(restaurant_prompt)

weather_prompt = "당신은 '날씨 분석가'입니다. get_weather_forecast 도구를 호출하세요."
WeatherAgent = create_specialist_agent(weather_prompt)

# --- 5. 도구 실행 노드 ---
def call_tools(state: AgentState):
    last_message = state['messages'][-1]
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls: return {}
    tool_messages = []
    weather_update = state.get('current_weather')
    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_to_call = AVAILABLE_TOOLS.get(tool_name)
        if tool_to_call:
            try:
                output = tool_to_call.invoke(tool_call["args"])
                if tool_name == "get_weather_forecast": weather_update = output
            except Exception as e: output = f"Error: {e}"
        else: output = "Error: Tool not found"
        print(f"\n--- [DEBUG] Tool Output ({tool_name}): {str(output)[:200]}...")
        tool_messages.append(ToolMessage(content=str(output), tool_call_id=tool_call["id"]))
    return {"messages": tool_messages, "current_weather": weather_update}

# --- 6. 라우터 및 그래프 빌드 ---

def expert_router(state: AgentState):
    last_message = state['messages'][-1]
    
    if isinstance(last_message, AIMessage):
        content = last_message.content
        if content and "[FINAL_ITINERARY_JSON]" in content:
            print("Router -> PDFCreationAgent")
            return "PDFCreationAgent"
        if last_message.tool_calls:
            print(f"Router -> call_tools")
            return "call_tools"

    # [수정됨] 무조건 종료하여 사용자 입력을 기다립니다.
    # SupervisorAgent가 브리핑을 마치면 여기서 멈춥니다.
    print("Router -> END")
    return END

def build_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("SupervisorAgent", SupervisorAgent)
    workflow.add_node("InfoCollectorAgent", InfoCollectorAgent)
    workflow.add_node("WeatherAgent", WeatherAgent)
    workflow.add_node("AttractionAgent", AttractionAgent)
    workflow.add_node("RestaurantAgent", RestaurantAgent)
    workflow.add_node("DayTransitionAgent", DayTransitionAgent)
    workflow.add_node("ConfirmationAgent", ConfirmationAgent)
    workflow.add_node("PDFCreationAgent", PDFCreationAgent)
    workflow.add_node("call_tools", call_tools)

    entry_points = {
        "InfoCollectorAgent": "InfoCollectorAgent",
        "WeatherAgent": "WeatherAgent",
        "AttractionAgent": "AttractionAgent",
        "RestaurantAgent": "RestaurantAgent",
        "SupervisorAgent": "SupervisorAgent",
        "DayTransitionAgent": "DayTransitionAgent",
        "ConfirmationAgent": "ConfirmationAgent",
        "PDFCreationAgent": "PDFCreationAgent",
        "end_node": END
    }
    workflow.set_conditional_entry_point(supervisor_router, entry_points)
    
    common_edge_mapping = {
        "call_tools": "call_tools", 
        END: END, 
        "PDFCreationAgent": "PDFCreationAgent"
        # "retry" 제거됨
    }

    for agent in ["InfoCollectorAgent", "WeatherAgent", "AttractionAgent", "RestaurantAgent", "SupervisorAgent"]:
        workflow.add_conditional_edges(agent, expert_router, common_edge_mapping)

    workflow.add_edge("DayTransitionAgent", END)
    workflow.add_edge("ConfirmationAgent", END)
    workflow.add_edge("PDFCreationAgent", END)
    workflow.add_conditional_edges("call_tools", supervisor_router, entry_points)

    return workflow.compile(checkpointer=CHECKPOINTER)
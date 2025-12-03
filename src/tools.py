# src/tools.py (전체 코드)

import os, json
import requests  # API 호출용
import datetime
import re 
from typing import List, Any 

from langchain_core.tools import tool
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.load import dumps, loads
from src.config import LLM, load_faiss_index, GMAPS_CLIENT
from src.region_cut_fuzz import normalize_region_name # 👈 [핵심] 정규화 함수 임포트
from itertools import permutations
from src.search import RegionPreFilteringRetriever  
from src.time_planner import TimedItinerary, plan

# --- 헬퍼 함수 (변경 없음) ---

def normalize_message_to_str(message: Any) -> str:
    """LLM / LangChain 메시지나 content를 항상 str로 변환."""
    if message is None:
        return ""
    if hasattr(message, "content"):
        return normalize_message_to_str(message.content)
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = []
        for part in message:
            if isinstance(part, dict):
                if part.get("type") == "text" and "text" in part:
                    parts.append(str(part["text"]))
                else:
                    parts.append(str(part))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    if isinstance(message, dict):
        try:
            return json.dumps(message, ensure_ascii=False)
        except TypeError:
            return str(message)
    return str(message)

def format_docs(docs):
    """검색된 Document 객체를 LLM 프롬프트용 문자열로 변환합니다."""
    return "\n\n".join(doc.page_content for doc in docs)

# --- RAG 체인 구성 (쿼리 생성 부분은 유지) ---

# 2. RAG-Fusion용 쿼리 생성 프롬프트
template = """
당신은 AI 여행/맛집 추천 조수입니다. 
주어진 사용자 질문에 대해 전국 관광지와 식당 리뷰 데이터를 기반으로 관련 정보를 찾을 수 있도록 다섯 가지 다른 버전을 생성하세요.
각 질문은 새 줄로 구분하여 제공하세요. 원본 질문: {question}
"""
prompt_perspectives = ChatPromptTemplate.from_template(template)


# 3. LLM의 쿼리 생성 결과를 정제하는 헬퍼 함수
def clean_generated_queries(text: str) -> List[str]:
    """LLM이 생성한 쿼리 문자열에서 실제 쿼리만 정리하여 리스트로 반환합니다."""
    queries = []
    for line in text.split("\n"):
        cleaned_line = re.sub(r"^\d+[:.]\s*", "", line).strip()
        if not cleaned_line:
            continue
        if cleaned_line.startswith("다음은") or cleaned_line.startswith("원본 질문"):
            continue
        queries.append(cleaned_line)
    return queries

# 4. 쿼리 생성 체인
generate_queries = (
    prompt_perspectives
    | LLM
    | StrOutputParser()
    | clean_generated_queries 
)

# 6. RAG 후보 목록 생성 프롬프트 (👈 [수정] 추천 다양화 및 카테고리화)
final_prompt = ChatPromptTemplate.from_template(
    """당신은 전문 여행 가이드입니다. 제공된 [검색 결과]를 바탕으로 사용자 질문에 답하세요.

[지침]
1. **다양한 추천:** 검색 결과를 최대한 활용하여 최소 3~5곳 이상의 장소를 추천하세요.
2. **카테고리화:** 추천 장소를 성격에 맞게 분류하세요. (예: 🏠 실내 활동, 🍽️ 한식 맛집, ☕ 오션뷰 카페, 📸 포토 스팟 등)
3. **상세 설명:** 각 장소마다 매력 포인트, 특징, 추천 메뉴 등을 1~2문장으로 요약하여 설명하세요.
4. **지역 확인:** 사용자가 요청한 지역이 맞는지 확인하고, 타 지역은 답변에서 언급하지 마세요. (검색 결과에 타 지역이 있다면 반드시 제외해야 합니다.)

--- [검색 결과(Context)] ---
{context}

--- 사용자 질문 ---
{question}

--- 답변 형식 (아래 형식 준수) ---
### 🏠 [카테고리 이름 1]
1. **[장소명]**
   - 설명: [상세 설명 및 특징]
   
### 🍽️ [카테고리 이름 2]
1. **[장소명]**
   - 설명: [상세 설명 및 특징]

... (필요한 만큼 카테고리를 나누고 반복)
"""
)

# 7. RAG 최종 생성 체인
final_generation_chain = (
    final_prompt
    | LLM
    | StrOutputParser()
)

# --- 4. 에이전트가 사용할 '도구(Tools)' 정의 ---

@tool
# 👈 [핵심 수정 1] destination 인자 추가 (Streamlit 종속성 제거)
def search_attractions_and_reviews(query: str, destination: str) -> str:
    """
    사용자 쿼리를 5개로 확장하고, '각 쿼리별 Top-3' 결과를 결합하여 후보 목록을 검색합니다. 
    (지역 필터링 적용)
    """
    print(f"\n--- [DEBUG RAG] RAG 검색 시작 ---") 
    
    # 1. 목적지 정규화 및 필터링 값 설정 (👈 지역 필터링 핵심)
    target_city = ""
    try:
        raw_destination = destination # 인자로 받은 destination 사용
        target_city = normalize_region_name(raw_destination)
        
        if target_city:
            print(f"DEBUG_RAG_3: 🔒 리트리버에 고정 지역 전달: {target_city}") 
        else:
            print("DEBUG_RAG_3: 🔓 목적지 정보 없음. 전국 검색으로 진행.")
    except Exception as e:
        print(f"DEBUG_RAG_ERROR: 지역 정규화 오류: {e}")

    try:
        DB = load_faiss_index() # 캐시된 DB 로드
        FAISS_RETRIEVER = RegionPreFilteringRetriever(
            vectorstore=DB, 
            k=15,  # 👈 [수정] k=15로 늘려 충분한 데이터를 제공
            fixed_location=target_city # 👈 정규화된 지역명 전달 (대구 차단)
        )        
        retrieval_only_chain = FAISS_RETRIEVER.map() 
    except Exception as e:
        print(f"!!!!!!!!!! [DEBUG] FAISS 인덱스 로드 실패 !!!!!!!!!!")
        return "오류: RAG 벡터 데이터베이스를 로드하는 데 실패했습니다."
    
    # 1. 5개 쿼리 생성 및 정제
    generated_queries = generate_queries.invoke(query)
    
    # 2. RAG 병렬 검색
    parallel_search_results = retrieval_only_chain.invoke(generated_queries)
    
    # 3. Top-3 결과 결합 (중복 제거) 👈 [수정] 쿼리당 Top-3을 뽑아옴
    top_1_docs = []
    seen_content = set()
    for doc_list in parallel_search_results:
        # 각 쿼리 결과 리스트에서 Top-3을 뽑아 LLM에게 전달 (추천 다양화)
        for doc in doc_list[:3]: 
            if doc.page_content not in seen_content:
                top_1_docs.append(doc)
                seen_content.add(doc.page_content)
    
    # 4. LLM 요약 (최종 후보 목록 생성)
    context_str = format_docs(top_1_docs)
    
    if not context_str:
        return "오류: RAG 검색 결과가 없습니다. (벡터DB에 관련 내용 없음)"

    input_for_final_chain = {"context": context_str, "question": query}
    
    final_result = final_generation_chain.invoke(input_for_final_chain)
    
    return final_result

@tool
# 👈 [핵심 수정 2] 날씨 문제 해결: 5일치 모두 전달
def get_weather_forecast(destination: str, dates: str) -> str:
    """
    도시명(destination)으로 위도/경도를 조회하고, 그 좌표로 OWM이 제공하는 5일 예보를 모두 가져와
    LLM에게 전달합니다. (LLM이 여행 기간에 맞춰 요약하도록 유도)
    """
    print(f"\n--- [DEBUG WEATHER] 날씨 검색 시작 ---")
    print(f"DEBUG_W_1: Agent 전달 목적지: {destination}")
    print(f"DEBUG_W_2: Agent 전달 기간: {dates}")
    
    API_KEY = os.getenv("OWM_API_KEY")
    if not API_KEY:
        return "오류: OWM_API_KEY가 .env 파일에 설정되지 않았습니다."

    # 1단계: Geocoding (좌표 구하기)
    geo_url = "https://api.openweathermap.org/geo/1.0/direct"
    geo_params = {'q': f"{destination},KR", 'limit': 1, 'appid': API_KEY}
    lat, lon = None, None
    try:
        response = requests.get(geo_url, params=geo_params, timeout=5)
        response.raise_for_status()
        geo_data = response.json()
        if geo_data:
            lat, lon = geo_data[0]['lat'], geo_data[0]['lon']
        else:
            return f"오류: '{destination}'의 좌표(Geocoding)를 찾을 수 없습니다."
    except Exception as e:
        return f"오류: Geocoding API 호출 중 문제 발생: {e}"

    # 2단계: Forecast (5일 예보 데이터 가져오기)
    forecast_url = "https://api.openweathermap.org/data/2.5/forecast"
    forecast_params = {'lat': lat, 'lon': lon, 'appid': API_KEY, 'units': 'metric', 'lang': 'kr'}
    forecasts = None
    try:
        response = requests.get(forecast_url, params=forecast_params, timeout=10)
        response.raise_for_status()
        forecast_data = response.json()
        forecasts = forecast_data.get('list', [])
    except Exception as e:
        return f"오류: Forecast API 호출 중 문제 발생: {e}"
    if not forecasts:
        return "오류: Forecast API에서 'list' 데이터를 찾을 수 없습니다."

    # 3단계: OWM의 5일치 예보를 LLM에게 모두 전달
    summary = []
    seen_dates = set()

    for item in forecasts:
        dt_txt = item['dt_txt']
        date_part = dt_txt.split(" ")[0]
        time_part = dt_txt.split(" ")[1]
        
        # 날짜별 대표 예보만 수집 (정오 기준 또는 최초 데이터)
        if "12:00:00" in time_part or date_part not in seen_dates:
            temp = item['main']['temp']
            desc = item['weather'][0]['description']
            seen_dates.add(date_part)
            summary.append(f"- {date_part} 정오 기준: {temp:.1f}℃, {desc}")
            
    result_text = "\n".join(summary)
    
    print(f"DEBUG_W_3: LLM에게 전달될 OWM 5일치 데이터:\n{result_text}")
    
    # LLM에게 5일치 정보를 다 주고, 사용자 날짜에 맞는 것만 골라 쓰라고 지시
    return f"[{destination} 5일치 날씨 예보 데이터]\n{result_text}\n\n[사용자 요청 기간: {dates}]\n(위 데이터 중 여행 기간에 해당하는 날짜만 골라서 답변하세요.)"
    
# --- (나머지 도구 함수는 그대로 유지) ---
def get_detailed_route(start_place: str, end_place: str, mode="transit"):
    # (코드 내용 변경 없음. 인자만 사용)
    # ...
    # (기존 코드 유지)
    # ...
    if not GMAPS_CLIENT:
        print("DEBUG: GMAPS_CLIENT가 설정되지 않았습니다.")
        return None
    
    try:
        directions_result = GMAPS_CLIENT.directions(
            origin=start_place,
            destination=end_place,
            mode=mode,
            departure_time=datetime.datetime.now(),
            region="KR",
            language="ko"
        )
        
        if not directions_result:
            return None

        route = directions_result[0]['legs'][0]
        duration = route['duration']['text']
        distance = route['distance']['text']
        
        steps_summary = []
        for step in route['steps']:
            travel_mode = step['travel_mode']
            
            if travel_mode == 'TRANSIT':
                transit_details = step['transit_details']
                line_name = transit_details['line'].get('short_name') or transit_details['line'].get('name')
                vehicle_type = transit_details['line']['vehicle']['type']
                steps_summary.append(f"[{vehicle_type}] {line_name}")
            
            elif travel_mode == 'WALKING':
                if step['duration']['value'] > 300: 
                    steps_summary.append(f"🚶 도보 {step['duration']['text']}")

        return {
            "duration": duration,
            "distance": distance,
            "steps": steps_summary
        }

    except Exception as e:
        print(f"ERROR: 상세 경로 조회 중 오류 발생: {e}")
        return None

@tool
def optimize_and_get_routes(places: List[str]) -> str:
    """
    여러 장소(places)의 최적 방문 순서를 'distance_matrix' API로 계산하고,
    '최적 순서'와 '예상 총 이동 시간'만 반환합니다. (상세 경로X)
    """
    if not GMAPS_CLIENT:
        return "오류: Google Maps API 키가 설정되지 않아 경로를 조회할 수 없습니다."
    
    if not places or len(places) < 2:
        return "오류: 경로를 최적화하려면 2개 이상의 장소가 필요합니다."

    # 👈 [디버그] 함수명 변경 식별
    print(f"\n--- [DEBUG] optimize_and_get_routes (v2 - 상세경로 제외) 호출됨 ---") 
    print(f"DEBUG: Input places: {places}")

    # --- 1단계: Distance Matrix API 호출 ---
    now = datetime.datetime.now()
    try:
        print("DEBUG: Distance Matrix API 호출 시도...")
        matrix_result = GMAPS_CLIENT.distance_matrix(origins=places,
                                                     destinations=places,
                                                     mode="transit",
                                                     departure_time=now)
        print("DEBUG: Distance Matrix API 호출 성공.")
    except Exception as e:
        print(f"!!!!!!!!!! [DEBUG] optimize_and_get_routes (Matrix API) 예외 발생 !!!!!!!!!!")
        print(f"DEBUG: Error details: {e}")
        return f"오류: Google Distance Matrix API 호출 중 문제 발생: {e}"

    # --- 2단계: 경로 최적화 (단순화된 TSP) ---
    try:
        print("DEBUG: Distance Matrix 결과 파싱 및 최적화 시작...")
        duration_matrix = []
        
        for i, row in enumerate(matrix_result['rows']):
            duration_row = []
            for j, el in enumerate(row['elements']):
                if el['status'] == 'OK':
                    duration_val = el['duration']['value']
                    duration_row.append(duration_val)
                else:
                    print(f"DEBUG: [ {places[i]} -> {places[j]} ] 구간 경로 없음 (Status: {el['status']})")
                    duration_row.append(float('inf')) 
            duration_matrix.append(duration_row)
        
        print(f"DEBUG: 완성된 Duration Matrix (초): {duration_matrix}")

        min_duration = float('inf')
        best_order_indices = []
        other_indices = list(range(1, len(places))) 
        
        for p in permutations(other_indices):
            current_order_indices = [0] + list(p) 
            current_duration = 0
            for i in range(len(current_order_indices) - 1):
                origin_idx = current_order_indices[i]
                dest_idx = current_order_indices[i+1]
                current_duration += duration_matrix[origin_idx][dest_idx]
            if current_duration < min_duration:
                min_duration = current_duration
                best_order_indices = current_order_indices

        if min_duration == float('inf'):
            print("DEBUG: 최적화 실패 (모든 경로에 유효한 값이 없어 'inf'만 존재)")
            return "오류: 장소 간의 유효한 대중교통 경로를 찾을 수 없어 최적화에 실패했습니다."
            
        optimized_places = [places[i] for i in best_order_indices]
        print(f"DEBUG: 최적화된 순서: {optimized_places}")

    except KeyError as e:
        print(f"!!!!!!!!!! [DEBUG] optimize_and_get_routes (Matrix 파싱) 예외 발생 !!!!!!!!!!")
        print(f"DEBUG: Error details: KeyError {e}")
        return f"오류: Distance Matrix 결과 파싱 중 문제 발생: {e}"
    except Exception as e:
        print(f"!!!!!!!!!! [DEBUG] optimize_and_get_routes (최적화 로직) 예외 발생 !!!!!!!!!!")
        print(f"DEBUG: Error details: {e}")
        return f"오류: 경로 최적화 로직 중 알 수 없는 문제 발생: {e}"

    # --- 3단계: 상세 경로 없이 결과 요약 ---
    
    output_str = f"--- 🗺️ 최적 경로 제안 (총 {len(optimized_places)}곳) ---\n"
    output_str += f"계산된 최적 순서: {' → '.join(optimized_places)}\n"
    output_str += f"예상 총 이동 시간(대중교통): 약 {min_duration // 60} 분\n"
    output_str += "(참고: '총 이동 시간'은 장소 간 이동 시간의 합이며, 장소에서 머무는 시간은 제외된 수치입니다.)"

    print("DEBUG: optimize_and_get_routes (v2) 성공적으로 완료. (상세 경로 제외)")
    return output_str

@tool
def plan_itinerary_timeline(itinerary: List[Dict]) -> str:
    """
    주어진 전체 여행 일정(식당, 관광지)을 분석하여, 각 항목에 대해 
    합리적인 시작/종료 시간을 할당한 후 JSON 문자열로 반환합니다. 
    이 결과는 경로 최적화 도구의 입력으로 사용됩니다.
    """
    print(f"\n--- [DEBUG TIME PLANNER] 시간 계획 시작 (총 {len(itinerary)}곳) ---")
    
    # 날짜와 시간에 따라 정렬하여 순서대로 계획해야 합니다.
    sorted_itinerary = sorted(itinerary, key=lambda x: x['day'])
    
    chain = create_time_planner_chain()
    
    try:
        # 체인 실행: 입력은 { 'itinerary': List[Dict] } 형식의 딕셔너리
        result = chain.invoke({"itinerary": sorted_itinerary})
        
        # [수정] JSON 객체를 다시 문자열로 변환하여 LLM에게 전달 (도구는 문자열을 반환해야 함)
        final_json_str = json.dumps(result, ensure_ascii=False, indent=2)
        
        print(f"DEBUG: 생성된 시간 계획 JSON:\n{final_json_str}")
        return final_json_str
        
    except Exception as e:
        print(f"!!!!!!!!!! [DEBUG] 시간 계획 체인 오류 !!!!!!!!!!")
        print(f"DEBUG: Error details: {e}")
        return "오류: 여행 시간 계획을 계산하는 데 실패했습니다."

# 에이전트가 사용할 도구 목록
TOOLS = [search_attractions_and_reviews, get_weather_forecast, optimize_and_get_routes]
AVAILABLE_TOOLS = {tool.name: tool for tool in TOOLS}
# src/tools.py

import os, json
import requests
import datetime
import re 
from typing import List, Any, Dict

from langchain_core.tools import tool
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.load import dumps, loads
from src.config import LLM, load_faiss_index, GMAPS_CLIENT

from itertools import permutations
from src.search import RegionPreFilteringRetriever
from src.scheduler.smart_scheduler import SmartScheduler  # 👈 [핵심] 사용자의 로직 임포트

# 🚨 [중요] time_planner에서 plan 함수 임포트 (이전 ImportError 해결)
from src.time_planner import plan 


# --- 헬퍼 함수 ---

def get_admin_district_from_coords(lat: float, lng: float) -> str:
    """
    좌표를 통해 '광역+기초' 행정구역을 찾습니다.
    Result #0에 정보가 부족하면 Result #1, #2...를 순회하며 보완합니다.
    """
    if not GMAPS_CLIENT: return ""

    try:
        # 1. API 호출
        results = GMAPS_CLIENT.reverse_geocode((lat, lng), language='ko')
        if not results: return ""

        # 2. 가장 적절한 행정구역 찾기
        best_do = ""
        best_si_gu = ""

        # 결과 리스트를 순회 (보통 상위 5개 안에 다 있음)
        for i, result in enumerate(results[:5]):
            comps = result.get('address_components', [])
            
            current_do = ""
            current_si_gu = ""

            # 컴포넌트 분석
            for comp in comps:
                types = comp.get('types', [])
                if 'administrative_area_level_1' in types:
                    current_do = comp['long_name']
                elif 'sublocality_level_1' in types:
                    current_si_gu = comp['long_name']
                elif 'locality' in types:
                    # 구(sublocality)가 아직 없을 때만 시(locality) 채택
                    if not current_si_gu:
                        current_si_gu = comp['long_name']
            
            # [전략 A] 이번 결과에 '광역'과 '기초'가 둘 다 있다면 이게 베스트! -> 즉시 반환
            if current_do and current_si_gu:
                print(f"DEBUG: ✅ Result #{i}에서 완벽한 행정구역 발견: {current_do} {current_si_gu}")
                return f"{current_do} {current_si_gu}".strip()
            
            # [전략 B] 둘 다 있는 완벽한 결과가 없을 경우를 대비해, 정보를 모아둠 (백업)
            if not best_do and current_do:
                best_do = current_do
            if not best_si_gu and current_si_gu:
                best_si_gu = current_si_gu

        # 반복문을 다 돌았는데도 완벽한 세트가 없으면, 모아둔 정보라도 조합해서 반환
        final_result = f"{best_do} {best_si_gu}".strip()
        print(f"DEBUG: ⚠️ 완벽한 매칭 실패. 조합된 결과 사용: {final_result}")
        return final_result

    except Exception as e:
        print(f"DEBUG: 📍 행정구역 변환 중 오류: {e}")
        return ""

def get_coordinates(location_name: str):
    """지명(예: 초량동)으로 위경도 좌표 획득"""
    if not GMAPS_CLIENT: return None, None
    try:
        # 쿼리에 한국 텍스트임을 명시
        res = GMAPS_CLIENT.geocode(location_name, language='ko')
        if res:
            loc = res[0]['geometry']['location']
            return loc['lat'], loc['lng']
    except Exception as e:
        print(f"DEBUG: 좌표 변환 실패 ({location_name}): {e}")
    return None, None


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

# --- RAG 체인 구성 ---

template = """
당신은 AI 여행/맛집 추천 조수입니다. 
주어진 사용자 질문에 대해 전국 관광지와 식당 리뷰 데이터를 기반으로 관련 정보를 찾을 수 있도록 다섯 가지 다른 버전을 생성하세요.
각 질문은 새 줄로 구분하여 제공하세요. 원본 질문: {question}
"""
prompt_perspectives = ChatPromptTemplate.from_template(template)

def clean_generated_queries(text: str) -> List[str]:
    queries = []
    for line in text.split("\n"):
        cleaned_line = re.sub(r"^\d+[:.]\s*", "", line).strip()
        if not cleaned_line: continue
        if cleaned_line.startswith("다음은") or cleaned_line.startswith("원본 질문"): continue
        queries.append(cleaned_line)
    return queries

generate_queries = (
    prompt_perspectives | LLM | StrOutputParser() | clean_generated_queries 
)

final_prompt = ChatPromptTemplate.from_template(
    """당신은 AI 여행 가이드의 검색 조수입니다.
제공된 맥락(리뷰 데이터)을 참고하여, 사용자 질문에 대한 '후보 장소 목록'을 생성하세요.

지침:
1.  결과는 반드시 '후보 목록' 형식이어야 합니다.
2.  최대 5개의 후보를 제시하세요.
3.  각 후보는 [이름]과 [핵심 특징(한 줄 요약)]만 포함해야 합니다.

--- 맥락 ---
{context}

--- 사용자 질문 ---
{question}

--- 후보 목록 (이 형식 준수) ---
1. [장소 이름]: [특징 요약]
2. [장소 이름]: [특징 요약]
3. [장소 이름]: [특징 요약]
"""
)

final_generation_chain = (
    final_prompt | LLM | StrOutputParser()
)

# --- 4. 에이전트가 사용할 '도구(Tools)' 정의 ---

def get_location_admin_area(place_name: str) -> str:
    """
    장소 이름(예: 신라호텔)을 받아 행정 구역(예: 제주특별자치도 서귀포시)을 반환합니다.
    """
    if not GMAPS_CLIENT or not place_name:
        return ""
    
    try:
        # 구글 지오코딩 API 호출
        geocode_result = GMAPS_CLIENT.geocode(place_name, language='ko')
        
        if not geocode_result:
            return ""
            
        # 주소 컴포넌트 분석
        # (format: '제주특별자치도 서귀포시 중문관광로72번길 75')
        address_components = geocode_result[0].get('address_components', [])
        
        admin_area_1 = "" # 도/광역시 (예: 제주특별자치도)
        locality = ""     # 시/군/구 (예: 서귀포시)
        sublocality = ""  # 동/읍/면 (예: 색달동)
        
        for component in address_components:
            types = component.get('types', [])
            if 'administrative_area_level_1' in types:
                admin_area_1 = component['long_name']
            elif 'locality' in types:
                locality = component['long_name']
            elif 'sublocality_level_1' in types or 'sublocality' in types:
                sublocality = component['long_name']
        
        # 가장 구체적인 지역 정보를 조합하여 반환
        # 예: "서귀포시 색달동" 또는 "제주특별자치도 서귀포시"
        region_info = f"{admin_area_1} {locality} {sublocality}".strip()
        print(f"DEBUG: '{place_name}'의 위치 파악 -> {region_info}")
        return region_info

    except Exception as e:
        print(f"DEBUG: 위치 파악 중 오류: {e}")
        return ""

@tool
def search_attractions_and_reviews(query: str, destination: str = "", start_location: str = "") -> str:
    """
    관광지/맛집 정보를 검색합니다.
    Google Maps를 활용해 지명(POI)을 정확한 행정구역으로 변환합니다.
    만약 출발지가 있다면, 출발지의 지역 정보를 활용해 목적지의 모호성을 해결합니다.
    """
    print(f"\n--- [DEBUG] search_attractions_and_reviews 호출 ---")
    
    # 1. 초기 타겟 설정
    target_location = destination
    
    # [핵심 수정] "서면" -> "부산 서면"으로 만들기 위한 문맥 보정 로직
    if start_location and destination:
        # 1) 출발지의 행정구역을 먼저 파악 (예: 부산역 -> 부산광역시 동구)
        start_lat, start_lng = get_coordinates(start_location)
        if start_lat and start_lng:
            start_admin = get_admin_district_from_coords(start_lat, start_lng)
            
            # 2) 광역 지자체명 추출 (예: "부산광역시 동구" -> "부산광역시")
            if start_admin:
                start_province = start_admin.split()[0] # 첫 단어만 추출
                
                # 3) 목적지가 너무 짧거나 모호하면(2글자 이하), 출발지 광역명을 앞에 붙임
                # 예: "서면"(2글자) -> "부산광역시 서면" (이렇게 하면 구글이 부산 서면을 찾음)
                if len(destination) <= 3: # 서면, 남산 등 짧은 지명
                    print(f"DEBUG: 💡 모호한 지명 보정: '{destination}' + 출발지('{start_province}')")
                    # 검색어 자체를 "부산광역시 서면"으로 변경
                    target_location = f"{start_province} {destination}"

    if not target_location and start_location:
         target_location = get_location_admin_area(start_location)

    if not target_location:
        target_location = query
    
    print(f"DEBUG: 🎯 최종 좌표 검색어: '{target_location}'")

    # 2. [Step 1] Google Maps 기반 행정구역 표준화
    standardized_region = ""
    
    # 보정된 검색어(예: 부산광역시 서면)로 좌표를 따면 -> 부산 서면 좌표가 나옴
    lat, lng = get_coordinates(target_location)
    
    if lat and lng:
        # 좌표 -> 행정구역 변환 (이제 부산진구 부전동 쪽 행정구역이 나올 것임)
        standardized_region = get_admin_district_from_coords(lat, lng) # 아까 만든 스마트 머지 함수 사용
        print(f"DEBUG: 🔄 표준화 변환: '{target_location}' -> '{standardized_region}'")
    
    final_region_filter = standardized_region if standardized_region else target_location
    # 내부 검색 함수 정의
    def run_search(region_filter, use_filter=True):
        try:
            DB = load_faiss_index()
            # 쿼리 중복 방지 (부산 수영구 부산 수영구 맛집... 방지)
            search_query = query
            if region_filter and region_filter not in query:
                search_query = f"{region_filter} {query}"
            
            if use_filter and region_filter:
                # 필터 적용 검색
                retriever = RegionPreFilteringRetriever(
                    vectorstore=DB, k=5, fixed_location=region_filter
                )
                print(f"DEBUG: 🔍 필터 검색 실행 (필터: {region_filter})")
            else:
                # 필터 미적용 (전체 검색) - retriever 대신 직접 vectorstore 사용이 나을 수 있음
                # 여기서는 필터값 None으로 주어 필터링 패스 유도
                retriever = RegionPreFilteringRetriever(
                    vectorstore=DB, k=5, fixed_location=None
                )
                print(f"DEBUG: 🔓 필터 해제(Fallback) 검색 실행 (쿼리: {search_query})")
                
            return retriever.invoke(search_query)
        except Exception as e:
            print(f"DEBUG: 검색 에러: {e}")
            return []

    # 3. [Step 2] 정밀 필터 검색 시도
    docs = run_search(final_region_filter, use_filter=True)

    # 4. [Step 3] 결과 0건일 때 Fallback (필터 해제 검색)
    if not docs:
        print(f"DEBUG: 🚨 정밀 검색 결과 없음. Fallback(전체 검색) 시도...")
        
        # 필터 없이 검색하되, 쿼리에 지역명을 강력하게 포함시켜야 함
        # 예: 필터 없이 "카페"만 찾으면 안됨 -> "광안리 카페"로 찾아야 함
        docs = run_search(target_location, use_filter=False)
        
        if docs:
             # Fallback 결과가 엉뚱한 지역(예: 제주도)일 수 있으므로 
             # 텍스트 내에 원래 지명이 포함되었는지 간단히 체크해주면 좋음 (옵션)
             filtered_fallback = [d for d in docs if target_location in d.page_content]
             if filtered_fallback:
                 docs = filtered_fallback
                 print(f"DEBUG: ✅ Fallback 결과 중 '{target_location}' 관련 문서 {len(docs)}건 확보")
             else:
                 print("DEBUG: ⚠️ Fallback 결과가 있지만, 지역명 매칭되는 게 적음.")

    # 5. 결과 반환
    if not docs:
        return f"'{target_location}'에 대한 정보를 찾을 수 없습니다."

    unique_docs = []
    seen = set()
    for doc in docs:
        if doc.page_content not in seen:
            unique_docs.append(doc)
            seen.add(doc.page_content)
    
    context_str = format_docs(unique_docs)
    
    input_for_final_chain = {
        "context": f"검색 기준 지역: {final_region_filter}\n{context_str}", 
        "question": query
    }
    final_result = final_generation_chain.invoke(input_for_final_chain)
    
    return final_result



@tool
def get_weather_forecast(destination: str, dates: str) -> str:
    """
    특정 지역(destination)의 날씨를 조회합니다.
    지명(예: 광안리, 서면)을 입력하면 좌표로 변환하여 정확한 지역 날씨를 가져옵니다.
    """
    API_KEY = os.getenv("OWM_API_KEY")
    if not API_KEY: return "오류: OWM_API_KEY가 설정되지 않았습니다."

    print(f"\n--- [DEBUG] 날씨 조회 요청: {destination} ({dates}) ---")

    lat, lon = None, None

    # [Step 1] Google Maps를 이용해 지명 -> 좌표 변환 (가장 정확)
    # 우리가 만든 get_coordinates 함수 활용
    try:
        lat, lon = get_coordinates(destination)
        if lat and lon:
            print(f"DEBUG: 📍 '{destination}' 좌표 획득 성공 (Google): {lat}, {lon}")
    except Exception as e:
        print(f"DEBUG: Google 좌표 변환 실패: {e}")

    # [Step 2] Google 실패 시, OWM 자체 Geocoding 시도 (Fallback)
    if not lat or not lon:
        print(f"DEBUG: ⚠️ 좌표 획득 실패. OWM 텍스트 검색 시도: '{destination}'")
        try:
            geo_url = "https://api.openweathermap.org/geo/1.0/direct"
            # "광안리"가 실패할 수 있으므로 ",KR"을 붙여서 시도
            response = requests.get(geo_url, params={'q': f"{destination},KR", 'limit': 1, 'appid': API_KEY}, timeout=5)
            geo_data = response.json()
            
            if geo_data:
                lat, lon = geo_data[0]['lat'], geo_data[0]['lon']
                print(f"DEBUG: 📍 OWM Geocoding 성공: {lat}, {lon}")
            else:
                return f"오류: '{destination}'의 위치 정보를 날씨 API에서 찾을 수 없습니다."
        except Exception as e:
            return f"오류: Geocoding API 호출 실패: {e}"

    # [Step 3] 좌표 기반 날씨 예보 조회 (Forecast API)
    try:
        forecast_url = "https://api.openweathermap.org/data/2.5/forecast"
        # 좌표(lat, lon)를 직접 파라미터로 넣음
        response = requests.get(forecast_url, params={'lat': lat, 'lon': lon, 'appid': API_KEY, 'units': 'metric', 'lang': 'kr'}, timeout=10)
        
        if response.status_code != 200:
            return f"오류: 날씨 API 응답 실패 (Code: {response.status_code})"
            
        data = response.json()
        forecasts = data.get('list', [])
        city_name = data.get('city', {}).get('name', destination) # API가 인식한 도시 이름
    except Exception as e:
        return f"오류: Forecast API 호출 실패: {e}"

    # [Step 4] 날짜 필터링 및 결과 포맷팅
    today = datetime.datetime.now()
    target_date = today

    try:
        if "오늘" in dates: target_date = today
        elif "내일" in dates: target_date = today + datetime.timedelta(days=1)
        elif "모레" in dates: target_date = today + datetime.timedelta(days=2)
        else:
            # "12월 5일" 같은 형식 파싱
            match = re.search(r"(\d+)월\s*(\d+)일", dates)
            if match:
                month, day = map(int, match.groups())
                # 연도는 현재 연도 또는 내년 (12월에 1월 검색 시 등 고려 필요하나 여기선 단순화)
                year = today.year
                if month < today.month: year += 1 
                target_date = datetime.datetime(year, month, day)
    except: 
        target_date = today

    target_date_str = target_date.strftime("%Y-%m-%d")
    
    output_str = f"[{destination}(API명: {city_name}) / {target_date_str} 날씨 예보]\n"
    found = False
    
    for forecast in forecasts:
        # API는 3시간 간격 데이터 제공. 해당 날짜 데이터만 추출
        if forecast['dt_txt'].startswith(target_date_str):
            time_utc = forecast['dt_txt'].split(' ')[1][:5] # HH:MM
            temp = forecast['main']['temp'] 
            desc = forecast['weather'][0]['description']
            output_str += f"- {time_utc}: {temp:.1f}℃, {desc}\n"
            found = True
    
    if not found:
        return f"정보: {target_date_str} 날짜의 예보 데이터가 없습니다 (5일 이내 예보만 가능하거나 날짜 형식이 다름)."
    
    return output_str


# --- [수정] 상세 경로 조회 함수 (도보 필터링 제거) ---
def get_detailed_route(start_place: str, end_place: str, mode="transit"):
    """두 장소 간의 상세 경로(대중교통/도보)를 조회합니다."""
    if not GMAPS_CLIENT:
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
                transit = step.get('transit_details', {})
                line = transit.get('line', {})
                line_name = line.get('short_name') or line.get('name') or "버스"
                vehicle = line.get('vehicle', {}).get('name') or "대중교통"
                steps_summary.append(f"[{vehicle}] {line_name}")
            
            elif travel_mode == 'WALKING':
                # 🚨 [수정] 모든 도보 경로 표시 (짧아도 포함)
                walk_duration = step['duration']['text']
                steps_summary.append(f"🚶 도보 {walk_duration}")
            
            else:
                steps_summary.append(f"🚗 {travel_mode}")

        if not steps_summary:
            steps_summary.append(f"🚶 도보로 이동 ({duration})")

        return {
            "duration": duration,
            "distance": distance,
            "steps": steps_summary
        }

    except Exception as e:
        print(f"ERROR: 상세 경로 조회 오류: {e}")
        return None

# --- [수정] 경로 최적화 도구 (출발지 고정 로직 추가) ---
@tool
def optimize_and_get_routes(places: List[str], start_location: str = "") -> str:
    """
    출발지(start_location)에서 시작하여 여러 장소(places)를 방문하는 최적 동선을 계산합니다.
    출발지는 고정하고 나머지 장소의 순서를 최적화합니다.
    """
    if not GMAPS_CLIENT:
        return "오류: Google Maps API 키가 설정되지 않았습니다."
    
    # 장소 리스트 구성 (출발지 + 방문지)
    # 출발지가 있으면 리스트 맨 앞에 추가
    if start_location and start_location not in places:
        all_places = [start_location] + places
        start_fixed = True
    else:
        all_places = places
        start_fixed = False

    if len(all_places) < 2:
        return "오류: 최소 2개 이상의 장소(출발지 포함)가 필요합니다."

    print(f"\n--- [DEBUG] optimize_and_get_routes 호출 (출발지: {start_location}, 총 {len(all_places)}곳) ---")

    # 1. Distance Matrix API 호출
    now = datetime.datetime.now()
    try:
        matrix_result = GMAPS_CLIENT.distance_matrix(
            origins=all_places, destinations=all_places, mode="transit", departure_time=now
        )
        
        duration_matrix = []
        for row in matrix_result['rows']:
            row_vals = []
            for el in row['elements']:
                val = el.get('duration', {}).get('value', float('inf')) if el['status'] == 'OK' else float('inf')
                row_vals.append(val)
            duration_matrix.append(row_vals)

    except Exception as e:
        return f"오류: 거리 계산 실패 ({e})"

    # 2. 최적화 (TSP) - 출발지 고정 여부에 따른 로직 분기
    min_duration = float('inf')
    best_order_indices = []
    n = len(all_places)
    
    if start_fixed:
        # 0번(출발지)은 고정, 나머지(1~n-1)만 순열 생성
        other_indices = list(range(1, n))
        for p in permutations(other_indices):
            current_indices = [0] + list(p) 
            current_dur = sum(duration_matrix[current_indices[i]][current_indices[i+1]] for i in range(len(current_indices)-1))
            if current_dur < min_duration:
                min_duration = current_dur
                best_order_indices = current_indices
    else:
        # 출발지 없으면 기존 로직대로
        other_indices = list(range(1, n))
        for p in permutations(other_indices):
            current_indices = [0] + list(p) 
            current_dur = sum(duration_matrix[current_indices[i]][current_indices[i+1]] for i in range(len(current_indices)-1))
            if current_dur < min_duration:
                min_duration = current_dur
                best_order_indices = current_indices

    if min_duration == float('inf'):
        optimized_places = all_places
        print("DEBUG: 경로 최적화 실패 (이동 불가 구간 포함)")
    else:
        optimized_places = [all_places[i] for i in best_order_indices]
        print(f"DEBUG: 최적화된 순서: {optimized_places}")

    # 3. 상세 경로 생성
    final_output = [f"--- 🗺️ 1일차 최적 경로 (출발: {optimized_places[0]}) ---"]
    final_output.append(f"✅ 추천 순서: {' → '.join(optimized_places)}\n")
    
    total_time_str = f"예상 총 이동 시간: 약 {min_duration // 60}분" if min_duration != float('inf') else ""
    final_output.append(total_time_str + "\n")
    final_output.append("--- [상세 이동 경로] ---")

    for i in range(len(optimized_places) - 1):
        start = optimized_places[i]
        end = optimized_places[i+1]
        
        route_info = get_detailed_route(start, end, mode="transit")
        
        if route_info:
            steps_str = " -> ".join(route_info['steps'])
            final_output.append(f"Leg {i+1}: [{start}] ➡️ [{end}]")
            final_output.append(f"   ⏱️ 소요: {route_info['duration']} (거리: {route_info['distance']})")
            final_output.append(f"   🚌 경로: {steps_str}")
            final_output.append("") 
        else:
            final_output.append(f"Leg {i+1}: [{start}] ➡️ [{end}] (경로 정보 없음)")

    result_text = "\n".join(final_output)
    print("DEBUG: 상세 경로 생성 완료")
    return result_text

# 🚨 [추가] 타임 플래너 도구 정의
@tool
def plan_itinerary_timeline(itinerary: List[Dict]) -> str:
    """
    여행 일정 리스트를 입력받아, 구글 맵 기반의 이동 시간과 
    장소별 체류 시간을 계산하여 '타임라인(Timeline)'을 생성합니다.
    """
    print(f"\n--- [DEBUG] SmartScheduler 호출 ---")
    
    try:
        # 1. 스케줄러 인스턴스 생성 (시작 시간 10:00 설정)
        scheduler = SmartScheduler(start_time_str="10:00")
        
        # 2. 로직 실행 (이동 시간 계산 포함)
        # itinerary는 [{'day': 1, 'name': '...', ...}, ...] 형태여야 함
        
        # 날짜별로 그룹화하여 처리
        timeline_result = []
        
        # 날짜 목록 추출
        days = sorted(list(set(item.get('day', 1) for item in itinerary)))
        
        for day in days:
            # 해당 날짜의 장소들만 추출
            day_places = [item for item in itinerary if item.get('day', 1) == day]
            
            # 스케줄러 돌리기 (SmartScheduler.plan_day 사용)
            # plan_day는 리스트를 받아 타임라인 리스트를 반환
            day_timeline = scheduler.plan_day(day_places)
            
            # 결과에 'day' 정보 다시 주입 (SmartScheduler는 day를 모를 수 있음)
            for item in day_timeline:
                item['day'] = day
                timeline_result.append(item)

        # 3. JSON 문자열로 변환하여 반환
        return json.dumps(timeline_result, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"ERROR: 스케줄링 실패: {e}")
        return "오류: 스케줄 생성 중 문제가 발생했습니다."

# 도구 목록 등록
TOOLS = [search_attractions_and_reviews, get_weather_forecast, optimize_and_get_routes, plan_itinerary_timeline]
AVAILABLE_TOOLS = {tool.name: tool for tool in TOOLS}
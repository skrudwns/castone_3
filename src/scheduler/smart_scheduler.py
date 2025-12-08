# src/scheduler/smart_scheduler.py

import datetime
from typing import List, Dict
import re
import asyncio
from src.tools import get_detailed_route, GMAPS_CLIENT

# --- 설정: 장소 유형별 기본 체류 시간 (분 단위) ---
DEFAULT_DURATIONS = {
    "식당": 90, "카페": 60, "관광지": 120, "산책로": 60, "테마파크": 180, "숙소": 0
}

def extract_place_name_for_api(raw_name: str) -> str:
    if not raw_name or not isinstance(raw_name, str): return raw_name
    cleaned = re.sub(r'^(점심|저녁|아침|오전|오후|숙소|출발|도착)\s*:\s*', '', raw_name)
    cleaned = re.sub(r'\s+(에서|및)\s+.*', '', cleaned)
    return cleaned.strip()

class SmartScheduler:
    def __init__(self, start_time_str: str = "10:00", start_date=None):
        now = datetime.datetime.now()
        self.base_date = start_date if start_date else now
        
        try:
            h, m = map(int, start_time_str.split(":"))
            self.default_start_time = datetime.time(h, m)
        except ValueError:
            self.default_start_time = datetime.time(10, 0)

        # 1일차 시작 시간 설정
        self.current_time = datetime.datetime.combine(self.base_date.date(), self.default_start_time)

    def _estimate_duration(self, place_info: Dict) -> int:
        place_type = place_info.get('type', '관광지')
        place_name = place_info.get('name', '')
        for key, duration in DEFAULT_DURATIONS.items():
            if key in place_type: return duration
        if "카페" in place_name: return 60
        if "식당" in place_name: return 90
        return 90

    async def plan_day(self, places: List[Dict]) -> List[Dict]:
        """
        [수정됨] 날짜별 시간 리셋 로직 강화
        """
        if not places: return []
        
        timeline = []
        ordered_places = places 
        
        # 1. 현재 처리 중인 날짜 확인
        # (리스트의 첫 번째 아이템의 'day' 값을 기준으로 함)
        current_day_num = ordered_places[0].get('day', 1)
        
        # 2. [핵심] 시간 리셋 로직
        # 1일차가 아니면 무조건 해당 날짜의 오전 10시로 리셋
        target_date = self.base_date.date() + datetime.timedelta(days=current_day_num - 1)
        
        if current_day_num == 1:
            # 1일차는 초기 설정된 시간(self.current_time)을 그대로 사용 (이전 로직 유지)
            # 단, 날짜는 확실하게 맞춰줌
            self.current_time = datetime.datetime.combine(target_date, self.current_time.time())
        else:
            # 2일차부터는 무조건 10:00 AM 시작
            self.current_time = datetime.datetime.combine(target_date, datetime.time(10, 0))
            print(f"DEBUG: 📅 Day {current_day_num} 시작 -> 시간 리셋 완료: {self.current_time}")

        cursor_time = self.current_time 

        for i in range(len(ordered_places)):
            current_place = ordered_places[i]
            
            # --- A. 이동 (이전 장소 -> 현재 장소) ---
            if i > 0:
                prev_place = ordered_places[i-1]
                prev_api_name = extract_place_name_for_api(prev_place['name'])
                curr_api_name = extract_place_name_for_api(current_place['name'])

                # API 호출
                route_result = await get_detailed_route(
                    prev_api_name, curr_api_name, mode="transit", departure_time=cursor_time
                )
                
                # 기본값
                travel_seconds = 1800 
                travel_text = "약 30분"
                transport_mode = "transit"
                transport_detail = "이동"

                if route_result:
                    travel_seconds = route_result.get('duration_value', 1800)
                    travel_text = route_result.get('duration', '30분')
                    transport_mode = route_result.get('mode', 'transit')
                    
                    steps = route_result.get('steps', [])
                    if steps:
                        transport_detail = " ➡️ ".join(steps) # 상세 경로 연결

                # 시간 업데이트
                start_move_time = cursor_time
                cursor_time += datetime.timedelta(seconds=travel_seconds)

                # 날짜 변경 접미사 (필요시)
                # s_suffix = f" (+{(start_move_time.date() - self.base_date.date()).days}일)"
                
                travel_info = {
                    "type": "move",
                    "from": prev_place['name'],
                    "to": current_place['name'],
                    "start": start_move_time.strftime("%H:%M"),
                    "end": cursor_time.strftime("%H:%M"),
                    "duration_min": travel_seconds // 60,
                    "transport_mode": transport_mode,
                    "transport_detail": transport_detail, 
                    "duration_text_raw": travel_text
                }
                timeline.append(travel_info)

            # --- B. 활동 ---
            stay_minutes = self._estimate_duration(current_place)
            activity_start = cursor_time
            cursor_time += datetime.timedelta(minutes=stay_minutes)
            activity_end = cursor_time

            activity_info = {
                "type": "activity",
                "name": current_place['name'],
                "category": current_place.get('type', '장소'),
                "start": activity_start.strftime("%H:%M"),
                "end": activity_end.strftime("%H:%M"),
                "duration_minutes": stay_minutes,
                "description": current_place.get('description', '')
            }
            timeline.append(activity_info)

        return timeline
# src/smart_scheduler.py

import datetime
from typing import List, Dict
import re

# 기존 도구 활용
from src.tools import get_detailed_route, GMAPS_CLIENT

# --- 설정: 장소 유형별 기본 체류 시간 (분 단위) ---
DEFAULT_DURATIONS = {
    "식당": 90,      # 1시간 30분
    "카페": 60,      # 1시간
    "관광지": 120,   # 2시간
    "산책로": 60,
    "테마파크": 180, # 3시간
    "숙소": 0
}

class SmartScheduler:
    def __init__(self, start_time_str: str = "10:00"):
        """
        초기화: 여행 시작 시간을 설정합니다. (기본값: 오전 10시)
        """
        now = datetime.datetime.now()
        try:
            # HH:MM 형식 파싱
            h, m = map(int, start_time_str.split(":"))
            self.current_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            
            # 과거 시간 방지 (테스트용)
            if self.current_time < now:
                self.current_time += datetime.timedelta(days=1)
                
        except ValueError:
            self.current_time = now

    def _estimate_duration(self, place_info: Dict) -> int:
        """장소 유형이나 이름을 분석하여 체류 시간을 추정합니다."""
        place_type = place_info.get('type', '관광지')
        place_name = place_info.get('name', '')
        
        for key, duration in DEFAULT_DURATIONS.items():
            if key in place_type:
                return duration
        
        if "카페" in place_name or "커피" in place_name: return 60
        if "식당" in place_name or "맛집" in place_name: return 90
        
        return 90 # 기본값

    def plan_day(self, places: List[Dict]) -> List[Dict]:
        """
        [핵심 로직] 장소 목록을 받아서 타임라인을 생성합니다.
        (이동 시간 API 조회 + 체류 시간 계산)
        """
        timeline = []
        ordered_places = places 
        cursor_time = self.current_time 

        for i in range(len(ordered_places)):
            current_place = ordered_places[i]
            
            # --- A. 이동 (이전 장소 -> 현재 장소) ---
            if i > 0:
                prev_place = ordered_places[i-1]
                
                # 구글 맵 API로 실제 이동 시간 조회
                route_result = get_detailed_route(
                    prev_place['name'], 
                    current_place['name'], 
                    mode="transit", 
                    departure_time=cursor_time
                )
                
                if route_result:
                    # 실제 소요 시간(초)을 가져와서 계산
                    travel_seconds = route_result.get('duration_value', 1800) # 없으면 30분 가정
                    travel_text = route_result.get('duration', '30분')
                    
                    start_move_time = cursor_time
                    cursor_time += datetime.timedelta(seconds=travel_seconds)
                    
                    travel_info = {
                        "type": "move",
                        "from": prev_place['name'],
                        "to": current_place['name'],
                        "start": start_move_time.strftime("%H:%M"),
                        "end": cursor_time.strftime("%H:%M"),
                        "duration_text": travel_text,
                        "transport": route_result['steps'][0] if route_result['steps'] else "이동"
                    }
                    timeline.append(travel_info)
                else:
                    # 경로 못 찾음 (도보 10분 가정)
                    cursor_time += datetime.timedelta(minutes=10)

            # --- B. 활동 (현재 장소 체류) ---
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
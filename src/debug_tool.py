# src/debug_tools.py

import os
import sys
from dotenv import load_dotenv

# ------------------------------------------------------------------------------
# 1. 경로 설정 (src 모듈 인식을 위해)
# ------------------------------------------------------------------------------
current_dir = os.path.dirname(os.path.abspath(__file__)) # src 폴더
parent_dir = os.path.dirname(current_dir)                # 프로젝트 루트
sys.path.append(parent_dir)

# .env 파일 로드 (API 키 등)
load_dotenv()

try:
    # tools.py에서 우리가 만든 툴을 가져옵니다.
    from src.tools import search_attractions_and_reviews
    print("✅ src/tools.py 임포트 성공")
except ImportError as e:
    print(f"❌ 임포트 실패: {e}")
    sys.exit(1)

def run_test(query):
    print("\n" + "="*60)
    print(f"🧪 [툴 테스트] 쿼리: '{query}'")
    print("="*60)
    
    try:
        # 툴 실행 (내부에서 print 문들이 실행 과정을 보여줄 것입니다)
        result = search_attractions_and_reviews.invoke(query)
        
        print("\n" + "-"*60)
        print("📝 [최종 LLM 응답 결과]")
        print("-"*60)
        print(result)
        print("-"*60)
        
    except Exception as e:
        print(f"❌ 툴 실행 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # 테스트 케이스 1: 부산 (필터링 확인용)
    # 기대 결과: 로그에 부산 관련 문서만 찍혀야 함. 강원도 등 다른 지역이 나오면 안 됨.
    run_test("부산 맛집 추천해줘")
    
    # 테스트 케이스 2: 서울 (필터링 확인용)
    run_test("서울 경복궁 설명해줘")
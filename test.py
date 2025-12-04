# debug_parser.py (새로 만들기)
import re
import json

# 1. 테스트할 함수 (수정한 코드를 여기에 복붙해서 테스트)
def normalize_to_string(content) -> str:
    if content is None: return ""
    if isinstance(content, str): return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and 'text' in item: texts.append(item['text'])
            else: texts.append(str(item))
        return "\n".join(texts)
    return str(content)

def test_update_logic(message_content):
    print(f"\n--- 입력 타입: {type(message_content)} ---")
    try:
        # 정규화
        text = normalize_to_string(message_content)
        print(f"변환된 텍스트: {text[:50]}...") # 앞부분만 출력
        
        # 정규식 테스트
        match_plan = re.search(r"'(.*?)'을/를 (\d+)일차", text)
        if match_plan:
            print(f"✅ 파싱 성공: {match_plan.groups()}")
        else:
            print("⚠️ 파싱된 내용 없음 (에러는 안 남)")
            
    except Exception as e:
        print(f"❌ 에러 발생: {e}")

# 2. 테스트 케이스 (실제 발생했던 상황들)
# Case A: 일반 문자열
case_string = "네, '맛집'을 1일차 식당 계획에 추가합니다."
test_update_logic(case_string)

# Case B: 도구 호출 시 발생하는 None
case_none = None
test_update_logic(case_none)

# Case C: 날씨 정보 등에서 발생하는 리스트 (이번 에러의 주범!)
case_list = [{'type': 'text', 'text': '제주도 날씨는 맑음입니다.'}]
test_update_logic(case_list)
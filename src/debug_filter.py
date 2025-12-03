import os
import sys

# 🚀 실행 확인용
print("🚀 [Pre-filter] 디버그 스크립트 시작!")

# 1. 경로 설정
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

try:
    from langchain_community.vectorstores import FAISS
    from langchain_huggingface import HuggingFaceEmbeddings
    from src.region_cut_fuzz import parse_regions_from_query
    print("✅ 라이브러리 로드 성공")
except ImportError as e:
    print(f"❌ 임포트 에러: {e}")
    sys.exit(1)

REVIEW_FAISS_PATH = os.path.join(parent_dir, "review_faiss")
MODEL_NAME = "upskyy/bge-m3-korean"

def load_db():
    if not os.path.exists(REVIEW_FAISS_PATH):
        print("❌ DB 폴더 없음")
        return None
    embeddings = HuggingFaceEmbeddings(model_name=MODEL_NAME, model_kwargs={"device": "cpu"})
    return FAISS.load_local(REVIEW_FAISS_PATH, embeddings, allow_dangerous_deserialization=True)

def run_debug_search(db, query):
    print(f"\n" + "="*50)
    print(f"🧪 쿼리: '{query}'")
    print("="*50)

    # 1. 지역 파싱
    allowed_regions = parse_regions_from_query(query, fuzzy=True)
    print(f"1️⃣  파싱된 허용 지역: {allowed_regions}")

    # ------------------------------------------------------------------
    # [핵심] Pre-filtering (검색 '하면서' 필터링)
    # ------------------------------------------------------------------
    
    # LangChain FAISS에 넘겨줄 필터 함수 정의
    # metadata 딕셔너리를 입력받아 True/False를 반환해야 함
    def faiss_filter_func(metadata):
        # 1. 지역 제한이 없으면 무조건 통과
        if not allowed_regions:
            return True
            
        # 2. 메타데이터에서 지역 가져오기
        # (키가 'region'인지 '지역'인지 확인 필요, 여기선 둘 다 체크)
        meta_region = metadata.get("region") or metadata.get("지역") or ""
        
        # 3. 부분 일치 확인 (예: '부산' in '부산광역시...')
        for target in allowed_regions:
            if target in str(meta_region):
                return True # 통과!
        
        return False # 탈락!

    # 2. 검색 (filter 옵션 추가!)
    # 이제 FAISS가 이 함수가 True인 것만 골라서 k개를 채울 때까지 뒤집니다.
    print(f"\n2️⃣  FAISS 검색 (k=5, filter 적용됨)...")
    
    results = db.similarity_search(
        query, 
        k=5, 
        filter=faiss_filter_func # 👈 여기가 핵심입니다!
    )
    
    # 3. 결과 확인
    print("-" * 60)
    if results:
        for i, doc in enumerate(results):
            r_val = doc.metadata.get("region") or doc.metadata.get("지역")
            place = doc.metadata.get("place_name") or doc.metadata.get("장소명")
            print(f"   [{i+1}] {place} (지역: {r_val})")
    else:
        print("❌ 검색 결과가 없습니다.")
    print("-" * 60)

if __name__ == "__main__":
    db = load_db()
    if db:
        run_debug_search(db, "부산 맛집 추천해줘")
        run_debug_search(db, "서울 경복궁")
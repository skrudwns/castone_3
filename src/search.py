# src/search.py

from typing import List, Any, Optional
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document

class RegionPreFilteringRetriever(BaseRetriever):
    """
    1. 검색(Fetch): 벡터 검색으로 넉넉하게 가져옴 (k * 5)
    2. 필터(Filter): 메타데이터의 '지역' 정보를 파싱하여 유연하게 검증
    3. 반환(Return): 상위 K개 반환
    """
    vectorstore: Any 
    k: int = 5 
    fixed_location: Optional[str] = None 

    def _normalize_region_part(self, part: str) -> str:
        """비교를 위해 접미사 제거 (예: 경기도->경기, 해운대구->해운대)"""
        if not part: return ""
        # 긴 접미사 우선 제거
        suffixes = ["특별자치시", "특별자치도", "광역시", "특별시", "남도", "북도"]
        for s in suffixes:
            if part.endswith(s):
                return part.replace(s, "")
        
        # 짧은 접미사 (안전하게 제거)
        if len(part) > 2:
            if part.endswith("도") or part.endswith("시") or part.endswith("군") or part.endswith("구"):
                return part[:-1]
        return part

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        
        # 1. 사용자 쿼리(지역) 파싱
        targets = []
        if self.fixed_location:
            # 공백 기준으로 나눔 (예: "부산 해운대" -> ["부산", "해운대"])
            raw_parts = self.fixed_location.split()
            targets = [self._normalize_region_part(p) for p in raw_parts]

        def is_valid_region(metadata: dict) -> bool:
            if not targets:
                return True # 지역 조건 없으면 통과

            # 2. 메타데이터(문서 지역) 파싱
            region_str = str(metadata.get("지역") or metadata.get("region") or "").strip()
            if not region_str or region_str == "nan":
                return False 
            
            # 문서의 지역을 어절 단위로 쪼개고 정규화
            # 예: "경기도 가평군" -> ["경기", "가평"]
            doc_parts = [self._normalize_region_part(p) for p in region_str.split()]
            
            # 3. [핵심 수정] 교차 검증 로직
            # 타겟 키워드가 문서의 지역 정보(광역 or 기초) 중 하나라도 일치해야 함
            
            # A. 1단어 검색일 때 (예: "가평", "부산")
            if len(targets) == 1:
                target_keyword = targets[0]
                # 문서의 지역 파트 중 하나라도 타겟을 포함하거나, 타겟이 문서 파트를 포함하면 통과
                # (예: target="가평" in doc_parts=["경기", "가평"] -> True)
                match_found = False
                for dp in doc_parts:
                    if target_keyword in dp or dp in target_keyword:
                        match_found = True
                        break
                if not match_found:
                    return False

            # B. 2단어 이상 검색일 때 (예: "부산 해운대")
            # 이건 더 엄격해야 함 (AND 조건) -> 모든 타겟 키워드가 문서 지역 정보 어딘가에 있어야 함
            else:
                doc_full_str = " ".join(doc_parts) # "경기 가평"
                for t in targets:
                    if t not in doc_full_str:
                        return False

            return True

        # [검색 실행]
        try:
            # 필터링으로 탈락할 것을 대비해 10배수 검색
            raw_docs = self.vectorstore.similarity_search(query, k=self.k * 10)
        except Exception as e:
            print(f"DEBUG: 벡터 검색 실패: {e}")
            return []

        filtered_docs = []
        for doc in raw_docs:
            if is_valid_region(doc.metadata):
                filtered_docs.append(doc)
            
            if len(filtered_docs) >= self.k:
                break
        
        return filtered_docs
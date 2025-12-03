# src/search.py

from typing import List, Any, Optional
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document

class RegionPreFilteringRetriever(BaseRetriever):
    """
    고정된 목적지(fixed_location) 기준으로 1차 필터링 후,
    쿼리 키워드로 2차 필터링을 수행하는 하이브리드 리트리버.
    (한글 키 '지역', '장소명' 지원 버전)
    """
    vectorstore: Any 
    k: int = 3
    fixed_location: Optional[str] = None # 예: "서울특별시" (정규화된 명칭)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        
        query_tokens = query.split()

        # [필터링 키워드 설정]
        target_keywords = []
        if self.fixed_location:
            # 1. 공식 명칭 (예: 서울특별시)
            target_keywords.append(self.fixed_location) 
            
            # 2. 약칭 처리 (예: 서울특별시 -> 서울)
            if "특별" in self.fixed_location or "광역" in self.fixed_location:
                short_name = self.fixed_location.replace("특별시", "").replace("광역시", "").replace("특별자치시", "").replace("특별자치도", "")
                target_keywords.append(short_name)

        def filter_func(metadata: dict) -> bool:
            # [핵심 수정] 한글 키 '지역'을 우선적으로 확인
            meta_region = str(metadata.get("지역") or metadata.get("region") or "")
            
            # 1. 목적지(광역시/도) 강제 필터링
            if self.fixed_location:
                is_region_match = False
                for kw in target_keywords:
                    if kw in meta_region:
                        is_region_match = True
                        break
                
                if not is_region_match:
                    return False # 지역 불일치 시 탈락

            # 2. 쿼리 키워드 매칭 (하위 지역 필터링)
            token_match = False
            has_sub_region_in_query = False
            
            for token in query_tokens:
                if len(token) >= 2 and token in meta_region:
                    token_match = True
                    has_sub_region_in_query = True
                    break
            
            if has_sub_region_in_query:
                return token_match
            else:
                return True 

        # 필터 적용 검색 실행
        docs = self.vectorstore.similarity_search(
            query, 
            k=self.k, 
            filter=filter_func 
        )
        
        return docs
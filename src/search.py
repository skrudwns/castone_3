from typing import List, Any
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from src.region_cut_fuzz import parse_regions_from_query

class RegionPreFilteringRetriever(BaseRetriever):
    """
    사용자 쿼리에서 지역을 파싱하여, FAISS 검색 시 'Pre-filtering'을 수행하는 Retriever.
    기존 Retriever와 입출력 형식이 동일하여 바로 교체 가능합니다.
    """
    vectorstore: Any # FAISS 인스턴스
    k: int = 3       # 기본 검색 개수 (필터링이 강력하므로 적어도 됨)
    fuzzy_threshold: int = 85

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        
        # 1. 쿼리에서 지역 파싱 (예: "부산 맛집" -> {'부산'})
        allowed_regions = parse_regions_from_query(
            query, 
            fuzzy=True, 
            fuzzy_threshold=self.fuzzy_threshold
        )

        # 2. FAISS용 필터 함수 정의 (Pre-filtering용)
        # 이 함수가 True를 반환하는 문서만 검색 후보에 오릅니다.
        def filter_func(metadata: dict) -> bool:
            # 지역 제한이 없으면 모든 문서 통과
            if not allowed_regions:
                return True
            
            # 메타데이터에서 지역 정보 가져오기 (키 이름 호환성 체크)
            meta_region = str(metadata.get("region") or metadata.get("지역") or "")
            
            # 부분 일치 확인 (예: '부산' in '부산광역시 중구')
            for target in allowed_regions:
                if target in meta_region:
                    return True
            return False

        # 3. 필터가 적용된 상태로 유사도 검색 수행
        # LangChain FAISS는 filter 인자에 함수(callable)를 넣을 수 있습니다.
        docs = self.vectorstore.similarity_search(
            query, 
            k=self.k, 
            filter=filter_func 
        )
        
        return docs
# src/region_cut_fuzz.py

import re
from typing import List, Set, Dict
from langchain_core.documents import Document

# 표준 지역명 리스트
CANON: List[str] = [
    "서울","부산","대구","인천","광주","대전","울산","세종",
    "경기","강원","충북","충남","전북","전남","경북","경남","제주"
]

# 흔한 별칭/영문/오타 매핑
ALIASES: Dict[str, Set[str]] = {
    "서울": {"서울시","서울특별시","seoul","서울특"},
    "부산": {"부산시","부산광역시","busan"},
    "대구": {"대구시","대구광역시","daegu"},
    "인천": {"인천시","인천광역시","incheon"},
    "광주": {"광주시","광주광역시","gwangju"},
    "대전": {"대전시","대전광역시","daejeon"},
    "울산": {"울산시","울산광역시","ulsan"},
    "세종": {"세종시","세종특별자치시","sejong"},
    "경기": {"경기도","gyeonggi"},
    "강원": {"강원도","gangwon","강원특별자치도"},
    "충북": {"충청북도","chungbuk"},
    "충남": {"충청남도","chungnam"},
    "전북": {"전라북도","jeonbuk","전북특별자치도"},
    "전남": {"전라남도","jeonnam"},
    "경북": {"경상북도","gyeongbuk"},
    "경남": {"경상남도","gyeongnam"},
    "제주": {"제주도","jeju","제주특별자치도"},
}

# 권역/집합 토큰 확장
MACROS: Dict[str, Set[str]] = {
    "수도권": {"서울","경기","인천"},
    "부울경": {"부산","울산","경남"},
    "영남": {"부산","대구","울산","경북","경남"},
    "호남": {"광주","전북","전남"},
    "충청권": {"대전","세종","충북","충남"},
    "강원권": {"강원"},
    "제주권": {"제주"},
    "서울근교": {"서울","경기","인천"},
    "수도": {"서울"},
}

SEP = re.compile(r"[,\|/·\-]")

def _tokenize_query(q: str) -> List[str]:
    q = SEP.sub(" ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q.split()

# 🚨 [추가된 함수] 지역명 정규화 함수
def normalize_region_name(query: str) -> str:
    """
    입력된 지역명(예: '부산광역시', 'Busan')을 표준 명칭(예: '부산')으로 변환합니다.
    매칭되는 것이 없으면 원본을 반환합니다.
    """
    q = query.strip()
    
    # 1. CANON 리스트에 있는지 확인 (이미 표준임)
    if q in CANON:
        return q
        
    # 2. ALIASES 딕셔너리 확인 (별칭 -> 표준)
    for canon, alias_set in ALIASES.items():
        if q in alias_set:
            return canon
            
    # 3. 접미사 제거 후 확인 (단순 매칭)
    simple_name = q.replace("특별시","").replace("광역시","").replace("특별자치시","").replace("특별자치도","").replace("도","").replace("시","")
    if simple_name in CANON:
        return simple_name
        
    return q

def parse_regions_from_query(query: str, fuzzy: bool = True, fuzzy_threshold: int = 85) -> Set[str]:
    """쿼리에서 광역시/도 집합을 뽑는다. (정확일치 → 별칭/권역 → 퍼지매칭 순)"""
    q = query.lower()
    tokens = _tokenize_query(q)

    found: Set[str] = set()

    # 1) 정확 일치
    for c in CANON:
        if c in query:
            found.add(c)

    # 2) 권역/매크로
    for macro, expands in MACROS.items():
        if macro in query:
            found |= expands

    # 3) 별칭 일치
    for canon, alset in ALIASES.items():
        if any(a.lower() in q for a in alset):
            found.add(canon)

    # 4) 퍼지 매칭
    if fuzzy:
        try:
            from rapidfuzz import process, fuzz
            for t in tokens:
                if len(t) < 2: 
                    continue
                cand, score, _ = process.extractOne(
                    t, CANON, scorer=fuzz.WRatio
                )
                if score >= fuzzy_threshold:
                    found.add(cand)
        except Exception:
            pass

    return found

def filter_docs_by_region(docs: List[Document], allowed: Set[str], field: str = "지역",
                          drop_unknown: bool = True) -> List[Document]:
    """리트리버 결과를 광역시/도로 컷."""
    if not allowed:
        return docs
    out = []
    for d in docs:
        reg = str((d.metadata or {}).get(field, ""))
        
        is_match = False
        for target in allowed:
            if target in reg:
                is_match = True
                break
        
        if is_match:
            out.append(d)
        elif not reg or reg == "nan":
            if not drop_unknown:
                out.append(d)
    return out
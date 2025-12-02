# src/rag_updater.py

import pandas as pd
import re
import emoji
import streamlit as st
import os
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings # 👈 [수정] 최신 권장 사항
from langchain_community.vectorstores import FAISS
from src.config import review_faiss # config.py에서 경로만 가져옴

# --- 1. data_process.ipynb에서 가져온 전처리 함수 ---

def clean_review(text):
    """리뷰 텍스트를 정제합니다."""
    text = str(text) # NaN 방지
    text = re.sub(r'\s+', ' ', text)
    text = emoji.replace_emoji(text, replace='')
    text = re.sub(r'[^가-힣a-zA-Z0-9\s]', '', text)
    text = text.strip()
    return text

def chunk_text_with_overlap(text, chunk_size=500, overlap=50):
    """텍스트를 청킹합니다."""
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
        if start < 0:
            start = 0
        if start >= len(text):
            break
    return chunks

# --- 2. embedding.ipynb에서 가져온 문서화 함수 ---

def create_documents_from_df(df):
    """DataFrame을 LangChain Document 리스트로 변환합니다."""
    docs = []
    for _, row in df.iterrows():
        cleaned_review = clean_review(row["리뷰"])
        
        chunks = chunk_text_with_overlap(cleaned_review, chunk_size=500, overlap=20)
        
        for chunk in chunks:
            if len(chunk) <= 5: # 5자 이하 청크는 무시
                continue

            # embedding.ipynb의 'combined_text' 로직 적용
            combined_text = (
                f"{row['장소']}은(는) "
                f"{row['지역']}에 위치한 "
                f"{row['카테고리_통합']}입니다. "
                f"리뷰 내용은 다음과 같습니다: {chunk}"
            )
            
            # embedding.ipynb의 Document 생성 로직 적용
            doc = Document(
                page_content=combined_text,
                metadata={
                    "place_name": str(row["장소"]),
                    "region": str(row["지역"]),
                    "category": str(row.get("카테고리_통합", "")),
                    "rating": str(row.get("평점", ""))
                }
            )
            docs.append(doc)
    return docs

# --- 3. 벡터 DB 업데이트 함수 (핵심) ---

def update_vector_db_if_needed(new_reviews_file="new_reviews.csv"):
    """
    new_reviews.csv 파일에 10개 이상 리뷰가 쌓이면
    벡터 DB(review-faiss)에 추가(업데이트)합니다.
    """
    try:
        df = pd.read_csv(new_reviews_file)
    except FileNotFoundError:
        return "누적된 리뷰 파일이 없습니다."
    except pd.errors.EmptyDataError:
        return "누적된 리뷰가 없습니다."

    if len(df) < 10:
        return f"리뷰 {len(df)}개 누적됨. (10개 이상이어야 업데이트)"

    st.toast(f"리뷰 {len(df)}개가 누적되어 벡터 DB 업데이트를 시작합니다...")
    print(f"--- [RAG Updater] 리뷰 {len(df)}개 DB 업데이트 시작 ---")

    try:
        # 1. 신규 리뷰를 Document로 변환
        new_docs = create_documents_from_df(df)
        if not new_docs:
            print("[RAG Updater] 처리할 유효한 문서가 없습니다.")
            os.remove(new_reviews_file) # 유효하지 않은 리뷰 파일 삭제
            return "업데이트할 유효한 리뷰가 없습니다."
            
        print(f"[RAG Updater] {len(new_docs)}개의 새 문서를 생성했습니다.")

        # 2. 임베딩 모델 로드 (embedding.ipynb 참고)
        embeddings = HuggingFaceEmbeddings(
            model_name="upskyy/bge-m3-korean",
            model_kwargs={"device": "cpu"}
        )
        
        # 3. 기존 FAISS DB 로드
        db = FAISS.load_local(
            review_faiss, embeddings, allow_dangerous_deserialization=True
        )
        print("[RAG Updater] 기존 FAISS 인덱스를 로드했습니다.")

        # 4. DB에 신규 문서 추가
        db.add_documents(new_docs)
        print("[RAG Updater] FAISS 인덱스에 새 문서를 추가했습니다.")

        # 5. DB 저장 (덮어쓰기)
        db.save_local(review_faiss)
        print("[RAG Updater] FAISS 인덱스를 로컬에 저장했습니다.")

        # 6. Streamlit 캐시 삭제 (중요!)
        # 1_trip_planner.py가 새 DB를 로드하도록 강제
        st.cache_resource.clear()
        print("[RAG Updater] Streamlit 캐시를 삭제했습니다.")

        # 7. 누적된 리뷰 파일 삭제
        os.remove(new_reviews_file)
        print(f"[RAG Updater] {new_reviews_file} 파일을 삭제했습니다.")
        
        st.toast("벡터 DB 업데이트 완료!", icon="🎉")
        return "벡터 DB 업데이트 완료!"

    except Exception as e:
        print(f"!!!!!!!!!! [RAG Updater] 예외 발생 !!!!!!!!!!")
        print(f"DEBUG: Error details: {e}")
        st.error(f"DB 업데이트 중 오류 발생: {e}")
        return f"오류: {e}"
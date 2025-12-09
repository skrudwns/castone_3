AI 여행 플래너 (AI Travel Planner)
1. 프로젝트 개요
이 프로젝트는 Streamlit 기반의 웹 애플리케이션으로, LangGraph를 활용한 멀티 에이전트 시스템입니다.

주요 에이전트는 'PlannerAgent(엄격한 스케줄러)'와 'EditorAgent(여행 일정 편집자)'로 구성되어 있으며, RAG(검색 증강 생성) 기술과 외부 API(Google Maps)를 결합하여 사용자에게 최적화된 개인화 여행 일정을 생성해 줍니다.

2. 핵심 대화 흐름 (Core Workflow)
대화는 크게 '계획(Planning)' 단계와 '편집(Editing)' 단계로 구분되어 진행됩니다.

초기 계획 (Planning)

PlannerAgent가 사용자의 입력(목적지, 기간, 인원 등)을 바탕으로 여행 계획을 수립합니다.

이때 시스템에 사전에 정의된 기계적인 '고정 스케줄' 규칙을 따릅니다.

일정 생성 (Itinerary Generation)

각 일차별로 정의된 슬롯(식당, 카페, 관광지)을 채우기 위해 find_and_select_best_place 도구를 호출하여 최적의 장소를 검색합니다.

타임라인 산정 (Timeline Calculation)

장소가 확정되면 plan_itinerary_timeline 도구를 통해 이동 시간과 체류 시간을 계산하여 구체적인 시간표를 생성합니다.

수정 및 편집 (Editing)

사용자가 일정을 수정하고 싶어 하면 EditorAgent가 개입합니다.

특정 장소를 삭제하거나 교체(delete_place -> find_and_select_best_place)하고, 변경된 동선에 맞춰 시간을 재계산합니다.

확정 및 PDF (Confirmation)

일정이 확정되면 PDF 다운로드 버튼을 활성화하여 대화를 종료합니다.

Vector DB 업데이트 (Self-Learning)

사용자에게 장소명, 리뷰, 지역명, 평점 등 정해진 메타데이터 양식에 맞춰 입력을 받습니다.

데이터가 10개 이상 모일 시, 자동으로 기존 데이터베이스에 업데이트됩니다.

3. 주요 기능 상세 (Key Features)
스케줄링 로직 (Planner Agent)
엄격한 슬롯 할당 에이전트는 자유롭게 일정을 짜는 것이 아니라, 사전에 정의된 '슬롯'을 채우는 방식으로 작동합니다.

Day 1: 점심(식당) - 카페 - 관광지 - 저녁(식당) (총 4곳)

중간 날 (Day 2 ~ N-1): 관광지(10시 시작) - 점심 - 카페 - 관광지 - 저녁 (총 5곳)

마지막 날: 관광지 딱 1곳 방문 후 일정 종료

경로 최적화

전체 장소를 대상으로, 맨 처음 입력받은 목적지 근처의 장소를 시작점으로 선정합니다.

이후 방금 설정한 장소를 기준으로 선호에 맞는 가장 가까운 위치를 차례로 선정하는 과정을 반복하여 동선을 최적화합니다.

검색 및 추천 로직 (Search Tools)
2단계 검색 전략 find_and_select_best_place 도구는 정확도를 위해 2단계 검색 전략을 수행합니다.

선호 반영 검색: 사용자 취향(user_info)을 쿼리에 포함하여 검색합니다.

Fallback (거리 우선) 검색: 1단계 결과가 없을 경우, 취향 정보를 제거하고 검색한 뒤 기준점(Anchor)에서 가장 가까운 거리순으로 정렬하여 추천합니다.

행정구역 정규화

사용자가 "해운대 맛집"처럼 검색하면, LLM과 Google Maps API를 통해 이를 "부산광역시 해운대구"라는 정확한 행정구역명으로 변환하여 검색 정확도를 높입니다.

RAG 데이터 업데이트 로직 (Review Writer)
사용자가 작성한 리뷰는 서버에 임시 파일(new_reviews.csv)로 누적됩니다.

자동 업데이트 트리거: 누적 리뷰가 10개 이상이 되면 업데이트 프로세스가 자동으로 실행됩니다.

지능형 주소 채우기: 새 리뷰에 주소 정보가 누락된 경우, 기존 벡터 DB(FAISS)에서 동일한 장소명을 검색하여 기존에 저장된 상세 주소를 자동으로 가져와 채워넣습니다.

임베딩 모델: 한국어 처리에 특화된 upskyy/bge-m3-korean 모델을 사용하여 데이터를 벡터화합니다.

PDF 생성 로직
사용자가 "PDF 만들어줘"라고 요청하거나 일정을 확정하면 confirm_and_download_pdf 도구가 호출됩니다. 이 도구가 실행되면 상태 변수(show_pdf_button)가 True로 변경되며, UI에 PDF 다운로드 버튼이 렌더링되고 에이전트의 작업이 종료됩니다.

비동기 처리 및 성능 최적화 (Asynchronous Processing)
병렬 도구 실행 (Parallel Execution)

src/graph_flow.py의 call_tools_node에서 asyncio.gather를 사용합니다.

에이전트가 여러 장소를 동시에 검색하거나 정보를 요청할 때, 이를 순차적으로 처리하지 않고 병렬로 동시에 실행하여 전체 응답 속도를 획기적으로 단축시킵니다.

Non-blocking I/O

src/tools.py에서 Google Maps API 호출이나 FAISS 벡터 DB 검색과 같이 시간이 오래 걸리는 작업은 asyncio.to_thread를 통해 별도의 스레드에서 처리합니다.

이를 통해 무거운 작업이 실행되는 동안에도 메인 애플리케이션이 멈추지 않고 반응성을 유지하도록 설계되었습니다.
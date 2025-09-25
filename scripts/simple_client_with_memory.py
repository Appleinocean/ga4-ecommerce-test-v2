import json
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

# --- 1. 기본 정보 설정 ---
GCP_PROJECT_ID = "ga4-llm-test"
BIGQUERY_DATASET_ID = "analytics_445198482"
DATASET_REF = f"{GCP_PROJECT_ID}.{BIGQUERY_DATASET_ID}"
# ✨ 새로 만들 저장 프로시저 이름
PROCEDURE_NAME = f"{DATASET_REF}.ga4_natural_language_query_with_memory"

# --- 2. BigQuery 호출 함수 수정 ---
def ask_question_to_bigquery(client, procedure_name, question, chat_history):
    """
    대화 기록을 포함하여 BigQuery 저장 프로시저를 호출합니다.
    """
    # Python 리스트를 JSON 문자열로 변환하여 프로시저에 전달
    history_json_string = json.dumps(chat_history, ensure_ascii=False)

    # ✨ 프로시저 호출 SQL에 @chat_history_json 파라미터 추가
    query = f"""
        DECLARE final_answer STRING;
        CALL `{procedure_name}`(
            @user_question,
            @chat_history_json,  -- 대화 기록 전달
            final_answer
        );
        SELECT final_answer;
    """
    
    # SQL 인젝션 방지를 위해 쿼리 파라미터 사용
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_question", "STRING", question),
            bigquery.ScalarQueryParameter("chat_history_json", "STRING", history_json_string),
        ]
    )

    try:
        job = client.query(query, job_config=job_config)
        results = job.result()
        for row in results:
            return row[0]
    except GoogleAPICallError as e:
        return f"BigQuery 호출 중 오류가 발생했습니다: {e}"

# --- 3. 메인 실행 루프 수정 ---
if __name__ == "__main__":
    bq_client = bigquery.Client(project=GCP_PROJECT_ID)
    
    # ✨ 대화 기록을 저장할 리스트 초기화
    chat_history = []

    print("✅ 대화형 AI 에이전트가 준비되었습니다. (대화 기록 기능 활성화)")
    
    while True:
        question = input("\n🧑 질문을 입력하세요 (종료하려면 '종료' 입력): ")
        if question.lower() in ['종료', 'exit', 'quit']:
            print("👋 프로그램을 종료합니다.")
            break
        
        print("🤖 BigQuery AI에게 질문하는 중 (대화 기록 포함)...")
        answer = ask_question_to_bigquery(bq_client, PROCEDURE_NAME, question, chat_history)
        
        print("\n✅ 최종 답변:")
        print(answer)

        # ✨ 현재 질문과 답변을 대화 기록에 추가
        chat_history.append({"role": "user", "content": question})
        chat_history.append({"role": "model", "content": answer})
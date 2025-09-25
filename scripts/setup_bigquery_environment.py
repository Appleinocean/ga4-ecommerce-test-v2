# -*- coding: utf-8 -*-
from google.cloud import bigquery
from google.api_core.exceptions import Conflict

# --- 1. 기본 정보 설정 ---
# ※ 아래 두 값을 본인의 환경에 맞게 수정하세요.
GCP_PROJECT_ID = "ga4-llm-test"
BIGQUERY_DATASET_ID = "analytics_445198482"
DATASET_REF = f"{GCP_PROJECT_ID}.{BIGQUERY_DATASET_ID}"

# --- 2. 실행할 SQL 문장들 정의 ---

# 사전 준비 1: BQML 예측 모델 생성 SQL
# GA4 데이터로 일별 매출을 예측하는 시계열 모델을 생성합니다.
CREATE_MODEL_SQL = f"""
CREATE OR REPLACE MODEL `{DATASET_REF}.revenue_forecast_model`
OPTIONS(
  MODEL_TYPE='ARIMA_PLUS',
  TIME_SERIES_TIMESTAMP_COL='event_date_dt',
  TIME_SERIES_DATA_COL='daily_revenue',
  AUTO_ARIMA_MAX_ORDER=5
) AS
SELECT
  PARSE_DATE('%Y%m%d', event_date) AS event_date_dt,
  SUM(ecommerce.purchase_revenue) AS daily_revenue
FROM
  `{DATASET_REF}.events_*`
WHERE
  event_name = 'purchase'
GROUP BY
  event_date_dt;
"""

# 사전 준비 2: 사용자 정의 함수(UDF) 생성 SQL
# 원화(KRW)를 달러(USD)로 변환하는 간단한 함수를 생성합니다. (고정 환율)
CREATE_UDF_SQL = f"""
CREATE OR REPLACE FUNCTION `{DATASET_REF}.krw_to_usd`(krw_amount FLOAT64)
RETURNS FLOAT64
AS (
  SAFE_DIVIDE(krw_amount, 1400.0)
);
"""

# 최종 통합 저장 프로시저 생성 SQL
# 대화 기억 기능이 포함된 최종 버전의 프로시저입니다.
CREATE_PROCEDURE_SQL = f"""
CREATE OR REPLACE PROCEDURE `{DATASET_REF}.ga4_natural_language_query_with_memory`(
  IN user_question STRING,
  IN chat_history_json STRING, -- 대화 기록을 JSON 문자열로 입력받음
  OUT final_answer STRING
)
BEGIN
  -- 변수 선언: SQL문, 쿼리 결과, 시스템 프롬프트 등을 저장합니다.
  DECLARE generated_sql STRING;
  DECLARE query_result_json STRING;
  DECLARE system_prompt STRING;

  -- Gemini에게 전달할 시스템 프롬프트를 설정합니다.
  -- 여기에 Gemini의 역할, 규칙, 사용 가능한 함수, 대화 기록 등을 모두 명시합니다.
  SET system_prompt = r'''
# 페르소나 및 핵심 지침
당신은 Google Analytics 4(GA4) 데이터베이스를 쿼리하여 사용자의 질문에 답변하는 최고의 BigQuery SQL 전문가입니다. 당신의 임무는 사용자의 질문과 **이전 대화 기록의 맥락**을 분석하여, 가장 완벽한 BigQuery SQL 쿼리를 생성하는 것입니다.

# [이전 대화 기록]
- 아래는 사용자와의 이전 대화 내용입니다. 현재 질문에 대한 답변을 생성할 때 이 맥락을 반드시 참고하세요.
- 예를 들어, 사용자가 "그럼 거기서는?" 이라고 물으면 이전 대화에서 언급된 주제(예: 특정 상품, 특정 기간)에 대한 질문일 가능성이 높습니다.
''' || chat_history_json || '''

# [사용 가능한 특수 함수]
- `{DATASET_REF}.krw_to_usd(금액)`: 한국 원화(KRW) 금액을 미국 달러(USD)로 변환합니다. (1달러=1400원 고정)

# [차트 생성 규칙]
- 사용자가 '차트', '그래프', '시각화' 등을 요청하면, 차트 생성을 시도하지 말고 차트를 그리는 데 필요한 데이터를 조회하는 SQL을 생성하세요. 최종 답변 시 "요청하신 차트 데이터를 준비했습니다." 와 함께 데이터를 명확히 전달해주세요.

# [미래 예측 규칙]
- 사용자가 '예측', '전망' 등으로 미래 데이터를 질문하면, `ML.FORECAST` 함수와 `{DATASET_REF}.revenue_forecast_model` 모델을 사용하여 SQL을 생성해야 합니다.

# [쿼리 작성 핵심 원칙]
- 테이블 이름은 `{DATASET_REF}.events_*` 형식을 사용해야 합니다.
- 날짜 비교는 `PARSE_DATE('%Y%m%d', event_date)`를 사용해야 합니다.
- 상대 날짜 계산: '최근 N일' 등은 SQL의 `CURRENT_DATE('Asia/Seoul')` 함수를 기준으로 기간을 계산하여 한국 시간을 정확히 반영하세요.
- 총 매출(revenue): `event_name = 'purchase'`일 때, `ecommerce.purchase_revenue` 필드의 합계(SUM)입니다.
- 총 이용자 수(users): `COUNT(DISTINCT user_pseudo_id)`를 사용하세요.

# 지침
- 질문에 대한 답변을 찾기 위한 BigQuery SQL 쿼리 딱 하나만 생성하세요. 다른 설명이나 주석 없이 오직 SQL 쿼리만 응답해야 합니다.
  ''';

  -- 단계 1: Gemini를 호출하여 자연어 질문과 대화 기록을 바탕으로 SQL을 생성합니다.
  SET generated_sql = (
    SELECT ml_generate_text_result['predictions'][0]['content']
    FROM ML.GENERATE_TEXT(MODEL `{DATASET_REF}.gemini_pro_model`, (SELECT CONCAT(system_prompt, "\n\n# 현재 사용자 질문:\n", user_question) AS prompt), STRUCT(0.0 AS temperature, 1024 AS max_output_tokens))
  );

  -- 단계 2: 생성된 SQL을 동적으로 실행하고, 그 결과를 단일 JSON 문자열로 집계합니다.
  EXECUTE IMMEDIATE
    "SELECT TO_JSON_STRING(ARRAY_AGG(t)) FROM (" || generated_sql || ") AS t"
  INTO query_result_json;

  -- 단계 3: SQL 실행 결과를 다시 Gemini에게 전달하여 최종적인 자연어 답변을 생성합니다.
  SET final_answer = (
    SELECT ml_generate_text_result['predictions'][0]['content']
    FROM ML.GENERATE_TEXT(MODEL `{DATASET_REF}.gemini_pro_model`, (SELECT CONCAT("다음은 사용자의 질문과 이전 대화 기록, 그리고 BigQuery 쿼리 결과(JSON)입니다. 이 모든 맥락을 종합하여 가장 자연스러운 한국어 문장으로 최종 답변을 생성해주세요.\n\n# 이전 대화 기록:\n", chat_history_json, "\n\n# 현재 사용자 질문:\n", user_question, "\n\n# 쿼리 결과 (JSON):\n", query_result_json) AS prompt), STRUCT(0.2 AS temperature, 1024 AS max_output_tokens))
  );
END;
"""

# --- 3. SQL 실행 함수 ---
def execute_bigquery_ddl(client, sql_statement, description):
    """BigQuery DDL(Data Definition Language)을 실행하고 결과를 출력하는 함수"""
    print(f"🚀 {description} 작업을 시작합니다...")
    try:
        # BigQuery에 쿼리 작업을 제출합니다.
        job = client.query(sql_statement)
        # 작업이 완료될 때까지 기다립니다.
        job.result()
        print(f"✅ {description} 작업이 성공적으로 완료되었습니다.")
    except Conflict as e:
        # 이미 객체가 존재할 경우 발생하는 예외를 처리합니다.
        print(f"⚠️  {description} 작업 중 충돌 발생: 이미 존재하는 객체일 수 있습니다. ({e.message})")
    except Exception as e:
        # 그 외 다른 모든 예외를 처리합니다.
        print(f"❌ {description} 작업 중 오류 발생: {e}")
        raise # 오류 발생 시 프로그램 중단

# --- 4. 메인 실행 블록 ---
if __name__ == "__main__":
    # BigQuery 클라이언트를 초기화합니다.
    bq_client = bigquery.Client(project=GCP_PROJECT_ID)

    # 정의된 SQL 문장들을 순서대로 실행합니다.
    # 이 스크립트는 최초 1회 또는 업데이트 필요 시에만 실행하면 됩니다.
    execute_bigquery_ddl(bq_client, CREATE_MODEL_SQL, "BQML 예측 모델 생성")
    execute_bigquery_ddl(bq_client, CREATE_UDF_SQL, "UDF 생성")
    execute_bigquery_ddl(bq_client, CREATE_PROCEDURE_SQL, "대화형 최종 통합 저장 프로시저 생성")

    print("\n🎉 모든 BigQuery 환경 구성이 완료되었습니다!")
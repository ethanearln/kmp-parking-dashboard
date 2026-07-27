import os
import pandas as pd
from google.cloud import bigquery

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
    os.path.dirname(__file__), "credentials", "kmp-platform-database-85d721abe80f.json"
)
PROJECT = "kmp-platform-database"
client = bigquery.Client()

df = client.query(f"SELECT * FROM `{PROJECT}.db_platform_kmp.tb_integrated_product_data_wide`").to_dataframe()


def regular_stock(row_df):
    exclude = "야간|심야|주말|휴일"
    is_regular = row_df["ticket_type"] == "정기권"
    kakao_ok = is_regular & ~row_df["kakao_product_name"].fillna("").str.contains(exclude, regex=True)
    modu_ok = is_regular & ~row_df["modu_product_name"].fillna("").str.contains(exclude, regex=True)
    kakao_val = row_df["kakao_stock"].where(kakao_ok, 0).fillna(0)
    modu_val = row_df["modu_stock"].where(modu_ok, 0).fillna(0)
    return (kakao_val + modu_val).sum()


def allday_stock_by_day(row_df, day_char):
    is_allday = row_df["ticket_type"] == "종일권"
    kakao_ok = is_allday & row_df["kakao_sales_days"].fillna("").str.contains(day_char, regex=False)
    modu_ok = is_allday & row_df["modu_sales_days"].fillna("").str.contains(day_char, regex=False)
    kakao_val = row_df["kakao_stock"].where(kakao_ok, 0).fillna(0)
    modu_val = row_df["modu_stock"].where(modu_ok, 0).fillna(0)
    return (kakao_val + modu_val).sum()


# 전체 데이터 기준 테스트
general_regular = regular_stock(df)
day_stock = {d: allday_stock_by_day(df, d) for d in ["월", "화", "수", "목", "금", "토", "일"]}
weekday_avg = round((day_stock["월"] + day_stock["화"] + day_stock["수"] + day_stock["목"] + day_stock["금"]) / 5)
weekend_avg = round((day_stock["토"] + day_stock["일"]) / 2)

print(f"전체 기준 일반 정기권 재고: {general_regular:,.0f}")
print(f"요일별 종일권 재고: {day_stock}")
print(f"평일 평균: {weekday_avg:,.0f} / 휴일 평균: {weekend_avg:,.0f}")

# 특정 현장 하나로 필터링해서 원본 이미지(원센티널, KMP9900198)와 유사한 케이스로 검증
site_df = df[df["pjt_code"] == "KMP9900198"]
if not site_df.empty:
    print("\n=== KMP9900198(원센티널) 원본 데이터 ===")
    print(site_df[["ticket_type", "kakao_product_name", "kakao_stock", "modu_product_name", "modu_stock", "kakao_sales_days", "modu_sales_days"]].to_string())
    print(f"\n일반 정기권 재고: {regular_stock(site_df):,.0f}")
    site_day_stock = {d: allday_stock_by_day(site_df, d) for d in ["월", "화", "수", "목", "금", "토", "일"]}
    print(f"요일별 종일권 재고: {site_day_stock}")
else:
    print("KMP9900198 데이터 없음 (다른 현장 코드일 수 있음)")

print("\n로직 정상 실행됨 - 에러 없음")

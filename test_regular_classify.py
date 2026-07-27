# -*- coding: utf-8 -*-
import re
import os
import pandas as pd
from google.cloud import bigquery

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
    os.path.dirname(__file__), "credentials", "kmp-platform-database-85d721abe80f.json"
)
PROJECT = "kmp-platform-database"
client = bigquery.Client()
df = client.query(f"SELECT * FROM `{PROJECT}.db_platform_kmp.tb_integrated_product_data_wide`").to_dataframe()


def classify_regular_product(name):
    if not name:
        return set()
    has_night = bool(re.search("야간|심야", name))
    has_weekday = bool(re.search("평일", name))
    has_holiday = bool(re.search("휴일|주말", name))

    if has_night and has_holiday and has_weekday:
        return {"야간", "휴일"}
    if has_night and has_holiday:
        return {"휴일"}
    if has_weekday and has_night:
        return {"야간"}
    if has_weekday and has_holiday:
        return {"일반"}
    if has_night:
        return {"야간"}
    if has_weekday:
        return {"평일"}
    if has_holiday:
        return {"휴일"}
    return {"일반"}


def regular_stock_by_category(row_df):
    totals = {"일반": 0.0, "야간": 0.0, "평일": 0.0, "휴일": 0.0}
    sub = row_df[row_df["ticket_type"] == "정기권"]
    for _, r in sub.iterrows():
        for name_col, stock_col in [("kakao_product_name", "kakao_stock"), ("modu_product_name", "modu_stock")]:
            name = r[name_col]
            stock = r[stock_col]
            if pd.isna(name) or pd.isna(stock):
                continue
            for c in classify_regular_product(name):
                totals[c] += stock
    return totals


# 각 고유 상품명이 어떤 카테고리로 분류되는지 확인
regular_df = df[df["ticket_type"] == "정기권"]
names = set(regular_df["kakao_product_name"].dropna()) | set(regular_df["modu_product_name"].dropna())
print(f"정기권 고유 상품명 수: {len(names)}")
print("\n=== 분류 결과 샘플 (2개 카테고리에 속하는 것들) ===")
for n in sorted(names):
    cats = classify_regular_product(n)
    if len(cats) > 1:
        print(f"  {n} -> {cats}")

print("\n=== 전체 기준 카테고리별 재고 합계 ===")
totals = regular_stock_by_category(df)
for k, v in totals.items():
    print(f"  {k}: {v:,.0f}")

print("\n로직 정상 실행됨")

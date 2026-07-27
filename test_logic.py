import os
import pandas as pd
from google.cloud import bigquery

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
    os.path.dirname(__file__), "credentials", "kmp-platform-database-85d721abe80f.json"
)
PROJECT = "kmp-platform-database"
client = bigquery.Client()

df = client.query(f"SELECT * FROM `{PROJECT}.db_platform_kmp.tb_integrated_product_data_wide`").to_dataframe()
site_df = client.query(f"""
    SELECT pjt_code, site_name, operation_hq, operation_manager,
           contract_type, spaces_count, open_date, close_date
    FROM `{PROJECT}.db_platform_kmp.tb_site_data_kmp_recent`
""").to_dataframe()

df["site_label"] = df["site_name"] + " (" + df["pjt_code"] + ")"
site_options = sorted(df["site_label"].dropna().unique())
print(f"site_label 샘플: {site_options[:3]}")

sample_label = site_options[0]
pjt_code = sample_label.split("(")[-1].rstrip(")")
print(f"추출된 pjt_code: {pjt_code}")
print(site_df[site_df["pjt_code"] == pjt_code])

filtered = df[df["site_label"] == sample_label].copy()
filtered["가격차이"] = filtered["kakao_price"] - filtered["modu_price"]
filtered["재고차이"] = filtered["kakao_stock"] - filtered["modu_stock"]
print("\n필터링 결과:")
print(filtered[["site_name", "ticket_type", "kakao_price", "modu_price", "가격차이", "재고차이"]])

chart_data = (
    filtered.groupby("ticket_type")[["kakao_stock", "modu_stock"]]
    .mean()
    .reset_index()
)
print("\n차트용 집계:")
print(chart_data)

kakao_avg = filtered["kakao_stock"].mean()
print(f"\nkakao_avg pd.isna check: {pd.isna(kakao_avg)}, value: {kakao_avg}")

print("\n모든 로직 정상 실행됨")

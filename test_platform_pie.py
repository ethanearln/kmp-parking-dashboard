import os
import pandas as pd
from google.cloud import bigquery

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
    os.path.dirname(__file__), "credentials", "kmp-platform-database-85d721abe80f.json"
)
PROJECT = "kmp-platform-database"
client = bigquery.Client()
df = client.query(f"SELECT * FROM `{PROJECT}.db_platform_kmp.tb_integrated_product_data_wide`").to_dataframe()

ticket_types = sorted(df["ticket_type"].dropna().unique())
plat_totals = []
for tt in ticket_types:
    sub = df[df["ticket_type"] == tt]
    kakao_sum = sub["kakao_stock"].fillna(0).sum()
    modu_sum = sub["modu_stock"].fillna(0).sum()
    if kakao_sum + modu_sum > 0:
        plat_totals.append((tt, kakao_sum, modu_sum))

for tt, k, m in plat_totals:
    total = k + m
    print(f"{tt}: 카카오T {k:,.0f} ({k/total*100:.1f}%) / 모두의주차장 {m:,.0f} ({m/total*100:.1f}%)")

print("\n로직 정상 실행됨")

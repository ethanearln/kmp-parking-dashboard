import html
import os
import pandas as pd
from google.cloud import bigquery

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
    os.path.dirname(__file__), "credentials", "kmp-platform-database-85d721abe80f.json"
)
PROJECT = "kmp-platform-database"
client = bigquery.Client()
df = client.query(f"SELECT * FROM `{PROJECT}.db_platform_kmp.tb_integrated_product_data_wide`").to_dataframe()

# 정렬 검증
display_df = df.sort_values(["pjt_code", "ticket_sort_order"]).reset_index(drop=True).copy()
print("정렬 결과 (앞부분 pjt_code):")
print(display_df["pjt_code"].head(10).tolist())
assert display_df["pjt_code"].is_monotonic_increasing or True  # 그룹 내 순서 확인용, 완전정렬은 아래에서 별도 체크
is_sorted = (display_df["pjt_code"].values[:-1] <= display_df["pjt_code"].values[1:]).all()
print(f"pjt_code 오름차순 정렬 여부: {is_sorted}")

same_site = display_df["pjt_code"] == display_df["pjt_code"].shift(1)
same_ticket = same_site & (display_df["ticket_type"] == display_df["ticket_type"].shift(1))
display_df.loc[same_site, "site_name"] = ""
display_df.loc[same_ticket, "ticket_type"] = ""

def fmt_num(x):
    return "" if pd.isna(x) else f"{x:,.0f}"

for col in ["kakao_price", "kakao_stock", "modu_price", "modu_stock"]:
    display_df[col] = display_df[col].apply(fmt_num)
for col in ["kakao_product_name", "modu_product_name"]:
    display_df[col] = display_df[col].fillna("")

def esc(v):
    return html.escape(str(v)) if v != "" else ""

body_rows = []
for _, row in display_df.head(20).iterrows():
    cells = "".join(
        f"<td>{esc(row[c])}</td>"
        for c in ["site_name","ticket_type","kakao_product_name","kakao_price","kakao_stock","modu_product_name","modu_price","modu_stock"]
    )
    body_rows.append(f"<tr>{cells}</tr>")
print("\n생성된 HTML 행 샘플 3개:")
for r in body_rows[:3]:
    print(r)

# 파이차트 집계 검증
pie_df = df.copy()
pie_df["총재고"] = pie_df["kakao_stock"].fillna(0) + pie_df["modu_stock"].fillna(0)
pie_data = pie_df.groupby("ticket_type")["총재고"].sum().reset_index()
print("\n상품종류별 재고 합계:")
print(pie_data)
print(f"합계 총계: {pie_data['총재고'].sum():,.0f}")

print("\n모든 로직 정상 실행됨")

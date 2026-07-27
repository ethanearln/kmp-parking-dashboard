import os
from google.cloud import bigquery

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
    os.path.dirname(__file__), "credentials", "kmp-platform-database-85d721abe80f.json"
)

client = bigquery.Client()

queries = {
    "tb_integrated_product_data_wide (샘플)": """
        SELECT * FROM `kmp-platform-database.db_platform_kmp.tb_integrated_product_data_wide`
        ORDER BY pjt_code, ticket_sort_order LIMIT 15
    """,
    "site_name 목록 (고유값 개수)": """
        SELECT COUNT(DISTINCT pjt_code) AS site_count
        FROM `kmp-platform-database.db_platform_kmp.tb_integrated_product_data_wide`
    """,
    "tb_site_data_kmp_recent (샘플)": """
        SELECT * FROM `kmp-platform-database.db_platform_kmp.tb_site_data_kmp_recent`
        LIMIT 5
    """,
    "tb_integrated_product_data (샘플, long format)": """
        SELECT * FROM `kmp-platform-database.db_platform_kmp.tb_integrated_product_data`
        ORDER BY pjt_code LIMIT 10
    """,
}

for title, q in queries.items():
    print(f"\n{'='*70}\n{title}\n{'='*70}")
    df = client.query(q).to_dataframe()
    print(df.to_string())

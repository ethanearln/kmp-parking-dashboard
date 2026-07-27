import os
from google.cloud import bigquery

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
    os.path.dirname(__file__), "credentials", "kmp-platform-database-85d721abe80f.json"
)

client = bigquery.Client()

for dataset_id in ["db_platform_kakao", "db_platform_kmp", "db_platform_modu"]:
    print(f"\n{'='*60}")
    print(f"데이터셋: {dataset_id}")
    print('='*60)
    tables = list(client.list_tables(dataset_id))
    if not tables:
        print("(테이블 없음)")
        continue
    for table in tables:
        table_ref = client.get_table(f"{dataset_id}.{table.table_id}")
        print(f"\n  [테이블] {table.table_id}  (행 수: {table_ref.num_rows}, 크기: {round(table_ref.num_bytes/1024/1024, 2)}MB)")
        for field in table_ref.schema:
            print(f"    - {field.name} ({field.field_type})")

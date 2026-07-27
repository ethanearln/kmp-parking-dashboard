import os
from google.cloud import bigquery

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(
    os.path.dirname(__file__), "credentials", "kmp-platform-database-85d721abe80f.json"
)

client = bigquery.Client()
print(f"연결된 프로젝트: {client.project}")

print("\n=== 데이터셋 목록 ===")
for dataset in client.list_datasets():
    print(f"- {dataset.dataset_id}")

import os
from google import genai

# Load .env manually
env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_file):
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.strip().split('=', 1)
                os.environ[k.strip()] = v.strip().strip("'\"")

try:
    client = genai.Client()
    models = client.models.list()
    print("=== 사용 가능한 제미나이 모델 목록 ===")
    for m in models:
        print(f"- {m.name}")
except Exception as e:
    print(f"모델 목록을 불러오지 못했습니다: {e}")

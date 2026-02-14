import requests
import time
import json

BASE_URL = "http://localhost:8081"
HEADERS = {"Authorization": "Bearer ***REDACTED***"}

def test_switch(model_alias):
    print(f"Switching to {model_alias}...")
    url = f"{BASE_URL}/model/switch"
    data = {"model": model_alias}
    try:
        response = requests.post(url, json=data, headers=HEADERS, timeout=60) # Increased timeout for container start
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        if response.status_code == 200:
            return True
    except Exception as e:
        print(f"Failed: {e}")
    return False

def check_system_status():
    url = f"{BASE_URL}/system/status"
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if "ai_service" in data:
                 print(f"AI Service Status: {data['ai_service']}")
                 return data['ai_service']
    except Exception as e:
        print(f"Status check failed: {e}")
    return None

def main():
    print("--- Verifying Backend Switching ---")
    
    # 1. Switch to VLLM model
    target_model = "Qwen2.5-Coder-32B-Instruct-AWQ"
    if test_switch(target_model):
        print("Switch request successful. Waiting for backend...")
        for i in range(12): # Wait up to 60s
            status = check_system_status()
            if status and status.get('backend') == 'vllm':
                print("SUCCESS: Backend switched to vllm!")
                break
            time.sleep(5)
    else:
        print("Switch failed.")

if __name__ == "__main__":
    main()

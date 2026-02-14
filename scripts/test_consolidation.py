import requests
import os
import json
import time

BASE_URL = "http://localhost:8081"
HEADERS = {"Authorization": "Bearer ***REDACTED***"}

def test_endpoint(name, url):
    print(f"Testing {name} ({url})...")
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            try:
                data = response.json()
                print(f"Data (keys): {list(data.keys()) if isinstance(data, dict) else 'List'}")
                if "ai_service" in data:
                     print(f"AI Service Status: {data['ai_service'].get('status')}")
                if "host" in data:
                     host_data = data['host']
                     if isinstance(host_data, dict) and "cpu_load_percent" in host_data:
                         print(f"Host Metrics Verified (CPU Load: {host_data['cpu_load_percent']}%)")
                     else:
                         print(f"WARNING: Invalid Host Data format: {host_data}")
                if "gpus" in data:
                     gpus = data['gpus']
                     print(f"GPU Count: {len(gpus)}")
                     if len(gpus) > 0:
                         gpu = gpus[0]
                         if "resources" in gpu and "gpu_load_percent" in gpu["resources"]:
                             print(f"GPU Metrics Verified (Load: {gpu['resources']['gpu_load_percent']}%)")
                         else:
                             print(f"WARNING: Invalid GPU Data format: {gpu.keys()}")
                return True
            except:
                print("Response not JSON")
                print(response.text[:100])
        else:
            print(f"Error Response: {response.text[:100]}")
    except Exception as e:
        print(f"Failed: {e}")
    return False

def main():
    print("--- Starting Validation ---")
    
    # 1. System Status
    success = test_endpoint("System Status", f"{BASE_URL}/system/status")
    
    # 2. Metrics Alias
    test_endpoint("Metrics Alias", f"{BASE_URL}/metrics")
    
    # 3. Model Current
    test_endpoint("Model Current", f"{BASE_URL}/model/current")
    
    # 4. Model Available
    test_endpoint("Model Available", f"{BASE_URL}/model/available")
    
    # 5. OpenAI Models
    test_endpoint("OpenAI Models", f"{BASE_URL}/v1/models")

    if success:
        print("\n--- Basic Checks Passed ---")
    else:
        print("\n--- Basic Checks Failed ---")

if __name__ == "__main__":
    main()

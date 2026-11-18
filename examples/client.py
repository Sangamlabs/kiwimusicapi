"""
Kiwi Music API - Python Client Example
Demonstrates search, resolve, streaming, and usage tracking with error handling.
"""

import os
import sys
import httpx

API_URL = os.getenv("KIWI_API_URL", "http://localhost:8000")
API_KEY = os.getenv("KIWIAPI", "YOUR_API_KEY_HERE")

def main():
    if len(sys.argv) > 1:
        key = sys.argv[1]
    else:
        key = API_KEY

    # Can pass either "KIWIAPI" or "X-API-Key"
    headers = {"KIWIAPI": key}

    with httpx.Client(base_url=API_URL, headers=headers, timeout=30.0) as client:
        # 1. Search for a song (Free endpoint)
        print("[1] Searching for 'Believer'...")
        search_res = client.get("/v1/search", params={"q": "Believer", "limit": 3})
        print(f"Status: {search_res.status_code}")
        search_data = search_res.json()
        print(f"Results: {search_data}")

        if not search_data.get("results"):
            print("No tracks found.")
            return

        first_track = search_data["results"][0]
        track_id = first_track["track_id"]
        print(f"\n[2] Resolving track: {track_id} - '{first_track['title']}'...")

        # 2. Resolve to Yukisbox Storage URL (Billable endpoint - 1 quota unit)
        resolve_res = client.get(f"/v1/resolve/{track_id}")
        print(f"Resolve Status: {resolve_res.status_code}")
        resolve_data = resolve_res.json()
        print(f"Resolve Data: {resolve_data}")
        print(f"Direct Music Stream URL: {resolve_data.get('music_url')}")

        # 3. Check API Key Usage (Free endpoint)
        print("\n[3] Checking API Key Usage...")
        usage_res = client.get("/v1/usage")
        print(f"Usage Status: {usage_res.status_code}")
        print(f"Usage Data: {usage_res.json()}")

if __name__ == "__main__":
    main()


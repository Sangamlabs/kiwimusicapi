package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"time"
)

const (
	defaultAPIURL = "http://localhost:8000"
	defaultAPIKey = "YOUR_API_KEY_HERE"
)

type TrackResult struct {
	TrackID   string `json:"track_id"`
	Title     string `json:"title"`
	Artist    string `json:"artist"`
	Duration  int    `json:"duration"`
	Thumbnail string `json:"thumbnail"`
}

type SearchResponse struct {
	Success bool          `json:"success"`
	Count   int           `json:"count"`
	Results []TrackResult `json:"results"`
}

type ResolveResponse struct {
	Success    bool   `json:"success"`
	TrackID    string `json:"track_id"`
	Title      string `json:"title"`
	Artist     string `json:"artist"`
	Storage    string `json:"storage"`
	StorageUID string `json:"storage_uid"`
	MusicURL   string `json:"music_url"`
	Cached     bool   `json:"cached"`
	Duration   int    `json:"duration"`
}

func main() {
	apiURL := defaultAPIURL
	apiKey := defaultAPIKey

	if envKey := os.Getenv("KIWIAPI"); envKey != "" {
		apiKey = envKey
	}
	if len(os.Args) > 1 {
		apiKey = os.Args[1]
	}

	client := &http.Client{Timeout: 30 * time.Second}

	// 1. Search Track (Free endpoint)
	fmt.Println("[1] Searching for 'Believer'...")
	searchURL := fmt.Sprintf("%s/v1/search?q=%s&limit=3", apiURL, url.QueryEscape("Believer"))
	req, err := http.NewRequest("GET", searchURL, nil)
	if err != nil {
		panic(err)
	}
	req.Header.Set("KIWIAPI", apiKey)

	resp, err := client.Do(req)
	if err != nil {
		panic(err)
	}
	defer resp.Body.Close()

	bodyBytes, _ := io.ReadAll(resp.Body)
	var searchResp SearchResponse
	json.Unmarshal(bodyBytes, &searchResp)
	fmt.Printf("Search Status: %d, Found: %d\n", resp.StatusCode, searchResp.Count)

	if len(searchResp.Results) == 0 {
		fmt.Println("No tracks found.")
		return
	}

	targetTrack := searchResp.Results[0]
	fmt.Printf("\n[2] Resolving track %s ('%s')...\n", targetTrack.TrackID, targetTrack.Title)

	// 2. Resolve Track (Billable endpoint)
	resolveURL := fmt.Sprintf("%s/v1/resolve/%s", apiURL, targetTrack.TrackID)
	req2, _ := http.NewRequest("GET", resolveURL, nil)
	req2.Header.Set("KIWIAPI", apiKey)

	resp2, err := client.Do(req2)
	if err != nil {
		panic(err)
	}
	defer resp2.Body.Close()

	bodyBytes2, _ := io.ReadAll(resp2.Body)
	var resolveResp ResolveResponse
	json.Unmarshal(bodyBytes2, &resolveResp)
	fmt.Printf("Resolve Status: %d, Cached: %t\n", resp2.StatusCode, resolveResp.Cached)
	fmt.Printf("Yukisbox Music URL: %s\n", resolveResp.MusicURL)

	// 3. Check Usage
	fmt.Println("\n[3] Checking API Usage...")
	usageURL := fmt.Sprintf("%s/v1/usage", apiURL)
	req3, _ := http.NewRequest("GET", usageURL, nil)
	req3.Header.Set("KIWIAPI", apiKey)

	resp3, err := client.Do(req3)
	if err != nil {
		panic(err)
	}
	defer resp3.Body.Close()

	bodyBytes3, _ := io.ReadAll(resp3.Body)
	fmt.Printf("Usage Response: %s\n", string(bodyBytes3))
}


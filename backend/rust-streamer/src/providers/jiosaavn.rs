use base64::prelude::*;
use cipher::{BlockDecrypt, KeyInit};
use des::Des;
use regex::Regex;
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, USER_AGENT};
use serde_json::Value;
use std::collections::HashSet;
use std::time::Duration;
use tracing::{info, warn};

use crate::error::ProviderError;
use crate::models::TrackMetadata;

const DES_CIPHER_KEY: &[u8; 8] = b"38346591";

pub struct JioSaavnProvider {
    client: reqwest::Client,
    base_url: Option<String>,
    match_threshold: f64,
}

impl JioSaavnProvider {
    pub fn new(base_url: Option<String>, timeout_secs: u64, match_threshold: f64) -> Self {
        let mut headers = HeaderMap::new();
        headers.insert(
            USER_AGENT,
            HeaderValue::from_static(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ),
        );
        headers.insert(ACCEPT, HeaderValue::from_static("application/json"));

        let client = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(timeout_secs))
            .build()
            .unwrap_or_default();

        Self {
            client,
            base_url,
            match_threshold,
        }
    }

    /// Decrypt JioSaavn DES-ECB encrypted_media_url with PKCS#5 unpadding.
    pub fn decrypt_media_url(encrypted_url: &str) -> Result<String, ProviderError> {
        let trimmed = encrypted_url.trim();
        if trimmed.is_empty() {
            return Err(ProviderError::Decryption(
                "Encrypted URL is empty".to_string(),
            ));
        }

        let raw_cipher = BASE64_STANDARD
            .decode(trimmed)
            .map_err(|e| ProviderError::Decryption(format!("Base64 decode failed: {}", e)))?;

        if raw_cipher.len() % 8 != 0 {
            return Err(ProviderError::Decryption(
                "Ciphertext length must be a multiple of 8 bytes".to_string(),
            ));
        }

        let cipher = Des::new_from_slice(DES_CIPHER_KEY)
            .map_err(|e| ProviderError::Decryption(format!("DES init failed: {}", e)))?;

        let mut decrypted = raw_cipher.clone();
        for chunk in decrypted.as_chunks_mut::<8>().0 {
            let block = cipher::generic_array::GenericArray::from_mut_slice(chunk);
            cipher.decrypt_block(block);
        }

        if decrypted.is_empty() {
            return Err(ProviderError::Decryption(
                "Decrypted buffer empty".to_string(),
            ));
        }

        let pad = *decrypted.last().unwrap() as usize;
        if pad == 0 || pad > 8 || pad > decrypted.len() {
            return Err(ProviderError::Decryption(
                "Invalid PKCS5 padding".to_string(),
            ));
        }

        decrypted.truncate(decrypted.len() - pad);

        let mut url = String::from_utf8(decrypted)
            .map_err(|e| ProviderError::Decryption(format!("UTF-8 decode failed: {}", e)))?;

        // Upgrade 96kbps to 160kbps if applicable
        if url.contains("_96.mp4") {
            url = url.replace("_96.mp4", "_160.mp4");
        }

        Ok(url)
    }

    /// Extract meaningful alphanumeric tokens from text and strip common stopwords.
    pub fn get_tokens(text: &str) -> HashSet<String> {
        let stop_words: HashSet<&'static str> = [
            "the",
            "a",
            "an",
            "and",
            "or",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "ft",
            "feat",
            "song",
            "video",
            "official",
            "4k",
            "8k",
            "hd",
            "1080p",
            "720p",
            "lyrics",
            "lyric",
            "audio",
            "full",
            "visualizer",
            "edit",
            "amv",
            "remix",
            "version",
            "records",
            "music",
        ]
        .iter()
        .copied()
        .collect();

        let re = Regex::new(r"[a-zA-Z0-9]+").unwrap();
        re.find_iter(text)
            .map(|m| m.as_str().to_lowercase())
            .filter(|w| !stop_words.contains(w.as_str()) && w.len() > 1)
            .collect()
    }

    /// Calculate confidence score between search query / YouTube title and a JioSaavn song.
    pub fn score_match(yt_title: &str, item_title: &str, artists: &str, album: &str) -> f64 {
        let yt_tokens = Self::get_tokens(yt_title);
        if yt_tokens.is_empty() {
            return 0.0;
        }

        let song_tokens = Self::get_tokens(item_title);
        if song_tokens.is_empty() {
            return 0.0;
        }

        // Title token match ratio
        let matched_title_count = yt_tokens.intersection(&song_tokens).count();
        let mut title_ratio = matched_title_count as f64 / song_tokens.len() as f64;

        // Single word titles need higher verification
        if song_tokens.len() == 1 {
            title_ratio = if title_ratio >= 1.0 { 0.50 } else { 0.0 };
        }

        // Artist bonus (+0.20 per matching artist token, max +0.40)
        let artist_tokens = Self::get_tokens(artists);
        let artist_match_count = yt_tokens.intersection(&artist_tokens).count();
        let artist_bonus = (artist_match_count as f64 * 0.20).min(0.40);

        // Album bonus (+0.10 per matching album token, max +0.20)
        let album_tokens = Self::get_tokens(album);
        let album_match_count = yt_tokens.intersection(&album_tokens).count();
        let album_bonus = (album_match_count as f64 * 0.10).min(0.20);

        title_ratio + artist_bonus + album_bonus
    }

    /// Generate cleaned progressive search candidates from raw titles.
    pub fn generate_search_candidates(raw_title: &str) -> Vec<String> {
        let mut candidates = Vec::new();
        let noise_re = Regex::new(r"(?i)\b(4k|8k|hd|1080p|720p|official music video|official video|full video song|full video|video song|music video|official audio|lyric video|lyrics video|lyrics|audio song|full song|visualizer|tiktok version|tiktok remix|edit audio|edit|amv|video|audio|song)\b").unwrap();
        let brackets_re = Regex::new(r"[\(\[\{][^\)\]\}]*[\)\]\}]").unwrap();
        let non_ascii_re = Regex::new(r"[^\x00-\x7F]+").unwrap();

        fn add_candidate(candidates: &mut Vec<String>, s: &str) {
            let clean = s.trim().trim_matches(|c| c == '-' || c == '|' || c == ':');
            if clean.len() >= 2 && !candidates.contains(&clean.to_string()) {
                candidates.push(clean.to_string());
            }
        }

        // 1. Delimited components: [Song] | [Album/Movie] | [Artist]
        let raw_parts: Vec<&str> = raw_title.split(['|', '/', ':']).collect();
        let mut clean_parts = Vec::new();

        for part in raw_parts {
            let no_brackets = brackets_re.replace_all(part, " ");
            let no_noise = noise_re.replace_all(&no_brackets, " ");
            let ascii = non_ascii_re.replace_all(&no_noise, " ");
            let clean = ascii
                .trim()
                .trim_matches(|c| c == '-' || c == '|' || c == ':');
            if clean.len() >= 2 {
                clean_parts.push(clean.to_string());
            }
        }

        if clean_parts.len() >= 2 {
            // [Song Name] + [Movie/Album]
            add_candidate(
                &mut candidates,
                &format!("{} {}", clean_parts[0], clean_parts[1]),
            );
            // Just [Song Name]
            add_candidate(&mut candidates, &clean_parts[0]);
            // [Song Name] + [Artist]
            for extra in clean_parts.iter().skip(2) {
                add_candidate(&mut candidates, &format!("{} {}", clean_parts[0], extra));
            }
        }

        // Clean raw
        let no_brackets = brackets_re.replace_all(raw_title, " ");
        let no_noise = noise_re.replace_all(&no_brackets, " ");
        let clean_raw = non_ascii_re.replace_all(&no_noise, " ");

        // Cut before ft./feat.
        let ft_re = Regex::new(r"(?i)\b(ft\.?|feat\.?).*$").unwrap();
        let no_ft = ft_re.replace_all(&clean_raw, " ");
        add_candidate(&mut candidates, &no_ft);

        add_candidate(&mut candidates, &clean_raw);
        if candidates.is_empty() {
            add_candidate(&mut candidates, raw_title);
        }

        candidates
    }

    /// Parse a raw JioSaavn JSON item into standardized TrackMetadata.
    pub fn normalize_item(item: &Value) -> Option<TrackMetadata> {
        let song_id = item
            .get("id")
            .or_else(|| item.get("song_id"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())?;

        let title = item
            .get("song")
            .or_else(|| item.get("title"))
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown Title")
            .trim()
            .to_string();

        let artist = item
            .get("primary_artists")
            .or_else(|| item.get("singers"))
            .or_else(|| item.get("music"))
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown Artist")
            .trim()
            .to_string();

        let album = item
            .get("album")
            .and_then(|v| v.as_str())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());

        let duration = item
            .get("duration")
            .and_then(|v| {
                if let Some(n) = v.as_u64() {
                    Some(n)
                } else if let Some(s) = v.as_str() {
                    s.parse::<u64>().ok()
                } else {
                    None
                }
            })
            .unwrap_or(0);

        let language = item
            .get("language")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        let thumbnail = item.get("image").and_then(|v| v.as_str()).map(|img| {
            img.replace("50x50", "500x500")
                .replace("150x150", "500x500")
        });

        // Resolve audio source URL (try encrypted_media_url first)
        let mut source_url = String::new();
        if let Some(enc) = item.get("encrypted_media_url").and_then(|v| v.as_str()) {
            if let Ok(dec) = Self::decrypt_media_url(enc) {
                source_url = dec;
            }
        }

        if source_url.is_empty() {
            if let Some(preview) = item
                .get("media_preview_url")
                .or_else(|| item.get("media_url"))
                .or_else(|| item.get("url"))
                .and_then(|v| v.as_str())
            {
                source_url = preview.to_string();
            }
        }

        let audio_format = if source_url.contains(".mp4") || source_url.contains(".m4a") {
            "m4a".to_string()
        } else {
            "mp3".to_string()
        };

        Some(TrackMetadata {
            provider: "jiosaavn".to_string(),
            provider_song_id: song_id.clone(),
            track_id: format!("kiwi_saavn_{}", song_id),
            title,
            artist,
            album,
            duration,
            language,
            thumbnail,
            source_url,
            audio_format,
        })
    }

    /// Search JioSaavn tracks by query string.
    pub async fn search(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<TrackMetadata>, ProviderError> {
        let clean_query = query.trim();
        if clean_query.is_empty() {
            return Ok(Vec::new());
        }

        // 1. Try Sumanjay API base URL if configured
        if let Some(ref base) = self.base_url {
            let url = format!(
                "{}/result/?query={}",
                base.trim_end_matches('/'),
                clean_query
            );
            if let Ok(resp) = self.client.get(&url).send().await {
                if resp.status().is_success() {
                    if let Ok(val) = resp.json::<Value>().await {
                        if let Some(arr) = val.as_array() {
                            let results: Vec<TrackMetadata> = arr
                                .iter()
                                .take(limit)
                                .filter_map(Self::normalize_item)
                                .collect();
                            if !results.is_empty() {
                                return Ok(results);
                            }
                        } else if let Some(arr) = val.get("results").and_then(|r| r.as_array()) {
                            let results: Vec<TrackMetadata> = arr
                                .iter()
                                .take(limit)
                                .filter_map(Self::normalize_item)
                                .collect();
                            if !results.is_empty() {
                                return Ok(results);
                            }
                        }
                    }
                }
            }
        }

        // 2. Direct native JioSaavn API call
        let n_str = limit.min(20).to_string();
        let params = [
            ("__call", "search.getResults"),
            ("_format", "json"),
            ("n", &n_str),
            ("p", "1"),
            ("q", clean_query),
        ];

        let resp = self
            .client
            .get("https://www.jiosaavn.com/api.php")
            .query(&params)
            .send()
            .await
            .map_err(|e| ProviderError::Network {
                provider: "jiosaavn".to_string(),
                message: e.to_string(),
            })?;

        if !resp.status().is_success() {
            return Err(ProviderError::Network {
                provider: "jiosaavn".to_string(),
                message: format!("HTTP {}", resp.status()),
            });
        }

        let data = resp
            .json::<Value>()
            .await
            .map_err(|e| ProviderError::Network {
                provider: "jiosaavn".to_string(),
                message: format!("Malformed JSON: {}", e),
            })?;

        let results: Vec<TrackMetadata> = data
            .get("results")
            .and_then(|r| r.as_array())
            .map(|arr| {
                arr.iter()
                    .take(limit)
                    .filter_map(Self::normalize_item)
                    .collect()
            })
            .unwrap_or_default();

        Ok(results)
    }

    /// Fetch track by track_id or YouTube video ID with confidence score threshold.
    pub async fn get_track(&self, track_id: &str) -> Result<Option<TrackMetadata>, ProviderError> {
        let clean_id = track_id.trim();
        if clean_id.is_empty() {
            return Ok(None);
        }

        // 1. Direct JioSaavn ID (e.g. kiwi_saavn_12345 or numeric 12345)
        let numeric_id = clean_id.strip_prefix("kiwi_saavn_").unwrap_or(clean_id);

        if numeric_id.chars().all(|c| c.is_ascii_digit()) && !numeric_id.is_empty() {
            let params = [
                ("__call", "song.getDetails"),
                ("pids", numeric_id),
                ("_format", "json"),
            ];

            let resp = self
                .client
                .get("https://www.jiosaavn.com/api.php")
                .query(&params)
                .send()
                .await
                .map_err(|e| ProviderError::Network {
                    provider: "jiosaavn".to_string(),
                    message: e.to_string(),
                })?;

            if resp.status().is_success() {
                if let Ok(data) = resp.json::<Value>().await {
                    if let Some(item) = data
                        .get(numeric_id)
                        .or_else(|| data.get("songs").and_then(|s| s.get(0)))
                    {
                        if let Some(mut track) = Self::normalize_item(item) {
                            track.track_id = format!("kiwi_saavn_{}", numeric_id);
                            return Ok(Some(track));
                        }
                    }
                }
            }
        }

        // 2. YouTube Video ID (11 chars alphanumeric / dashes / underscores)
        let yt_re = Regex::new(r"^[a-zA-Z0-9_-]{11}$").unwrap();
        if yt_re.is_match(clean_id) {
            let oembed_url = format!(
                "https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={}&format=json",
                clean_id
            );

            if let Ok(oembed_resp) = self.client.get(&oembed_url).send().await {
                if oembed_resp.status().is_success() {
                    if let Ok(oembed_data) = oembed_resp.json::<Value>().await {
                        if let Some(yt_title) = oembed_data.get("title").and_then(|t| t.as_str()) {
                            let mut best_match: Option<TrackMetadata> = None;
                            let mut best_score = 0.0;

                            for candidate in Self::generate_search_candidates(yt_title) {
                                if let Ok(results) = self.search(&candidate, 5).await {
                                    for item in results {
                                        let score = Self::score_match(
                                            yt_title,
                                            &item.title,
                                            &item.artist,
                                            item.album.as_deref().unwrap_or(""),
                                        );
                                        if score > best_score {
                                            best_score = score;
                                            best_match = Some(item);
                                        }
                                    }
                                }
                                if best_score >= 1.0 {
                                    break;
                                }
                            }

                            if best_score >= self.match_threshold {
                                if let Some(mut matched) = best_match {
                                    info!(
                                        "Matched YouTube ID {} ('{}') with JioSaavn '{}' (score={:.2})",
                                        clean_id, yt_title, matched.title, best_score
                                    );
                                    matched.track_id = clean_id.to_string();
                                    return Ok(Some(matched));
                                }
                            } else {
                                warn!(
                                    "No confident match for YouTube ID {} ('{}', best_score={:.2} < threshold={:.2})",
                                    clean_id, yt_title, best_score, self.match_threshold
                                );
                                return Ok(None);
                            }
                        }
                    }
                }
            }
        }

        // 3. Fallback: fuzzy candidate search using clean_id
        let mut best_match: Option<TrackMetadata> = None;
        let mut best_score = 0.0;

        for candidate in Self::generate_search_candidates(clean_id) {
            if let Ok(results) = self.search(&candidate, 3).await {
                for item in results {
                    let score = Self::score_match(
                        clean_id,
                        &item.title,
                        &item.artist,
                        item.album.as_deref().unwrap_or(""),
                    );
                    if score > best_score {
                        best_score = score;
                        best_match = Some(item);
                    }
                }
            }
            if best_score >= 1.0 {
                break;
            }
        }

        if best_score >= self.match_threshold {
            if let Some(mut matched) = best_match {
                matched.track_id = clean_id.to_string();
                return Ok(Some(matched));
            }
        }

        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_des_decryption() {
        // Known encrypted URL roundtrip test
        // Let's encrypt a string using DES-ECB with key "38346591" and verify decrypt_media_url
        let plain = b"https://aac.saavncdn.com/123/sample_96.mp4";
        let pad_len = 8 - (plain.len() % 8);
        let mut padded = plain.to_vec();
        padded.extend(std::iter::repeat_n(pad_len as u8, pad_len));

        use cipher::{BlockEncrypt, KeyInit};
        let cipher = Des::new_from_slice(DES_CIPHER_KEY).unwrap();
        let mut encrypted = padded.clone();
        for chunk in encrypted.as_chunks_mut::<8>().0 {
            let block = cipher::generic_array::GenericArray::from_mut_slice(chunk);
            cipher.encrypt_block(block);
        }

        let b64 = BASE64_STANDARD.encode(&encrypted);
        let decrypted =
            JioSaavnProvider::decrypt_media_url(&b64).expect("Decryption should succeed");
        // Quality upgrade check: _96.mp4 becomes _160.mp4
        assert_eq!(decrypted, "https://aac.saavncdn.com/123/sample_160.mp4");
    }

    #[test]
    fn test_candidate_generation() {
        let title = "Aaja Mahi Aaja Mahi 4K Video Song | Singh Is Bliing | Akshay Kumar, Amy Jackson | Arijit Singh Song";
        let candidates = JioSaavnProvider::generate_search_candidates(title);
        assert!(!candidates.is_empty());
        // Should contain "Aaja Mahi Singh Is Bliing"
        assert!(candidates
            .iter()
            .any(|c| c.contains("Aaja Mahi") && c.contains("Singh Is Bliing")));
        // Should not have "4K Video Song" noise
        assert!(!candidates.iter().any(|c| c.contains("4K Video Song")));
    }

    #[test]
    fn test_score_match_confidence() {
        let yt_title = "Aaja Mahi Aaja Mahi 4K Video Song | Singh Is Bliing | Akshay Kumar, Amy Jackson | Arijit Singh Song";

        // Good match
        let score_good =
            JioSaavnProvider::score_match(yt_title, "Mahi Aaja", "Arijit Singh", "Singh Is Bliing");
        assert!(score_good >= 0.70, "Good match score was {}", score_good);

        // Devotional / noise match
        let score_bad = JioSaavnProvider::score_match(
            yt_title,
            "Aaja Maa Aaja",
            "Narendra Chanchal",
            "Mata Ki Bheint",
        );
        assert!(score_bad < 0.70, "Bad match score was {}", score_bad);

        // Single word title without artist
        let score_phonk = JioSaavnProvider::score_match(
            "REVENGE - 1HXSX (Brazilian Phonk)",
            "Revenge",
            "Mankirt Aulakh",
            "Revenge",
        );
        assert!(score_phonk < 0.70, "Phonk match score was {}", score_phonk);
    }
}

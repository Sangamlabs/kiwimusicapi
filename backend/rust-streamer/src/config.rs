use std::env;

#[derive(Debug, Clone)]
pub struct ProviderConfig {
    pub provider_order: Vec<String>,
    pub jiosaavn_base_url: Option<String>,
    pub jiosaavn_timeout_secs: u64,
    pub ytdlp_executable: String,
    pub ytdlp_timeout_secs: u64,
    pub match_threshold: f64,
    pub allowed_media_hosts: Vec<String>,
    pub max_concurrency: usize,
}

impl Default for ProviderConfig {
    fn default() -> Self {
        Self {
            provider_order: vec!["jiosaavn".to_string(), "ytdlp".to_string()],
            jiosaavn_base_url: None,
            jiosaavn_timeout_secs: 15,
            ytdlp_executable: detect_ytdlp_executable(),
            ytdlp_timeout_secs: 30,
            match_threshold: 0.60,
            allowed_media_hosts: Vec::new(),
            max_concurrency: 10,
        }
    }
}

fn detect_ytdlp_executable() -> String {
    // 1. Check explicit environment variable
    if let Ok(exe) = env::var("KIWI_YTDLP_EXECUTABLE") {
        if !exe.trim().is_empty() {
            return exe.trim().to_string();
        }
    }

    // 2. Check Windows python user scripts path if on Windows
    #[cfg(target_os = "windows")]
    {
        if let Ok(appdata) = env::var("APPDATA") {
            let user_scripts = format!(r"{}\Python\Python311\Scripts\yt-dlp.exe", appdata);
            if std::path::Path::new(&user_scripts).exists() {
                return user_scripts;
            }
        }
    }

    // 3. Fallback to system PATH
    "yt-dlp".to_string()
}

impl ProviderConfig {
    pub fn from_env() -> Self {
        let mut config = Self::default();

        if let Ok(order_str) = env::var("KIWI_PROVIDER_ORDER") {
            let order: Vec<String> = order_str
                .split(',')
                .map(|s| s.trim().to_lowercase())
                .filter(|s| !s.is_empty())
                .collect();
            if !order.is_empty() {
                config.provider_order = order;
            }
        }

        if let Ok(url) = env::var("JIOSAAVN_BASE_URL") {
            let trimmed = url.trim();
            if !trimmed.is_empty() {
                config.jiosaavn_base_url = Some(trimmed.to_string());
            }
        }

        if let Ok(t_str) = env::var("KIWI_PROVIDER_TIMEOUT") {
            if let Ok(t) = t_str.parse::<u64>() {
                config.jiosaavn_timeout_secs = t;
            }
        }

        if let Ok(y_str) = env::var("KIWI_YTDLP_TIMEOUT") {
            if let Ok(t) = y_str.parse::<u64>() {
                config.ytdlp_timeout_secs = t;
            }
        }

        if let Ok(m_str) = env::var("KIWI_MATCH_THRESHOLD") {
            if let Ok(m) = m_str.parse::<f64>() {
                config.match_threshold = m;
            }
        }

        if let Ok(hosts_str) = env::var("KIWI_ALLOWED_MEDIA_HOSTS") {
            let hosts: Vec<String> = hosts_str
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();
            config.allowed_media_hosts = hosts;
        }

        if let Ok(conc_str) = env::var("KIWI_PROVIDER_MAX_CONCURRENCY") {
            if let Ok(c) = conc_str.parse::<usize>() {
                if c > 0 {
                    config.max_concurrency = c;
                }
            }
        }

        config
    }
}

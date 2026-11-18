use crate::error::ProviderError;
use std::net::{IpAddr, Ipv4Addr};
use url::Url;

pub struct SecurityValidator {
    allowed_hosts: Vec<String>,
}

impl SecurityValidator {
    pub fn new(allowed_hosts: Vec<String>) -> Self {
        Self { allowed_hosts }
    }

    /// Check whether an IP address is private, loopback, link-local, or reserved.
    pub fn is_private_or_restricted_ip(ip: IpAddr) -> bool {
        match ip {
            IpAddr::V4(ipv4) => {
                ipv4.is_loopback()
                    || ipv4.is_private()
                    || ipv4.is_link_local()
                    || ipv4.is_broadcast()
                    || ipv4.is_documentation()
                    || ipv4 == Ipv4Addr::UNSPECIFIED
            }
            IpAddr::V6(ipv6) => {
                ipv6.is_loopback()
                    || ipv6.is_unspecified()
                    // Unique local address fc00::/7
                    || ((ipv6.segments()[0] & 0xfe00) == 0xfc00)
                    // Link-local address fe80::/10
                    || ((ipv6.segments()[0] & 0xffc0) == 0xfe80)
            }
        }
    }

    /// Validate a resolved media source URL against SSRF and host policies.
    pub fn validate_url(&self, raw_url: &str) -> Result<Url, ProviderError> {
        let parsed = Url::parse(raw_url)
            .map_err(|e| ProviderError::Security(format!("Malformed URL: {}", e)))?;

        // 1. Enforce HTTP/HTTPS scheme
        if parsed.scheme() != "http" && parsed.scheme() != "https" {
            return Err(ProviderError::Security(format!(
                "Unsupported URL scheme '{}'. Only HTTP/HTTPS allowed",
                parsed.scheme()
            )));
        }

        let host_str = parsed
            .host_str()
            .ok_or_else(|| ProviderError::Security("URL has no host".to_string()))?;

        // 2. Reject explicit localhost
        if host_str.eq_ignore_ascii_case("localhost") {
            return Err(ProviderError::Security(
                "Access to localhost is prohibited".to_string(),
            ));
        }

        // 3. If raw IP address is provided (IPv4 or IPv6), reject private / restricted ranges
        match parsed.host() {
            Some(url::Host::Ipv4(ipv4)) => {
                if Self::is_private_or_restricted_ip(IpAddr::V4(ipv4)) {
                    return Err(ProviderError::Security(format!(
                        "Access to private/restricted IP address '{}' is forbidden",
                        ipv4
                    )));
                }
            }
            Some(url::Host::Ipv6(ipv6)) => {
                if Self::is_private_or_restricted_ip(IpAddr::V6(ipv6)) {
                    return Err(ProviderError::Security(format!(
                        "Access to private/restricted IP address '{}' is forbidden",
                        ipv6
                    )));
                }
            }
            Some(url::Host::Domain(domain)) => {
                let clean_ip = domain.trim_start_matches('[').trim_end_matches(']');
                if let Ok(ip) = clean_ip.parse::<IpAddr>() {
                    if Self::is_private_or_restricted_ip(ip) {
                        return Err(ProviderError::Security(format!(
                            "Access to private/restricted IP address '{}' is forbidden",
                            ip
                        )));
                    }
                }
            }
            None => {
                return Err(ProviderError::Security("URL has no host".to_string()));
            }
        }

        // 4. If allowed_hosts is specified, ensure host matches or is a subdomain
        if !self.allowed_hosts.is_empty() {
            let matches = self.allowed_hosts.iter().any(|allowed| {
                host_str.eq_ignore_ascii_case(allowed)
                    || host_str.ends_with(&format!(".{}", allowed))
            });
            if !matches {
                return Err(ProviderError::Security(format!(
                    "Host '{}' is not in allowed media hosts list",
                    host_str
                )));
            }
        }

        Ok(parsed)
    }
}

/// Convenience function to validate arbitrary URLs against SSRF (private/loopback/cloud metadata)
pub fn validate_url_ssrf(raw_url: &str) -> Result<Url, ProviderError> {
    let validator = SecurityValidator::new(vec![]);
    validator.validate_url(raw_url)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_https_url() {
        let validator = SecurityValidator::new(vec![]);
        assert!(validator
            .validate_url("https://aac.saavncdn.com/song.mp4")
            .is_ok());
    }

    #[test]
    fn test_rejects_localhost() {
        let validator = SecurityValidator::new(vec![]);
        let err = validator.validate_url("http://localhost:8000/media.mp3");
        assert!(matches!(err, Err(ProviderError::Security(_))));
    }

    #[test]
    fn test_rejects_private_ipv4() {
        let validator = SecurityValidator::new(vec![]);
        assert!(validator
            .validate_url("http://127.0.0.1/audio.mp3")
            .is_err());
        assert!(validator
            .validate_url("http://192.168.1.100/audio.mp3")
            .is_err());
        assert!(validator.validate_url("http://10.0.0.5/audio.mp3").is_err());
        assert!(validator
            .validate_url("http://172.16.0.1/audio.mp3")
            .is_err());
        assert!(validator
            .validate_url("http://169.254.169.254/latest/meta-data")
            .is_err());
    }

    #[test]
    fn test_rejects_private_ipv6() {
        let validator = SecurityValidator::new(vec![]);
        assert!(validator.validate_url("http://[::1]/audio.mp3").is_err());
        assert!(validator
            .validate_url("http://[fe80::1]/audio.mp3")
            .is_err());
    }

    #[test]
    fn test_allowed_hosts_filter() {
        let validator = SecurityValidator::new(vec![
            "saavncdn.com".to_string(),
            "googlevideo.com".to_string(),
        ]);
        assert!(validator
            .validate_url("https://aac.saavncdn.com/audio.mp4")
            .is_ok());
        assert!(validator
            .validate_url("https://rr1.googlevideo.com/videoplayback")
            .is_ok());
        assert!(validator
            .validate_url("https://malicious-site.com/track.mp3")
            .is_err());
    }
}

use native_tls::TlsConnector;
use postgres_native_tls::MakeTlsConnector;
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio_postgres::{Client, NoTls};
use tracing::{error, info};

#[derive(Error, Debug)]
pub enum DbError {
    #[error("PostgreSQL client error: {0}")]
    Postgres(#[from] tokio_postgres::Error),

    #[error("Database connection configuration error: {0}")]
    Config(String),

    #[error("TLS connection error: {0}")]
    Tls(String),

    #[error("Database query error: {0}")]
    Query(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LeaseResult {
    Acquired,
    AlreadyCompleted,
    LockedByOther { lock_age_secs: Option<i32> },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DbTrackRecord {
    pub track_id: String,
    pub provider: String,
    pub provider_song_id: String,
    pub title: String,
    pub artist: String,
    pub album: Option<String>,
    pub duration: i32,
    pub language: Option<String>,
    pub thumbnail: Option<String>,
    pub source_url: Option<String>,
    pub storage_provider: String,
    pub storage_uid: Option<String>,
    pub storage_url: Option<String>,
    pub file_size: Option<i64>,
    pub audio_format: String,
    pub status: String,
}

pub struct PostgresManager {
    conn_str: String,
    use_tls: bool,
}

impl PostgresManager {
    pub fn new(database_url: &str) -> Result<Self, DbError> {
        let mut clean_url = database_url.trim().to_string();

        // Convert python asyncpg prefixes to standard postgresql://
        if clean_url.starts_with("postgresql+asyncpg://") {
            clean_url = clean_url.replacen("postgresql+asyncpg://", "postgresql://", 1);
        } else if clean_url.starts_with("postgres://") {
            clean_url = clean_url.replacen("postgres://", "postgresql://", 1);
        }

        // Determine if TLS should be used (Neon / hosted postgres typically requires TLS)
        let use_tls = clean_url.contains("sslmode=require")
            || clean_url.contains("ssl=require")
            || clean_url.contains("neon.tech")
            || clean_url.contains("render.com");

        // Remove parameters that tokio_postgres config parser might reject
        let parsed_conn = clean_url
            .split('?')
            .next()
            .unwrap_or(&clean_url)
            .to_string();

        Ok(Self {
            conn_str: parsed_conn,
            use_tls,
        })
    }

    async fn connect(&self) -> Result<Client, DbError> {
        if self.use_tls {
            let mut builder = TlsConnector::builder();
            builder.danger_accept_invalid_certs(false);
            let tls_connector = builder
                .build()
                .map_err(|e| DbError::Tls(format!("Failed to build TLS connector: {}", e)))?;
            let connector = MakeTlsConnector::new(tls_connector);

            let (client, connection) = tokio_postgres::connect(&self.conn_str, connector)
                .await
                .map_err(DbError::Postgres)?;

            tokio::spawn(async move {
                if let Err(e) = connection.await {
                    error!("PostgreSQL TLS connection error: {}", e);
                }
            });

            Ok(client)
        } else {
            let (client, connection) = tokio_postgres::connect(&self.conn_str, NoTls)
                .await
                .map_err(DbError::Postgres)?;

            tokio::spawn(async move {
                if let Err(e) = connection.await {
                    error!("PostgreSQL connection error: {}", e);
                }
            });

            Ok(client)
        }
    }

    /// Check if track already exists and is available in PostgreSQL `tracks` table
    pub async fn find_track(&self, track_id: &str) -> Result<Option<DbTrackRecord>, DbError> {
        let client = self.connect().await?;
        let query = "SELECT track_id, provider, provider_song_id, title, artist, album, \
                            duration, language, thumbnail, source_url, storage_provider, \
                            storage_uid, storage_url, file_size, audio_format, status \
                     FROM tracks WHERE track_id = $1 AND status = 'available' AND storage_url IS NOT NULL";

        let row_opt = client
            .query_opt(query, &[&track_id])
            .await
            .map_err(DbError::Postgres)?;

        if let Some(row) = row_opt {
            Ok(Some(DbTrackRecord {
                track_id: row.get(0),
                provider: row.get(1),
                provider_song_id: row.get(2),
                title: row.get(3),
                artist: row.get(4),
                album: row.get(5),
                duration: row.get(6),
                language: row.get(7),
                thumbnail: row.get(8),
                source_url: row.get(9),
                storage_provider: row.get(10),
                storage_uid: row.get(11),
                storage_url: row.get(12),
                file_size: row.get(13),
                audio_format: row.get(14),
                status: row.get(15),
            }))
        } else {
            Ok(None)
        }
    }

    /// Atomically acquire single-flight lease on `upload_jobs`
    /// Lease duration: 60 seconds (calculated database-side to prevent clock skew)
    pub async fn try_acquire_lease(
        &self,
        track_id: &str,
        lease_secs: i64,
    ) -> Result<LeaseResult, DbError> {
        let client = self.connect().await?;

        // 1. Check existing job with database-side age calculation
        let check_query = "SELECT status, \
                                  CASE WHEN locked_at IS NOT NULL \
                                       THEN (EXTRACT(EPOCH FROM (NOW() - locked_at)))::int4 \
                                       ELSE NULL \
                                  END AS age_secs \
                           FROM upload_jobs WHERE track_id = $1";
        let existing = client
            .query_opt(check_query, &[&track_id])
            .await
            .map_err(DbError::Postgres)?;

        if let Some(row) = existing {
            let status: String = row.get(0);
            let age_secs: Option<i32> = row.get(1);

            if status == "COMPLETED" {
                return Ok(LeaseResult::AlreadyCompleted);
            }

            if let Some(age) = age_secs {
                if age < lease_secs as i32 {
                    return Ok(LeaseResult::LockedByOther {
                        lock_age_secs: Some(age),
                    });
                }
            }

            // Stale lease or failed job -> reclaim lease
            let update_query = "UPDATE upload_jobs \
                                SET status = 'PROCESSING', \
                                    locked_at = NOW(), \
                                    attempts = attempts + 1, \
                                    updated_at = NOW() \
                                WHERE track_id = $1";
            client
                .execute(update_query, &[&track_id])
                .await
                .map_err(DbError::Postgres)?;

            info!("Reclaimed lease for track_id: {}", track_id);
            return Ok(LeaseResult::Acquired);
        }

        // 2. Insert new job with ON CONFLICT DO NOTHING
        let insert_query = "INSERT INTO upload_jobs (track_id, status, attempts, locked_at, created_at, updated_at) \
                            VALUES ($1, 'PROCESSING', 1, NOW(), NOW(), NOW()) \
                            ON CONFLICT (track_id) DO NOTHING";

        let affected = client
            .execute(insert_query, &[&track_id])
            .await
            .map_err(DbError::Postgres)?;

        if affected == 1 {
            info!("Acquired new lease for track_id: {}", track_id);
            Ok(LeaseResult::Acquired)
        } else {
            // Concurrent insert race condition -> check again
            let recheck = client
                .query_opt(check_query, &[&track_id])
                .await
                .map_err(DbError::Postgres)?;
            if let Some(row) = recheck {
                let status: String = row.get(0);
                if status == "COMPLETED" {
                    Ok(LeaseResult::AlreadyCompleted)
                } else {
                    let age: Option<i32> = row.get(1);
                    Ok(LeaseResult::LockedByOther { lock_age_secs: age })
                }
            } else {
                Ok(LeaseResult::Acquired)
            }
        }
    }

    /// Mark upload_job as COMPLETED
    pub async fn mark_job_completed(&self, track_id: &str) -> Result<(), DbError> {
        let client = self.connect().await?;
        let query = "UPDATE upload_jobs \
                     SET status = 'COMPLETED', \
                         completed_at = NOW(), \
                         locked_at = NULL, \
                         updated_at = NOW() \
                     WHERE track_id = $1";
        client
            .execute(query, &[&track_id])
            .await
            .map_err(DbError::Postgres)?;
        Ok(())
    }

    /// Mark upload_job as FAILED and release lock
    pub async fn mark_job_failed(&self, track_id: &str, error_msg: &str) -> Result<(), DbError> {
        let client = self.connect().await?;
        let query = "UPDATE upload_jobs \
                     SET status = 'FAILED', \
                         last_error = $2, \
                         locked_at = NULL, \
                         updated_at = NOW() \
                     WHERE track_id = $1";
        client
            .execute(query, &[&track_id, &error_msg])
            .await
            .map_err(DbError::Postgres)?;
        Ok(())
    }

    /// Persist verified track to `tracks` table
    pub async fn upsert_track(&self, track: &DbTrackRecord) -> Result<(), DbError> {
        let client = self.connect().await?;
        let query = "INSERT INTO tracks (track_id, provider, provider_song_id, title, artist, album, \
                                         duration, language, thumbnail, source_url, storage_provider, \
                                         storage_uid, storage_url, file_size, audio_format, status, \
                                         created_at, updated_at, last_accessed_at) \
                     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, NOW(), NOW(), NOW()) \
                     ON CONFLICT (track_id) DO UPDATE \
                     SET storage_uid = EXCLUDED.storage_uid, \
                         storage_url = EXCLUDED.storage_url, \
                         file_size = EXCLUDED.file_size, \
                         status = 'available', \
                         updated_at = NOW(), \
                         last_accessed_at = NOW()";

        client
            .execute(
                query,
                &[
                    &track.track_id,
                    &track.provider,
                    &track.provider_song_id,
                    &track.title,
                    &track.artist,
                    &track.album,
                    &track.duration,
                    &track.language,
                    &track.thumbnail,
                    &track.source_url,
                    &track.storage_provider,
                    &track.storage_uid,
                    &track.storage_url,
                    &track.file_size,
                    &track.audio_format,
                    &track.status,
                ],
            )
            .await
            .map_err(DbError::Postgres)?;

        Ok(())
    }
}

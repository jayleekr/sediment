// HTTP client builder + canonical request helpers.
//
// One reqwest::Client per process — reuses connection pool across many
// short-lived subprocess calls when the MCP shim invokes us repeatedly.

use anyhow::Result;
use reqwest::{Client, Method, RequestBuilder, Response, StatusCode};
use serde::de::DeserializeOwned;
use serde::Serialize;
use std::time::Duration;

use crate::error::ApiError;

const USER_AGENT: &str = concat!("sediment-cli/", env!("CARGO_PKG_VERSION"));

pub struct Http {
    pub base_url: String,
    pub token: Option<String>,
    client: Client,
}

impl Http {
    pub fn new(base_url: String, token: Option<String>) -> Result<Self> {
        let client = Client::builder()
            .user_agent(USER_AGENT)
            .pool_idle_timeout(Some(Duration::from_secs(30)))
            .timeout(Duration::from_secs(60))
            .build()?;
        Ok(Self {
            base_url,
            token,
            client,
        })
    }

    fn request(&self, method: Method, path: &str) -> RequestBuilder {
        let url = format!("{}{}", self.base_url.trim_end_matches('/'), path);
        let mut b = self.client.request(method, url);
        if let Some(t) = &self.token {
            if !t.is_empty() {
                b = b.bearer_auth(t);
            }
        }
        b
    }

    pub async fn get_json<T: DeserializeOwned>(&self, path: &str) -> Result<T> {
        let resp = self.request(Method::GET, path).send().await?;
        Self::decode(resp).await
    }

    pub async fn get_json_with<T: DeserializeOwned, Q: Serialize + ?Sized>(
        &self,
        path: &str,
        query: &Q,
    ) -> Result<T> {
        let resp = self.request(Method::GET, path).query(query).send().await?;
        Self::decode(resp).await
    }

    pub async fn post_json<T: DeserializeOwned, B: Serialize>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        let resp = self.request(Method::POST, path).json(body).send().await?;
        Self::decode(resp).await
    }

    pub async fn raw_get(&self, path: &str) -> Result<Response> {
        let resp = self.request(Method::GET, path).send().await?;
        if !resp.status().is_success() {
            let s = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(ApiError::from_status(s.as_u16(), &body).into());
        }
        Ok(resp)
    }

    pub async fn raw_post(&self, path: &str, body: serde_json::Value) -> Result<Response> {
        let resp = self.request(Method::POST, path).json(&body).send().await?;
        if !resp.status().is_success() {
            let s = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(ApiError::from_status(s.as_u16(), &body).into());
        }
        Ok(resp)
    }

    async fn decode<T: DeserializeOwned>(resp: Response) -> Result<T> {
        let status = resp.status();
        if status.is_success() {
            let body = resp.bytes().await?;
            let value = serde_json::from_slice::<T>(&body).map_err(|e| {
                anyhow::anyhow!(
                    "invalid JSON body: {} (first 200 bytes: {})",
                    e,
                    String::from_utf8_lossy(&body)
                        .chars()
                        .take(200)
                        .collect::<String>()
                )
            })?;
            return Ok(value);
        }
        let body = resp.text().await.unwrap_or_default();
        Err(ApiError::from_status(status.as_u16(), &body).into())
    }
}

/// Whether a status warrants a single retry with the server's Retry-After.
pub fn is_transient_retry(status: StatusCode) -> bool {
    matches!(status.as_u16(), 429 | 502 | 503 | 504)
}

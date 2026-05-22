// Error envelope — every failure emits a JSON object on stdout so LLMs and
// shell pipelines can branch deterministically.
//
// Wire shape:
//   {
//     "error": {
//       "code": <int>,        // HTTP-ish status, or -1 for network errors
//       "reason": "snake_case_token",
//       "message": "human-readable detail",
//       "hint": "optional next action"
//     }
//   }

use serde::Serialize;

#[derive(Debug)]
pub struct AppError {
    pub code: i32,
    pub reason: &'static str,
    pub message: String,
    pub hint: Option<&'static str>,
}

#[derive(Serialize)]
pub struct Envelope {
    pub error: ErrorBody,
}

#[derive(Serialize)]
pub struct ErrorBody {
    pub code: i32,
    pub reason: &'static str,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hint: Option<String>,
}

impl AppError {
    pub fn into_envelope(self) -> Envelope {
        Envelope {
            error: ErrorBody {
                code: self.code,
                reason: self.reason,
                message: self.message,
                hint: self.hint.map(|s| s.to_string()),
            },
        }
    }
}

impl Envelope {
    pub fn exit_code(&self) -> i32 {
        match self.error.code {
            401 => 2,
            403 => 3,
            404 => 4,
            429 => 5,
            _ => 1,
        }
    }
}

impl From<anyhow::Error> for AppError {
    fn from(e: anyhow::Error) -> Self {
        // Try to classify by chain — HTTP errors set their own metadata via
        // ApiError; everything else becomes a generic.
        if let Some(api) = e.downcast_ref::<ApiError>() {
            return AppError {
                code: api.status as i32,
                reason: api.reason,
                message: api.message.clone(),
                hint: api.hint,
            };
        }
        if e.downcast_ref::<reqwest::Error>().is_some() {
            return AppError {
                code: -1,
                reason: "network_unreachable",
                message: e.to_string(),
                hint: Some("check your internet / SEDIMENT_BASE_URL"),
            };
        }
        AppError {
            code: 1,
            reason: "internal_error",
            message: e.to_string(),
            hint: None,
        }
    }
}

// Carry HTTP errors with classified reason + hint up through anyhow::Error.
#[derive(Debug, thiserror::Error)]
#[error("{message}")]
pub struct ApiError {
    pub status: u16,
    pub reason: &'static str,
    pub message: String,
    pub hint: Option<&'static str>,
}

impl ApiError {
    pub fn from_status(status: u16, body: &str) -> Self {
        let (reason, hint) = match status {
            400 => ("bad_request", None),
            401 => ("auth_expired", Some("run `sediment auth login`")),
            403 => ("permission_denied", None),
            404 => ("not_found", None),
            429 => ("rate_limited", Some("slow down or wait Retry-After")),
            500..=599 => ("server_error", None),
            _ => ("http_error", None),
        };
        // Don't dump huge bodies into envelopes.
        let snippet: String = body.chars().take(400).collect();
        Self {
            status,
            reason,
            message: format!("HTTP {}: {}", status, snippet),
            hint,
        }
    }
}

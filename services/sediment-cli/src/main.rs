// Sediment CLI — entry point and clap dispatcher.
//
// Usage shape mirrors `gws` (Google Workspace CLI): subcommands per noun,
// JSON-first output, env-overridable token, multi-account via --account.

use anyhow::Result;
use clap::{Parser, Subcommand};

mod auth_flow;
mod commands;
mod config;
mod error;
mod http;
mod output;
mod token;

#[derive(Parser, Debug)]
#[command(
    name = "sediment",
    version,
    about = "Sediment CLI — evidence-grounded memory on the command line"
)]
struct Cli {
    /// Account to use (email). Selects the keychain entry. Falls back to
    /// `sediment auth default`'s setting if omitted.
    #[arg(long, global = true, env = "SEDIMENT_ACCOUNT")]
    account: Option<String>,

    /// Override the Sediment base URL. Default: production fly.dev URL,
    /// or http://localhost:10100 if SEDIMENT_DEV_MODE=1.
    #[arg(long, global = true, env = "SEDIMENT_BASE_URL")]
    base_url: Option<String>,

    /// Output format. Default: `json` when piped, `table` when a TTY.
    #[arg(long, global = true, value_enum)]
    format: Option<output::Format>,

    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand, Debug)]
enum Cmd {
    /// Manage credentials (login / status / logout / list / default).
    Auth {
        #[command(subcommand)]
        action: AuthCmd,
    },
    /// Show the identity bound to the current token.
    Whoami,
    /// Hybrid search over the vault.
    Search {
        /// Free-text query (Korean or English).
        query: String,
        /// Max results.
        #[arg(short, long, default_value_t = 8)]
        limit: u32,
        /// Optional artifact-type filter.
        #[arg(short = 't', long)]
        r#type: Option<String>,
    },
    /// Ask a natural-language question. Streams the answer.
    Ask {
        /// The question.
        query: String,
        /// Stream tokens to stdout as they arrive (default true on TTY).
        #[arg(long)]
        stream: bool,
    },
    /// Fetch one artifact body by ref path.
    Read { r#ref: String },
    /// List artifacts added/updated in the last N days.
    Recent {
        #[arg(short, long, default_value_t = 7)]
        days: u32,
        #[arg(short = 't', long)]
        r#type: Option<String>,
        #[arg(short, long, default_value_t = 20)]
        limit: u32,
        /// Fetch all pages as NDJSON.
        #[arg(long)]
        page_all: bool,
    },
    /// Introspect the JSON schema of a tool (search, ask, read, recent, whoami).
    Schema { tool: String },
}

#[derive(Subcommand, Debug)]
enum AuthCmd {
    /// Run the device-code OAuth flow and store a JWT in the OS keychain.
    Login {
        /// Pre-bind to a specific account email (skips picker prompts).
        #[arg(long)]
        account: Option<String>,
    },
    /// Print the bound identity for the active account (or --account).
    Status,
    /// Remove stored credentials for the active account (or --account, or all with --all).
    Logout {
        #[arg(long)]
        all: bool,
    },
    /// List every account that has stored credentials on this machine.
    List,
    /// Set the default account used when --account isn't given.
    Default {
        #[arg(long)]
        account: String,
    },
}

#[tokio::main]
async fn main() {
    let exit = match run().await {
        Ok(()) => 0,
        Err(e) => {
            // Errors are emitted as JSON envelopes on stdout (LLM-parseable)
            // and exit non-zero so shells can branch on it.
            let envelope = error::AppError::from(e).into_envelope();
            println!("{}", serde_json::to_string(&envelope).unwrap());
            envelope.exit_code()
        }
    };
    std::process::exit(exit);
}

async fn run() -> Result<()> {
    let cli = Cli::parse();
    let cfg = config::Config::resolve(cli.account.clone(), cli.base_url.clone())?;
    let fmt = cli.format.unwrap_or_else(output::Format::default_for_tty);

    match cli.cmd {
        Cmd::Auth { action } => match action {
            AuthCmd::Login { account } => {
                commands::auth_login(&cfg, account.or(cli.account.clone()), fmt).await
            }
            AuthCmd::Status => commands::auth_status(&cfg, fmt).await,
            AuthCmd::Logout { all } => commands::auth_logout(&cfg, all, fmt).await,
            AuthCmd::List => commands::auth_list(fmt).await,
            AuthCmd::Default { account } => commands::auth_set_default(&account, fmt).await,
        },
        Cmd::Whoami => commands::whoami(&cfg, fmt).await,
        Cmd::Search {
            query,
            limit,
            r#type,
        } => commands::search(&cfg, &query, limit, r#type.as_deref(), fmt).await,
        Cmd::Ask { query, stream } => commands::ask(&cfg, &query, stream, fmt).await,
        Cmd::Read { r#ref } => commands::read(&cfg, &r#ref, fmt).await,
        Cmd::Recent {
            days,
            r#type,
            limit,
            page_all,
        } => commands::recent(&cfg, days, r#type.as_deref(), limit, page_all, fmt).await,
        Cmd::Schema { tool } => commands::schema(&cfg, &tool, fmt).await,
    }
}

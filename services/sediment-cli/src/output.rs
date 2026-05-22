// Output formatters — json (default for pipes), table (default for TTY),
// yaml, and ndjson (one JSON per line for --page-all).
//
// Format detection:
//   --format X        : explicit
//   stdout is TTY     : table
//   stdout is piped   : json
//
// Each command produces a serde_json::Value and hands it here.

use clap::ValueEnum;
use serde::Serialize;
use serde_json::Value;
use std::io::Write;

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum Format {
    Json,
    Table,
    Yaml,
    Ndjson,
}

impl Format {
    pub fn default_for_tty() -> Self {
        if atty::is(atty::Stream::Stdout) {
            Format::Table
        } else {
            Format::Json
        }
    }
}

pub fn render(value: &Value, fmt: Format) -> anyhow::Result<()> {
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    match fmt {
        Format::Json => {
            serde_json::to_writer_pretty(&mut out, value)?;
            writeln!(out)?;
        }
        Format::Yaml => {
            let y = serde_yaml::to_string(value)?;
            out.write_all(y.as_bytes())?;
        }
        Format::Ndjson => {
            // Single value as a single line. For multi-page emit per-page.
            serde_json::to_writer(&mut out, value)?;
            writeln!(out)?;
        }
        Format::Table => render_table(&mut out, value)?,
    }
    Ok(())
}

pub fn render_ndjson_line(value: &Value) -> anyhow::Result<()> {
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    serde_json::to_writer(&mut out, value)?;
    writeln!(out)?;
    Ok(())
}

// ---- table rendering ----
//
// Heuristics rather than a full library — keeps the binary small. Three
// shapes handled:
//   1. Object with "items": [obj, obj, ...] → header row from keys union
//   2. Top-level array of objects → same
//   3. Anything else → pretty JSON fallback so the user always sees the data

fn render_table(out: &mut impl Write, value: &Value) -> anyhow::Result<()> {
    let rows: Option<&Vec<Value>> = match value {
        Value::Object(map) => map.get("items").and_then(|v| v.as_array()),
        Value::Array(arr) => Some(arr),
        _ => None,
    };
    let Some(rows) = rows else {
        serde_json::to_writer_pretty(&mut *out, value)?;
        writeln!(out)?;
        return Ok(());
    };
    if rows.is_empty() {
        writeln!(out, "(no items)")?;
        return Ok(());
    }
    // Union of keys, in first-seen order, capped at 6 columns for readability.
    let mut cols: Vec<String> = Vec::new();
    for r in rows {
        if let Some(obj) = r.as_object() {
            for k in obj.keys() {
                if !cols.contains(k) {
                    cols.push(k.clone());
                }
            }
        }
    }
    cols.truncate(6);

    // Compute widths
    let mut widths: Vec<usize> = cols.iter().map(|c| c.len()).collect();
    let cell_vals: Vec<Vec<String>> = rows
        .iter()
        .map(|r| cols.iter().map(|c| stringify_cell(r.get(c))).collect())
        .collect();
    for row in &cell_vals {
        for (i, cell) in row.iter().enumerate() {
            if cell.len() > widths[i] {
                widths[i] = cell.len().min(60);
            }
        }
    }
    // Header
    for (i, c) in cols.iter().enumerate() {
        if i > 0 {
            write!(out, "  ")?;
        }
        write!(out, "{:<width$}", c, width = widths[i])?;
    }
    writeln!(out)?;
    for (i, w) in widths.iter().enumerate() {
        if i > 0 {
            write!(out, "  ")?;
        }
        write!(out, "{}", "-".repeat(*w))?;
    }
    writeln!(out)?;
    // Rows
    for row in &cell_vals {
        for (i, cell) in row.iter().enumerate() {
            if i > 0 {
                write!(out, "  ")?;
            }
            let truncated = truncate(cell, widths[i]);
            write!(out, "{:<width$}", truncated, width = widths[i])?;
        }
        writeln!(out)?;
    }
    Ok(())
}

fn stringify_cell(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "".into(),
        Some(Value::String(s)) => s.replace('\n', " "),
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::Bool(b)) => b.to_string(),
        Some(other) => serde_json::to_string(other).unwrap_or_default(),
    }
}

fn truncate(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.into()
    } else {
        // Try to keep multibyte-safe — char-by-char.
        let mut out = String::new();
        for (i, ch) in s.chars().enumerate() {
            if i + 1 >= n {
                out.push('…');
                break;
            }
            out.push(ch);
        }
        out
    }
}

// Convenience for commands that produce typed structs.
pub fn render_typed<T: Serialize>(value: &T, fmt: Format) -> anyhow::Result<()> {
    let json = serde_json::to_value(value)?;
    render(&json, fmt)
}

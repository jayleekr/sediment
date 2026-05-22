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
        // std::io::IsTerminal (Rust 1.70+) is more reliable than the
        // atty crate, especially for embedded IDE terminals (Antigravity,
        // VS Code, ...). Honors NO_COLOR convention indirectly via the
        // shell, and works through containerized terminals where atty
        // misreports.
        use std::io::IsTerminal;
        if std::io::stdout().is_terminal() {
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
        // Non-list object → render as a key: value block. Much friendlier
        // than a raw JSON dump for commands like `whoami` or `schema`.
        if let Value::Object(map) = value {
            render_kv_block(out, map, 0)?;
            return Ok(());
        }
        // Scalar at root — print it bare.
        writeln!(out, "{}", value)?;
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

// Render a serde_json::Map as a "key: value" block. Nested objects/arrays
// indent two spaces and recurse. Used by render_table when the root isn't
// a list (e.g. whoami, schema, auth status results).
fn render_kv_block(
    out: &mut impl Write,
    map: &serde_json::Map<String, Value>,
    indent: usize,
) -> std::io::Result<()> {
    let pad = " ".repeat(indent);
    let key_width = map.keys().map(|k| k.len()).max().unwrap_or(0);
    for (k, v) in map {
        match v {
            Value::Object(m) => {
                writeln!(out, "{}{}:", pad, k)?;
                render_kv_block(out, m, indent + 2)?;
            }
            Value::Array(arr) if !arr.is_empty() && arr[0].is_object() => {
                writeln!(out, "{}{}:", pad, k)?;
                for (i, item) in arr.iter().enumerate() {
                    writeln!(out, "{}  - # [{}]", pad, i)?;
                    if let Value::Object(im) = item {
                        render_kv_block(out, im, indent + 4)?;
                    } else {
                        writeln!(out, "{}    {}", pad, stringify_cell(Some(item)))?;
                    }
                }
            }
            Value::Array(arr) => {
                let inline: Vec<String> = arr
                    .iter()
                    .map(|x| stringify_cell(Some(x)))
                    .take(8)
                    .collect();
                let more = if arr.len() > 8 {
                    format!("  …(+{} more)", arr.len() - 8)
                } else {
                    String::new()
                };
                writeln!(
                    out,
                    "{}{:<width$}  [{}]{}",
                    pad,
                    k,
                    inline.join(", "),
                    more,
                    width = key_width
                )?;
            }
            _ => writeln!(
                out,
                "{}{:<width$}  {}",
                pad,
                k,
                stringify_cell(Some(v)),
                width = key_width
            )?,
        }
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
#[allow(dead_code)]
pub fn render_typed<T: Serialize>(value: &T, fmt: Format) -> anyhow::Result<()> {
    let json = serde_json::to_value(value)?;
    render(&json, fmt)
}

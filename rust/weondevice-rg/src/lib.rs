use std::io;
use std::path::Path;
use std::time::Instant;

use globset::Glob;
use grep_regex::RegexMatcherBuilder;
use grep_searcher::sinks::UTF8;
use grep_searcher::{BinaryDetection, SearcherBuilder};
use ignore::overrides::OverrideBuilder;
use ignore::WalkBuilder;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::Serialize;

#[derive(Serialize)]
struct SearchMatch {
    path: String,
    line_number: u64,
    line: String,
    context_before: Vec<String>,
    context_after: Vec<String>,
}

#[derive(Serialize)]
struct SearchReport {
    query: String,
    root: String,
    elapsed_ms: f64,
    files_scanned: usize,
    total_matches: usize,
    matches: Vec<SearchMatch>,
}

#[derive(Serialize)]
struct FileEntry {
    path: String,
    size_bytes: u64,
}

#[pyfunction]
#[pyo3(signature = (
    query,
    root = ".",
    case_insensitive = true,
    max_results = 25,
    context_lines = 2,
    glob_pattern = None,
    max_file_size = 2_000_000,
    literal = false
))]
fn search_json(
    query: &str,
    root: &str,
    case_insensitive: bool,
    max_results: usize,
    context_lines: usize,
    glob_pattern: Option<&str>,
    max_file_size: u64,
    literal: bool,
) -> PyResult<String> {
    let start = Instant::now();
    let root_path = Path::new(root);
    if !root_path.exists() {
        return Err(PyValueError::new_err(format!(
            "Search root does not exist: {}",
            root
        )));
    }

    let mut matcher_builder = RegexMatcherBuilder::new();
    matcher_builder
        .case_insensitive(case_insensitive)
        .fixed_strings(literal);
    let matcher = matcher_builder
        .build(query)
        .map_err(|e| PyValueError::new_err(format!("Invalid pattern: {}", e)))?;

    let mut walk_builder = WalkBuilder::new(root_path);
    walk_builder
        .hidden(true)
        .git_ignore(true)
        .git_global(true)
        .git_exclude(true)
        .max_filesize(Some(max_file_size));

    if let Some(glob_str) = glob_pattern {
        if !glob_str.trim().is_empty() {
            let mut overrides = OverrideBuilder::new(root_path);
            overrides
                .add(glob_str)
                .map_err(|e| PyValueError::new_err(format!("Invalid glob: {}", e)))?;
            let built = overrides
                .build()
                .map_err(|e| PyValueError::new_err(format!("Failed to build glob: {}", e)))?;
            walk_builder.overrides(built);
        }
    }

    let mut searcher = SearcherBuilder::new()
        .binary_detection(BinaryDetection::quit(b'\x00'))
        .line_number(true)
        .build();

    let mut matches: Vec<SearchMatch> = Vec::new();
    let mut files_scanned: usize = 0;

    for result in walk_builder.build() {
        let entry = match result {
            Ok(e) => e,
            Err(_) => continue,
        };
        if !entry.file_type().map(|ft| ft.is_file()).unwrap_or(false) {
            continue;
        }
        files_scanned += 1;

        let path = entry.path();
        let rel_path = path
            .strip_prefix(root_path)
            .unwrap_or(path)
            .to_string_lossy()
            .to_string();

        let mut file_match_lines: Vec<(u64, String)> = Vec::new();
        let search_res = searcher.search_path(
            &matcher,
            path,
            UTF8(|lnum, line| {
                file_match_lines.push((lnum, line.trim_end_matches(&['\r', '\n'][..]).to_string()));
                if matches.len() + file_match_lines.len() >= max_results {
                    Ok(false)
                } else {
                    Ok(true)
                }
            }),
        );

        if search_res.is_err() {
            continue;
        }

        if !file_match_lines.is_empty() {
            let all_lines: Vec<String> = if context_lines > 0 {
                std::fs::read_to_string(path)
                    .map(|s| s.lines().map(|l| l.to_string()).collect())
                    .unwrap_or_default()
            } else {
                Vec::new()
            };

            for (lnum, line_text) in file_match_lines {
                let mut before = Vec::new();
                let mut after = Vec::new();
                if context_lines > 0 && !all_lines.is_empty() {
                    let idx = (lnum as usize).saturating_sub(1);
                    let start_idx = idx.saturating_sub(context_lines);
                    let end_idx = (idx + 1 + context_lines).min(all_lines.len());
                    if start_idx < idx {
                        before = all_lines[start_idx..idx].to_vec();
                    }
                    if idx + 1 < end_idx {
                        after = all_lines[idx + 1..end_idx].to_vec();
                    }
                }
                matches.push(SearchMatch {
                    path: rel_path.clone(),
                    line_number: lnum,
                    line: line_text,
                    context_before: before,
                    context_after: after,
                });
                if matches.len() >= max_results {
                    break;
                }
            }
        }

        if matches.len() >= max_results {
            break;
        }
    }

    let elapsed_ms = start.elapsed().as_secs_f64() * 1000.0;
    let report = SearchReport {
        query: query.to_string(),
        root: root.to_string(),
        elapsed_ms,
        files_scanned,
        total_matches: matches.len(),
        matches,
    };

    serde_json::to_string(&report)
        .map_err(|e| io::Error::new(io::ErrorKind::Other, e).into())
}

#[pyfunction]
#[pyo3(signature = (root = ".", glob_pattern = None, max_files = 500))]
fn list_files_json(root: &str, glob_pattern: Option<&str>, max_files: usize) -> PyResult<String> {
    let root_path = Path::new(root);
    if !root_path.exists() {
        return Err(PyValueError::new_err(format!(
            "Root does not exist: {}",
            root
        )));
    }

    let glob_matcher = match glob_pattern {
        Some(pat) if !pat.trim().is_empty() => Some(
            Glob::new(pat)
                .map_err(|e| PyValueError::new_err(format!("Invalid glob: {}", e)))?
                .compile_matcher(),
        ),
        _ => None,
    };

    let mut walk_builder = WalkBuilder::new(root_path);
    walk_builder
        .hidden(true)
        .git_ignore(true)
        .git_global(true)
        .git_exclude(true);

    let mut entries: Vec<FileEntry> = Vec::new();
    for result in walk_builder.build() {
        let entry = match result {
            Ok(e) => e,
            Err(_) => continue,
        };
        if !entry.file_type().map(|ft| ft.is_file()).unwrap_or(false) {
            continue;
        }
        let path = entry.path();
        let rel_path = path
            .strip_prefix(root_path)
            .unwrap_or(path)
            .to_string_lossy()
            .to_string();

        if let Some(ref gm) = glob_matcher {
            if !gm.is_match(&rel_path) {
                continue;
            }
        }

        let size_bytes = entry.metadata().map(|m| m.len()).unwrap_or(0);
        entries.push(FileEntry {
            path: rel_path,
            size_bytes,
        });
        if entries.len() >= max_files {
            break;
        }
    }

    serde_json::to_string(&entries)
        .map_err(|e| io::Error::new(io::ErrorKind::Other, e).into())
}

#[pymodule]
fn weondevice_rg(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(search_json, m)?)?;
    m.add_function(wrap_pyfunction!(list_files_json, m)?)?;
    Ok(())
}

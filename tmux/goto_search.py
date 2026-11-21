#!/usr/bin/env python3
"""Filter cached picker rows using SQLite FTS5 bm25 ranking and fzf fallback."""
import argparse
import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MUTED_SPLIT_RE = re.compile(r"\x1b\[38;2;[0-9;]+m")
HOME_PREFIX_RE = re.compile(
    r"(?:/Users/[^/\s]+|~)(?:/(?:GitHubRepos|GitlabRepos|Documents/obsidian|Documents))?(?=/[^/\s])"
)
CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
TOKEN_PART_RE = re.compile(r"[^\W_\u4e00-\u9fff\u3400-\u4dbf]+|[\u4e00-\u9fff\u3400-\u4dbf]")
LITERAL_OP_RE = re.compile(r"[!^'$|]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def clean_meta(text: str) -> str:
    return HOME_PREFIX_RE.sub("", text)


def index_tokens(text: str) -> str:
    return " ".join(TOKEN_PART_RE.findall(text.casefold()))


def term_clauses(term: str) -> str | None:
    toks = TOKEN_PART_RE.findall(term.casefold())
    if not toks:
        return None
    suffix = "" if CJK_CHAR_RE.fullmatch(toks[-1]) else "*"
    return f'"{" ".join(toks)}"{suffix}'


def extract_doc_fields(fields: list[str]) -> tuple[str, str, str, str]:
    parts = MUTED_SPLIT_RE.split(fields[2], maxsplit=1)
    wname = strip_ansi(parts[0]).strip()
    summary = strip_ansi(parts[1]).strip() if len(parts) > 1 else ""
    if len(fields) >= 14:
        kw_raw = fields[12].strip()
        kw = "" if kw_raw in ("", "(none)") else clean_meta(strip_ansi(kw_raw))
        col13 = clean_meta(strip_ansi(fields[13]))
        col11 = clean_meta(strip_ansi(fields[11]))
        extra = "" if col11 in ("", "-", col13) else f" {col11}"
        meta = f"{col13}{extra}".strip()
    else:
        kw = ""
        meta = clean_meta(strip_ansi(fields[11])) if len(fields) > 11 else ""
    return wname, kw, summary, meta


def _row_tier(kind: str, display_plain: str, terms_cf: list[str]) -> int:
    if all(term in display_plain for term in terms_cf):
        return 0 if kind == 'W' else 1
    return 2


def _collect_groups(parsed: list[tuple]) -> tuple[dict[str, list[str]], dict[str, int]]:
    groups: dict[str, list[str]] = {}
    wid_order: dict[str, int] = {}
    for idx, entry in enumerate(parsed):
        wid = entry[0][4]
        groups.setdefault(wid, []).append(entry[5])
        wid_order.setdefault(wid, idx)
    return groups, wid_order


def _assemble(ordered_wids: list[str], groups: dict[str, list[str]], matched_ids: set[tuple[str, str]]) -> list[str]:
    return [row for wid in ordered_wids for row in groups[wid]
            if row.startswith('W\t') or tuple(row.split('\t')[:2]) in matched_ids]


def _fzf_fallback(parsed: list[tuple], query: str, terms_cf: list[str],
                  groups: dict[str, list[str]]) -> tuple[list[str], bool]:
    fzf_bin = shutil.which("fzf") or "/opt/homebrew/bin/fzf"
    fzf_input = "\n".join(
        f"{idx}\t{wname}\t{kw}\t{summary}\t{meta}"
        for idx, (_, wname, kw, summary, meta, _) in enumerate(parsed)
    )
    cmd = [fzf_bin, '--literal']
    if LITERAL_OP_RE.search(query):
        cmd.append('--no-extended')
    cmd.extend(['--delimiter=\t', '--nth=2,3,4,5', f'--filter={query}'])
    try:
        result = subprocess.run(cmd, input=fzf_input, text=True, capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return [], True
    if result.returncode not in (0, 1):
        return [], True
    ranked_lines = result.stdout.splitlines()
    if not ranked_lines:
        return [], False
    ranks: dict[str, tuple[int, int]] = {}
    matched_ids: set[tuple[str, str]] = set()
    for rank, line in enumerate(ranked_lines):
        idx = int(line.split('\t', 1)[0])
        fields = parsed[idx][0]
        matched_ids.add((fields[0], fields[1]))
        tier = _row_tier(fields[0], strip_ansi(fields[2]).casefold(), terms_cf)
        score = (tier, rank)
        wid = fields[4]
        if wid not in ranks or score < ranks[wid]:
            ranks[wid] = score
    return _assemble(sorted(ranks, key=ranks.get), groups, matched_ids), False


def filter_rows(source: str, query: str) -> list[str]:
    rows = source.splitlines()
    if not query:
        return rows
    if query.isdecimal():
        windows = {r.split('\t')[4] for r in rows if r.split('\t')[9] == query}
        return [r for r in rows if r.split('\t')[4] in windows]

    parsed = []
    for row in rows:
        fields = row.split('\t')
        wname, kw, summary, meta = extract_doc_fields(fields)
        parsed.append((fields, wname, kw, summary, meta, row))
    groups, wid_order = _collect_groups(parsed)

    terms = query.split()
    terms_cf = [t.casefold() for t in terms]
    special_terms = [t for t in terms_cf if LITERAL_OP_RE.search(t)]
    doc_plains = [f"{wname} {kw} {summary} {meta}".casefold() for _, wname, kw, summary, meta, _ in parsed]
    clauses = [term_clauses(t) for t in terms_cf]

    term_wids: list[dict[str, float]] = []
    term_hit_rids: list[set[tuple[str, str]]] = []

    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE docs USING fts5(wname, kw, summary, meta)")
        con.executemany(
            "INSERT INTO docs(rowid, wname, kw, summary, meta) VALUES (?, ?, ?, ?, ?)",
            [(idx + 1, index_tokens(wname), index_tokens(kw), index_tokens(summary), index_tokens(meta))
             for idx, (_, wname, kw, summary, meta, _) in enumerate(parsed)],
        )

        # Stage 1: Row-level FTS5 BM25 AND + display literal tiers
        if clauses and all(clauses):
            fts_and = " AND ".join(f"({c})" for c in clauses)
            hits = con.execute(
                "SELECT rowid, bm25(docs, 10.0, 8.0, 4.0, 1.5) AS s FROM docs WHERE docs MATCH ? ORDER BY s, rowid",
                (fts_and,),
            ).fetchall()
            ranks: dict[str, tuple[int, float, int]] = {}
            matched_ids: set[tuple[str, str]] = set()
            for rowid, bscore in hits:
                idx = rowid - 1
                if special_terms and not all(t in doc_plains[idx] for t in special_terms):
                    continue
                fields = parsed[idx][0]
                matched_ids.add((fields[0], fields[1]))
                tier = _row_tier(fields[0], strip_ansi(fields[2]).casefold(), terms_cf)
                cand = (tier, bscore, idx)
                wid = fields[4]
                if wid not in ranks or cand < ranks[wid]:
                    ranks[wid] = cand
            if ranks:
                return _assemble(sorted(ranks, key=ranks.get), groups, matched_ids)

        # Stage 2: Cross-(Window + Pane) multi-term BM25 AND (collects per-term hits for Stage 4)
        for ti, clause in enumerate(clauses):
            w_scores: dict[str, float] = {}
            hit_rids: set[tuple[str, str]] = set()
            if clause is not None:
                t_cf = terms_cf[ti]
                require_lit = bool(LITERAL_OP_RE.search(t_cf))
                for rowid, bscore in con.execute(
                    "SELECT rowid, bm25(docs, 10.0, 8.0, 4.0, 1.5) FROM docs WHERE docs MATCH ?",
                    (clause,),
                ):
                    idx = rowid - 1
                    if require_lit and t_cf not in doc_plains[idx]:
                        continue
                    fields = parsed[idx][0]
                    wid = fields[4]
                    hit_rids.add((fields[0], fields[1]))
                    if wid not in w_scores or bscore < w_scores[wid]:
                        w_scores[wid] = bscore
            term_wids.append(w_scores)
            term_hit_rids.append(hit_rids)

        if len(clauses) >= 2 and all(clauses):
            common_wids = set.intersection(*(set(m.keys()) for m in term_wids))
            if common_wids:
                all_hits = set().union(*term_hit_rids)
                w_ranks = {
                    wid: (
                        0 if all(t in win_display for t in terms_cf) else 2,
                        sum(m[wid] for m in term_wids),
                        wid_order[wid],
                    )
                    for wid in common_wids
                    for win_display in [strip_ansi(groups[wid][0].split('\t')[2]).casefold()]
                }
                return _assemble(sorted(w_ranks, key=w_ranks.get), groups, all_hits)
    finally:
        con.close()

    # Stage 3: Cleaned fzf --literal fallback for abbreviations / partial substrings
    fzf_rows, fzf_failed = _fzf_fallback(parsed, query, terms_cf, groups)
    if fzf_rows:
        return fzf_rows

    # Stage 4: Multi-term partial-match BM25 OR fallback
    if len(clauses) >= 2 and any(term_wids):
        candidate_wids = set().union(*(m.keys() for m in term_wids))
        all_hits = set().union(*term_hit_rids)
        partial_ranks: dict[str, tuple[int, float, int]] = {}
        for wid in candidate_wids:
            scores = [m[wid] for m in term_wids if wid in m]
            partial_ranks[wid] = (-len(scores), sum(scores), wid_order[wid])
        return _assemble(sorted(partial_ranks, key=partial_ranks.get), groups, all_hits)

    return rows if fzf_failed else []


def view_source(source: str, metadata: dict, view: str) -> str:
    """Filter whole windows, keeping their pane rows and original sort order."""
    if view == 'all':
        return source
    return '\n'.join(row for row in source.splitlines()
                     if metadata.get(row.split('\t')[4], {}).get(view, False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--window-id', default='')
    parser.add_argument('--query', default='')
    parser.add_argument('--view', choices=('all', 'pinned', 'working', 'completed'))
    parser.add_argument('--cycle-view', action='store_true')
    args = parser.parse_args()
    view_file = args.state_dir/'view'
    if args.view:
        view_file.write_text(args.view)
    view = view_file.read_text().strip() if view_file.exists() else 'all'
    if args.cycle_view:
        views = ('all', 'pinned', 'working', 'completed')
        view = views[(views.index(view) + 1) % len(views)]
        view_file.write_text(view)
    pane_lookup = re.fullmatch(r"%[0-9]+", args.query)
    source = (args.state_dir / ('pane-entries' if pane_lookup else 'entries')).read_text()
    metadata = json.loads((args.state_dir/'window-meta.json').read_text()) if view != 'all' else {}
    source = view_source(source, metadata, view)
    if pane_lookup:
        rows = [row for row in source.splitlines() if row.split('\t', 2)[1] == args.query]
    else:
        rows = filter_rows(source, args.query)
    windows = [(i, r.split('\t')) for i, r in enumerate(rows, 1) if r.startswith('W\t')]
    (args.state_dir/'windows').write_text(''.join(f'{r[9]}\t{i}\n' for i,r in windows))
    (args.state_dir/'start').write_text(str(next((i for i,r in windows if r[4] == args.window_id), 1)))
    if rows:
        print('\n'.join(rows))


if __name__ == '__main__':
    main()

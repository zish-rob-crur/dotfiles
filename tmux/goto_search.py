#!/usr/bin/env python3
"""Filter cached picker rows using full metadata, while displaying only column 3."""
import argparse
import re
import subprocess
from pathlib import Path


def filter_rows(source: str, query: str) -> list[str]:
    rows = source.splitlines()
    if not query:
        return rows
    if query.isdecimal():
        windows = {r.split('\t')[4] for r in rows if r.split('\t')[9] == query}
        return [r for r in rows if r.split('\t')[4] in windows]
    result = subprocess.run(['fzf', '--ansi', '--delimiter=\t', '--nth=3,12', '--filter='+query],
                            input=source, text=True, capture_output=True, timeout=2)
    if result.returncode not in (0, 1):
        return rows  # Keep navigation available if filtering fails.
    # fzf --ansi strips escape sequences from --filter output. Match stable
    # identity fields, never the rendered line (which retains its ANSI colors).
    ranked = [r.split('\t') for r in result.stdout.splitlines()]
    matches = {tuple(fields[:2]) for fields in ranked}
    groups = {}
    for row in rows:
        fields = row.split('\t')
        groups.setdefault(fields[4], []).append(row)
    # Literal words in a window label/project beat incidental subsequence
    # matches across long metadata. Within each tier retain fzf relevance.
    terms = query.casefold().split()
    simple = all(re.fullmatch(r"[\w./-]+", term) for term in terms)
    ranks = {}
    for rank, fields in enumerate(ranked):
        display = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", fields[2]).casefold()
        literal = simple and all(term in display for term in terms)
        tier = 0 if literal and fields[0] == 'W' else 1 if literal else 2
        score = (tier, rank)
        ranks[fields[4]] = min(ranks.get(fields[4], score), score)
    return [row for wid in sorted(ranks, key=ranks.get) for row in groups[wid]
            if row.startswith('W\t') or tuple(row.split('\t')[:2]) in matches]



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--window-id', default='')
    parser.add_argument('--query', default='')
    args = parser.parse_args()
    rows = filter_rows((args.state_dir/'entries').read_text(), args.query)
    windows = [(i, r.split('\t')) for i, r in enumerate(rows, 1) if r.startswith('W\t')]
    (args.state_dir/'windows').write_text(''.join(f'{r[9]}\t{i}\n' for i,r in windows))
    (args.state_dir/'start').write_text(str(next((i for i,r in windows if r[4] == args.window_id), 1)))
    if rows:
        print('\n'.join(rows))


if __name__ == '__main__':
    main()

#!/usr/bin/env bash
set -euo pipefail

idx=${1:-}
if [[ -z "$idx" || ! "$idx" =~ ^[0-9]+$ ]]; then
  printf '%s' "$idx"
  exit 0
fi

# map 1..20 to single-codepoint circled numbers; else map per-digit
case "$idx" in
  1)  printf '①'; exit 0;;  2)  printf '②'; exit 0;;  3)  printf '③'; exit 0;;
  4)  printf '④'; exit 0;;  5)  printf '⑤'; exit 0;;  6)  printf '⑥'; exit 0;;
  7)  printf '⑦'; exit 0;;  8)  printf '⑧'; exit 0;;  9)  printf '⑨'; exit 0;;
  10) printf '⑩'; exit 0;; 11) printf '⑪'; exit 0;; 12) printf '⑫'; exit 0;;
  13) printf '⑬'; exit 0;; 14) printf '⑭'; exit 0;; 15) printf '⑮'; exit 0;;
  16) printf '⑯'; exit 0;; 17) printf '⑰'; exit 0;; 18) printf '⑱'; exit 0;;
  19) printf '⑲'; exit 0;; 20) printf '⑳'; exit 0;;
esac

# per-digit fallback (supports any length)
out=""
for ((i=0; i<${#idx}; i++)); do
  d=${idx:$i:1}
  case "$d" in
    0) out+="⓪";; 1) out+="①";; 2) out+="②";; 3) out+="③";; 4) out+="④";;
    5) out+="⑤";; 6) out+="⑥";; 7) out+="⑦";; 8) out+="⑧";; 9) out+="⑨";;
    *) out+="$d";;
  esac
done
printf '%s' "$out"


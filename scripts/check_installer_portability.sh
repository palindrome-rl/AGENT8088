#!/bin/bash
# Fail if install.sh uses a construct unavailable in bash 3.2 or a GNU-only tool
# flag.
#
# Worth having as a script rather than a habit: stock macOS ships bash 3.2.57 and
# will never ship a newer one, so a bash-4 construct is a macOS-only syntax error
# that never reproduces on a Linux box. The same goes for `sed -i` and
# `readlink -f`, which exist on both platforms with different meanings.
set -e

target="${1:-install.sh}"
[ -f "$target" ] || { echo "no such file: $target" >&2; exit 2; }
fail=0

# Matches are reported with line numbers, minus anything that is only a comment --
# these constructs are routinely *described* in comments explaining why they are
# avoided, and flagging that would make the lint unusable.
check() {
    local pattern="$1" why="$2" hits
    hits="$(grep -nE "$pattern" "$target" | grep -vE '^[0-9]+:[[:space:]]*#' || true)"
    if [ -n "$hits" ]; then
        echo "$hits"
        echo "  ^^ $why"
        fail=1
    fi
}

check 'declare -A|local -A'                 'associative arrays need bash 4'
check '\$\{[A-Za-z_][A-Za-z0-9_]*,,\}'      '${x,,} lowercasing needs bash 4 (use tr)'
check '\$\{[A-Za-z_][A-Za-z0-9_]*\^\^\}'    '${x^^} uppercasing needs bash 4 (use tr)'
check '\bmapfile\b|\breadarray\b'           'mapfile/readarray need bash 4'
check '[^|&]\|&'                            '|& needs bash 4 (use 2>&1 |)'
# `sed -i.bak` (with an explicit suffix) is portable and IS used here: BSD/macOS
# requires a suffix argument and GNU accepts one, so the suffixed form is the
# common subset. Only the bare `sed -i <script>` form is GNU-only.
check 'sed -i[[:space:]]|sed --in-place[[:space:]]' 'bare `sed -i` is GNU-only; BSD/macOS needs a suffix (use sed -i.bak)'
check '\breadlink -f\b'                     'readlink -f is GNU-only; macOS lacks it'
check '\bgrep -P\b'                         'grep -P is GNU-only'
check '\bbase64 -w\b'                       'base64 -w is GNU-only'
check '\bstat -c\b'                         'stat -c is GNU; BSD/macOS uses stat -f'
check '\bdate -d\b'                         'date -d is GNU; BSD/macOS uses date -v'

if [ "$fail" -eq 0 ]; then
    echo "portability OK: $target (bash 3.2 + BSD/macOS safe)"
else
    echo ""
    echo "portability check FAILED for $target" >&2
    exit 1
fi

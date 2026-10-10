#!/usr/bin/env bash
# Git-хук: коммиты делает только владелец проекта.
#   check-author.sh ident        -> автор и коммиттер совпадают с владельцем (этап pre-commit)
#   check-author.sh msg <file>   -> в сообщении нет строк о соавторах и генераторах (этап commit-msg)
set -euo pipefail

OWNER_NAME="evgenybaulin"
OWNER_EMAIL="e.baulin@icloud.com"

fail() {
    echo "check-author: $1" >&2
    exit 1
}

check_ident() {
    local role="$1" ident name email
    ident="$(git var "GIT_${role}_IDENT")"
    name="${ident%% <*}"
    email="${ident#*<}"
    email="${email%%>*}"
    if [[ "$name" != "$OWNER_NAME" || "$email" != "$OWNER_EMAIL" ]]; then
        fail "$(echo "$role" | tr "[:upper:]" "[:lower:]") is '$name <$email>', expected '$OWNER_NAME <$OWNER_EMAIL>'"
    fi
}

case "${1:-}" in
    ident)
        check_ident AUTHOR
        check_ident COMMITTER
        ;;
    msg)
        msg_file="${2:?commit message file is required}"
        if grep -Eiq "^co-authored-by:" "$msg_file"; then
            fail "remove the 'Co-Authored-By' trailer from the commit message"
        fi
        if grep -Eiq "^[^[:alnum:]]*generated with " "$msg_file"; then
            fail "remove the 'Generated with …' line from the commit message"
        fi
        ;;
    *)
        fail "usage: check-author.sh ident | msg <file>"
        ;;
esac

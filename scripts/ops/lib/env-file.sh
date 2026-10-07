#!/usr/bin/env bash
# Reads a dotenv file the way Compose reads it, without ever running a value as shell code.
#
# `set -a; source .env` breaks on an unquoted value with a space, `&`, `;` or `(` and executes
# `$(...)`/backticks inside a value. This reader treats every value as data:
#   - double quotes: the value ends at the closing quote; \" \\ \$ \n are escapes
#   - single quotes: the value ends at the closing quote; only \' is an escape
#   - anything after a closing quote is ignored
#   - unquoted: the value ends at the first " #" (a "#" inside a word stays part of the value)
#   - an unterminated quote or a value that starts with "#" counts as empty
#   - "$" is kept literally (no interpolation); put values containing "$" in single quotes so
#     Compose and this reader agree
#   - the last entry of a variable wins; one entry per line (no multi-line values)

# env_file_parse_value <raw-value> — print the value Compose would see for the text after "=".
env_file_parse_value() {
    local value=$1 out="" rest char next closed=0
    value=${value#"${value%%[![:space:]]*}"}
    case "$value" in
        \"*)
            rest=${value#\"}
            while [ -n "$rest" ]; do
                char=${rest:0:1}
                rest=${rest:1}
                if [ "$char" = '"' ]; then
                    closed=1
                    break
                fi
                if [ "$char" = '\' ] && [ -n "$rest" ]; then
                    next=${rest:0:1}
                    rest=${rest:1}
                    case "$next" in
                        '"' | '\' | '$') out+=$next ;;
                        n) out+=$'\n' ;;
                        *) out+=$char$next ;;
                    esac
                else
                    out+=$char
                fi
            done
            [ "$closed" = 1 ] || out=""
            ;;
        \'*)
            rest=${value#\'}
            while [ -n "$rest" ]; do
                char=${rest:0:1}
                rest=${rest:1}
                if [ "$char" = "'" ]; then
                    closed=1
                    break
                fi
                if [ "$char" = '\' ] && [ "${rest:0:1}" = "'" ]; then
                    out+="'"
                    rest=${rest:1}
                else
                    out+=$char
                fi
            done
            [ "$closed" = 1 ] || out=""
            ;;
        \#*) out="" ;;
        *)
            out=${value%% \#*}
            out=${out%"${out##*[![:space:]]}"}
            ;;
    esac
    printf '%s' "$out"
}

# load_env_file <file> — export every KEY=value entry of the file into the current shell.
load_env_file() {
    local file=$1 line key value
    local entry='^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$'
    while IFS= read -r line || [ -n "$line" ]; do
        [[ "$line" =~ $entry ]] || continue
        key=${BASH_REMATCH[2]}
        value=$(env_file_parse_value "${BASH_REMATCH[3]}")
        printf -v "$key" '%s' "$value" 2>/dev/null || continue # skip read-only names such as UID
        export "${key?}"
    done <"$file"
}

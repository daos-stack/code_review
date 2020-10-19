#!/bin/bash -uex

mydir="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

: "${PROJECT_REPO:=$mydir}"
: "${CODE_REVIEW:=$mydir}"
export PROJECT_REPO
"$CODE_REVIEW/check_json.sh"
"$CODE_REVIEW/check_python.sh"
"$CODE_REVIEW/check_ruby.sh"
"$CODE_REVIEW/check_yaml.sh"
"$CODE_REVIEW/shellcheck_scripts.sh"

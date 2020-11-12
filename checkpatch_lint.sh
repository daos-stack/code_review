#!/bin/bash -uex

mydir="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

: "${PROJECT_REPO:=$mydir}"
: "${CODE_REVIEW:=$mydir}"
export PROJECT_REPO
result=0
if ! "$CODE_REVIEW/check_json.sh"; then
  result=1
fi
if ! "$CODE_REVIEW/check_python.sh"; then
  result=1
fi
if ! "$CODE_REVIEW/check_ruby.sh"; then
  result=1
fi
if ! "$CODE_REVIEW/check_yaml.sh"; then
  result=1
fi
if ! "$CODE_REVIEW/shellcheck_scripts.sh"; then
  result=1
fi
exit $result

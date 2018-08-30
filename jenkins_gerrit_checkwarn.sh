#!/bin/bash -uex

# comma separated list
def_ignore="SPLIT_STRING,SSCANF_TO_KSTRTO,PREFER_KERNEL_TYPES,BRACES"
def_ignore+=",USE_NEGATIVE_ERRNO,CAMELCASE"

: "${IGNORE:="${def_ignore}"}"

mydir="$(dirname "${0}")"
checkpatch_py="$(find "${mydir}" -name gerrit_checkpatch.py -print -quit)"

check_make="$(find "${mydir}" -name check_make_output.sh -print -quit)"
check_style="$(find "${mydir}" -name checkpatch.pl -print -quit)"
check_shell="$(find "${mydir}" -name shellcheck_scripts.sh -print -quit)"
check_python="$(find "${mydir}" -name check_python.sh -print -quit)"

# For a matrix review we only want to default for the full check for the
# el7 target, just compiler warnings for the rest.
set +u
# shellcheck disable=SC2154
if [ -n "${distro}" ]; then
  if [ "${distro}" != 'el7' ]; then
    : "${FULL_REVIEW:=0}"
  fi
fi
: "${FULL_REVIEW:=1}"
set -u

# colon separated list
def_checkpatch_paths="${check_make}"
if [ "${FULL_REVIEW}" -ne 0 ]; then
  def_checkpatch_paths+=":${check_style}:${check_shell}:${check_python}"
fi

: "${CHECKPATCH_PATHS:="${def_checkpatch_paths}"}"
export CHECKPATCH_PATHS

: "${CHECKPATCH_ARGS:="--notree --show-types --ignore $IGNORE -"}"
export CHECKPATCH_ARGS

# Comma separated list
def_ignored_files="code_review/checkpatch.pl"

: "${CHECKPATCH_IGNORED_FILES:="${def_ignored_files}"}"
export CHECKPATCH_IGNORED_FILES

set +u
if [ -n "${CORAL_ARTIFACTS}" ]; then
  REVIEW_HISTORY_BASE="${CORAL_ARTIFACTS}"/"${JOB_NAME}"
fi
set -u
: "${REVIEW_HISTORY_BASE:="${PWD}"}"
export REVIEW_HISTORY_PATH="${REVIEW_HISTORY_BASE}"/REVIEW_HISTORY

if [ ! -e "$REVIEW_HISTORY_PATH" ]; then
  mkdir -p "${REVIEW_HISTORY_BASE}"
  touch "${REVIEW_HISTORY_PATH}"
fi

# CentOS PyOpenSSL out of date, need a virtualenv to use correct one.

# Some prompt variables are usually not set.
set +u
if [ -n "${WORKSPACE}" ];then
  # Need to remove older virtualenv with pip/wheel etc.
  set +e
  grep "\#\!${WORKSPACE}"  -r test_env
  grep_st=$?
  set -e
  if [ "${grep_st}" -eq 0 ]; then
    rm -rf test_env
  fi
fi
if [ ! -e test_env ]; then
  virtualenv --system-site-packages \
    --no-setuptools --no-pip --no-wheel test_env
  # shellcheck disable=SC1091
  source test_env/bin/activate
  pip install -I --root test_env --prefix test_env \
    -U --force-reinstall pyOpenSSL
  pip install -I --root test_env --prefix test_env -U pylint flake8
else
  # shellcheck disable=SC1091
  source test_env/bin/activate
fi
set -u

python "${checkpatch_py}"
result=$?

exit ${result}


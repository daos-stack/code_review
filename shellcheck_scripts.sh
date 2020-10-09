#!/bin/bash

# Input if this in a Jenkins review job.
#   GERRIT_PROJECT and GIT_BRANCH are set by Jenkins
#   PROJECT_REPO is the directory to review.
#      Needs to be set if is not the same directory as GERRIT_PROJECT
#
# If this is a commit hook, it is expected the working directory
# is at the base of the repository checkout.
#

# Only output lines for the files in the review.
# shellcheck disable=SC1090
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"/git_args.sh
read -r -a git_args <<< "$(git_args)"

: "${GERRIT_PROJECT:="."}"
: "${PROJECT_REPO:="${GERRIT_PROJECT}"}"

# Reviews that build typically checkout the project into a directory
# named for the last path in the ${GERRIT_PROJECT} so try a guess.
if [ ! -d "${PROJECT_REPO}" ]; then
  test_dir=${PROJECT_REPO#*/}
  if [ -d "${test_dir}" ]; then
    PROJECT_REPO="${test_dir}"
  else
    echo "Could not find PROJECT_REPO=\"${PROJECT_REPO}\" to check"
    exit 1
  fi
fi

# Follow external references if shellcheck supports it.
external=
if (shellcheck --help | grep "\-\-external") &> /dev/null; then
  external=--external
fi

rc=0
pushd "${PROJECT_REPO}" > /dev/null || exit 1

  if [ -n "$FILELIST" ]; then
    file_list="$FILELIST"
  else
    file_list1=$(git "${git_args[@]}")

    file_list=${file_list1//$'\n'/ }
  fi

  for script_file in ${file_list}; do

    if  [ -f "${script_file}" ] &&
        ( [[ ${script_file} == *.sh ]] ||
      grep -m 1 -E '^#!(/usr)*/bin/.*(bash|sh)' "${script_file}" ); then
      if ! shellcheck ${external} --format=gcc "${script_file}"; then
        (( rc=rc+PIPESTATUS[0] ))
      fi
    fi
  done
popd > /dev/null || exit 1
exit ${rc}

#!/bin/bash

# This is intended to be run in a Jenkins review job.
# Input
#   GERRIT_PROJECT and GIT_BRANCH are set by Jenkins
#   MAKE_OUTPUT is the file with the build output log.
#   PROJECT_REPO is the directory for the source files.
#      Needs to be set if is not the same directory as GERRIT_PROJECT

# shellcheck disable=SC1090
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"/git_args.sh
read -r -a git_args <<< "$(git_args)"

: "${MAKE_OUTPUT:="make_output"}"
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

if [ ! -e "${MAKE_OUTPUT}" ]; then
  # Nothing to do.
  exit 0
fi

# Only output lines for the files in the review.
if [ -n "$FILELIST" ]; then
  file_list="$FILELIST"
else
  pushd "${PROJECT_REPO}" >> /dev/null || exit 1
    file_list1=$(git "${git_args[@]}")
  popd >> /dev/null || exit 1
  file_list=${file_list1//$'\n'/|}
fi
grep -E "${file_list}" "${MAKE_OUTPUT}"

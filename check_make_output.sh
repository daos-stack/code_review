#!/bin/bash

if [ -z "${GERRIT_PROJECT}" ]; then
  # Commit Hook
  git_args=(ls-files --exclude-standard)
else
  # Review job
  git_args=(diff-tree --name-only -r HEAD "origin/${GERRIT_BRANCH}")
fi

: "${MAKE_OUTPUT:="make_output"}"
: "${GERRIT_PROJECT:="."}"
: "${GERRIT_BRANCH:="master"}"

if [ ! -e "${MAKE_OUTPUT}" ]; then
  # Nothing to do.
  exit 0
fi

# Only output lines for the files in the review.
pushd "${GERRIT_PROJECT}" >> /dev/null
  file_list1=$(git "${git_args[@]}")
popd >> /dev/null
file_list=${file_list1//$'\n'/|}
grep -E "${file_list}" "${MAKE_OUTPUT}"


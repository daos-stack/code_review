#!/bin/bash

# Only output lines for the files in the review.

if [ -z "${GERRIT_PROJECT}" ]; then
  # Commit Hook
  git_args=(ls-files --exclude-standard)
else
  # Review job
  git_args=(diff-tree --name-only -r HEAD "origin/${GERRIT_BRANCH}")
fi

: "${GERRIT_PROJECT:="."}"
: "${GERRIT_BRANCH:="master"}"

rc=0
pushd "${GERRIT_PROJECT}" > /dev/null

  file_list1=$(git "${git_args[@]}")

  file_list=${file_list1//$'\n'/ }

  for script_file in ${file_list}; do

    if [[ ${script_file} == *.yml ]]; then
      yamllint -f parsable "${script_file}"
      let rc=rc+$?
    fi
  done
popd > /dev/null
exit ${rc}


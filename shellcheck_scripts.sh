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
set -x
if [ -z "${GIT_BRANCH}" ]; then
  # Commit Hook
  git_args=(ls-files --exclude-standard)
else
  # Review job
  git_args=(diff-tree --name-only -r HEAD HEAD^)
fi

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

rc=0
pushd "${PROJECT_REPO}" > /dev/null

  file_list1=$(git "${git_args[@]}")

  file_list=${file_list1//$'\n'/ }

  for script_file in ${file_list}; do

    if [[ ${script_file} == *.sh ]]; then
      shellcheck --format=gcc "${script_file}"
      let rc=rc+$?
    else
      grep -E '^#!/bin/(bash|sh)' "${script_file}"
      if [ $? -eq 0 ]; then
        shellcheck --format=gcc "${script_file}"
        let rc=rc+$?
      fi
    fi
  done
popd > /dev/null
exit ${rc}


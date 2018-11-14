#!/bin/bash

git_args() {
    # Only output lines for the files in the review.
    if [ -z "${GIT_BRANCH}" ]; then
      # Commit Hook
      echo "ls-files --exclude-standard"
    else
      # Review job
      echo "diff-tree --name-only -r HEAD HEAD^"
    fi
}

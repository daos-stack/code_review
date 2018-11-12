#!/bin/bash

git_args() {
    # Only output lines for the files in the review.
    if [ -z "${GIT_BRANCH}" ]; then
      # Commit Hook
      echo "ls-files --exclude-standard"
    else
      # Review job
      # Github reviews can have many commits on them.  CHANGE_TARGET is
      # the "base"
      if [ -n "$CHANGE_TARGET" ]; then
          CHANGE_TARGET="origin/$CHANGE_TARGET"
      else
          CHANGE_TARGET="HEAD^"
      fi
      echo "diff-tree --name-only -r HEAD $CHANGE_TARGET"
    fi
}

#!/bin/bash -uex

# Bridge script while migrate from gerrit to Github

exec "${0%/*}"/jenkins_github_checkwarn.sh "$@"

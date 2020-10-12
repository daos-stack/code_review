#!/bin/bash

# Input if this in a Jenkins review job.
#   GERRIT_PROJECT and GIT_BRANCH are set by Jenkins
#   PROJECT_REPO is the directory to review.
#      Needs to be set if is not the same directory as GERRIT_PROJECT
#   PYLINT_OUT can be used to set the path for the pylint log unless
#       a project specific check_modules.sh is used.
#
# If this is a commit hook, it is expected the working directory
# is at the base of the repository checkout.
#
# shellcheck disable=SC1090
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"/git_args.sh
read -r -a git_args <<< "$(git_args)"

: "${PYLINT_OUT:="pylint.log"}"

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

repo=${PROJECT_REPO}

# If the project has a check_modules.sh file, use it instead of the
# default checks
project_check_module=""
pushd "${repo}" > /dev/null || exit 1
if [ -e "./check_modules.sh" ]; then
  project_check_module="./check_modules.sh"
else
  if [ -e "utils/check_modules.sh" ]; then
    project_check_module="utils/check_modules.sh"
  else
    project_check_module="$(find . -name check_modules.sh -print -quit)"
  fi
fi
if [ -n "${project_check_module}" ]; then
  # A local check_modules file creates a pylint.log (empty if no
  # issues are found)
  cm_pylint_out="${PWD}/pylint.log"
  rm -f "${cm_pylint_out}"
  # Must suppress issues being written to stdout.
    "${project_check_module}" > check_module.out
    if [ -s "${cm_pylint_out}" ]; then
      grep -E ".+:[[:digit:]]+:.+:.+" "${cm_pylint_out}"
      popd || exit 1
      exit 1
    else
      rm -f "${cm_pylint_out}"
    fi
  popd || exit 1
  exit 0
fi
popd || exit 1

# Default checking
pylint_rc="$(find "${repo}" -name pylint.rc -print -quit)"
pylint3_rc="$(find "${repo}" -name pylint3.rc -print -quit)"

def_python="python"
tox_ini="$(find "${repo}" -name tox.ini -print -quit)"
if [ -e "${tox_ini}" ]; then
  if grep envlist "${tox_ini}" | grep py3; then
    def_python="python3"
  fi
fi

pyl_opts=""
pyl3_opts=""

if [ -n "${pylint_rc}" ]; then
  pyl_opts=" --rcfile=${pylint_rc}"
fi
if [ -n "${pylint3_rc}" ]; then
  pyl3_opts=" --rcfile=${pylint3_rc}"
fi

rc=0
pushd "${PROJECT_REPO}" > /dev/null || exit 1
  if [ -n "$FILELIST" ]; then
    file_list="$FILELIST"
  else
    file_list1=$(git "${git_args[@]}")

    file_list=${file_list1//$'\n'/ }
  fi

  rm -f "${PYLINT_OUT}"

  tmpl="{path}:{line}: pylint-{symbol}: {msg}"

  pylint="$(command -v pylint)"
  if [ -z "${pylint}" ]; then
    echo "pylint not found"
    exit 1
  fi

  for script_file in ${file_list}; do

    IFS=" " read -r -a pylint_cmd <<< "${pylint} ${pyl_opts} ${script_file}"
    IFS=" " read -r -a pylint3_cmd <<< "${pylint} ${pyl3_opts} ${script_file}"
    if [ ! -f "${script_file}" ]; then
        continue
    fi
    if [[ ${script_file} == *.py ]]; then
      # if there is a shebang use it.
      if (head -1 "$script_file" | grep -q -E '^#!(/usr)?/bin/.*python'); then
        if (head -1 "$script_file" | \
            grep -q -E '^#!(/usr)?/bin/.*python3'); then
          if ! python3 "${pylint3_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1; then
            (( rc=rc+PIPESTATUS[0] ))
          fi
        else
          if ! python "${pylint_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1; then
            (( rc=rc+PIPESTATUS[0] ))
          fi
        fi
      else
        if [ "${def_python}" == "python" ]; then
          if ! python "${pylint_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1; then
            (( rc=rc+PIPESTATUS[0] ))
          fi
        else
          if ! python3 "${pylint3_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1; then
            (( rc=rc+PIPESTATUS[0] ))
          fi
        fi
      fi
    else
      if (head -1 "$script_file" | grep -q -E '^#!(/usr)?/bin/.*python'); then
        if (head -1 "$script_file" | \
            grep -q -E '^#!(/usr)?/bin/.*python3'); then
          if ! python3 "${pylint3_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1; then
            (( rc=rc+PIPESTATUS[0] ))
          fi
        else
          if ! python "${pylint_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1; then
            (( rc=rc+PIPESTATUS[0] ))
          fi
        fi
      fi
    fi
  done
  if [ -e "${PYLINT_OUT}" ]; then
    grep ':' "${PYLINT_OUT}"
  fi
popd > /dev/null || exit 1
exit ${rc}

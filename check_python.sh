#!/bin/bash

# Input if this in a Jenkins review job.
#   GERRIT_PROJECT and GERRIT_BRANCH are set by Jenkins
#   PROJECT_REPO is the directory to review.
#      Needs to be set if is not the same directory as GERRIT_PROJECT
#   PYLINT_OUT can be used to set the path for the pylint log unless
#       a project specific check_modules.sh is used.
#
# If this is a commit hook, it is expected the working directory
# is at the base of the repository checkout.
#

if [ -z "${GERRIT_PROJECT}" ]; then
  # Commit Hook
  git_args=(ls-files --exclude-standard)
else
  # Review job
  git_args=(diff-tree --name-only -r HEAD "origin/${GERRIT_BRANCH}")
fi

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
project_check_module="$(find "${repo}" -name check_modules.sh -print -quit)"
if [ -n "${project_check_module}" ]; then
  # A local check_modules file creates a pylint.log if any issues are found
  cm_pylint_out="${GERRIT_PROJECT}/pylint.log"
  rm -f "${cm_pylint_out}"
  pushd "${GERRIT_PROJECT}" > /dev/nul
    ${project_check_module}
  popd
  if [ -e "${cm_pylint_out}" ]; then
    cat "${cm_pylint_out}"
    exit 1
  fi
  exit 0
fi

# Default checking
pylint_rc="$(find "${repo}" -name pylint.rc -print -quit)"
pylint3_rc="$(find "${repo}" -name pylint3.rc -print -quit)"

def_python="python"
tox_ini="$(find "${repo}" -name tox.ini -print -quit)"
if [ -e "${tox_ini}" ]; then
  grep envlist "${tox_ini}" | grep py3
  if [ $? == 0 ]; then
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
pushd "${GERRIT_PROJECT}" > /dev/null
  file_list1=$(git "${git_args[@]}")

  file_list=${file_list1//$'\n'/ }

  rm -f "${PYLINT_OUT}"

  tmpl="{path}:{line}: pylint-{symbol}: {msg}"

  pylint="$(which pylint)"
  if [ -z "${pylint}" ]; then
    echo "pylint not found"
    exit 1
  fi

  for script_file in ${file_list}; do

    pylint_cmd=(${pylint} ${pyl_opts} ${script_file})
    pylint3_cmd=(${pylint} ${pyl3_opts} ${script_file})
    if [[ ${script_file} == *.py ]]; then
      # if there is a shebang use it.
      grep '^#!/bin/.*python' "${script_file}"
      if [ $? -eq 0 ]; then
        grep '^#!/bin/.*python3' "${script_file}"
        if [ $? -eq 0 ]; then
          python3 "${pylint3_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1
          let rc=rc+$?
        else
          python "${pylint_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1
          let rc=rc+$?
        fi
      else
        if [ "${def_python}" == "python" ]; then
          python "${pylint_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1
          let rc=rc+$?
        else
          python3 "${pylint3_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1
          let rc=rc+$?
        fi
      fi
      let rc=rc+$?
    else
      grep '^#!/bin/.*python' "${script_file}"
      if [ $? -eq 0 ]; then
        grep '^#!/bin/.*python3' "${script_file}"
        if [ $? -eq 0 ]; then
          python3 "${pylint3_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1
          let rc=rc+$?
        else
          python "${pylint_cmd[@]}" --msg-template "${tmpl}" >> \
            "${PYLINT_OUT}" 2>&1
          let rc=rc+$?
        fi
      fi
    fi
  done
  if [ -e "${PYLINT_OUT}" ]; then
    grep ':' "${PYLINT_OUT}"
  fi
popd > /dev/null
exit ${rc}


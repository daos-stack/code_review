#!/usr/bin/python3
#
# GPL HEADER START
#
# DO NOT ALTER OR REMOVE COPYRIGHT NOTICES OR THIS FILE HEADER.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 only,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License version 2 for more details (a copy is included
# in the LICENSE file that accompanied this code).
#
# You should have received a copy of the GNU General Public License
# version 2 along with this program; If not, see
# http://www.gnu.org/licenses/gpl-2.0.html
#
# GPL HEADER END
#
# Copyright (c) 2014-2019, Intel Corporation.
#
# Author: Brian J. Murrell <brian.murrell@intel.com>
#   based on gerrit_checkpatch.py
#
"""
Github Checkpatch Reviewer
~~~~~~ ~~~~~~~~~~ ~~~~~~~~

* Run linters on HEAD.
* POST reviews back to github based on checkpatch output.
"""

import fnmatch
import logging
import os
import sys
import subprocess
import re
import ssl
import time
import requests
import github
from github import Github
from github import GithubException

# Monkey-patch in the comfort-fade header.
def pygithub_create_review2(
        self,
        commit=github.GithubObject.NotSet,
        body=github.GithubObject.NotSet,
        event=github.GithubObject.NotSet,
        comments=github.GithubObject.NotSet,
    ):
        """
        :calls: `POST /repos/:owner/:repo/pulls/:number/reviews <https://developer.github.com/v3/pulls/reviews/>`_
        :param commit: github.Commit.Commit
        :param body: string
        :param event: string
        :param comments: list
        :rtype: :class:`github.PullRequestReview.PullRequestReview`
        """
        assert commit is github.GithubObject.NotSet or isinstance(
            commit, github.Commit.Commit
        ), commit
        assert body is github.GithubObject.NotSet or isinstance(body, str), body
        assert event is github.GithubObject.NotSet or isinstance(event, str), event
        assert comments is github.GithubObject.NotSet or isinstance(
            comments, list
        ), comments
        post_parameters = dict()
        if commit is not github.GithubObject.NotSet:
            post_parameters["commit_id"] = commit.sha
        if body is not github.GithubObject.NotSet:
            post_parameters["body"] = body
        post_parameters["event"] = (
            "COMMENT" if event == github.GithubObject.NotSet else event
        )
        if comments is github.GithubObject.NotSet:
            post_parameters["comments"] = []
        else:
            post_parameters["comments"] = comments
        headers, data = self._requester.requestJsonAndCheck(
            "POST", self.url + "/reviews", input=post_parameters,
            headers={"Accept": 'application/vnd.github.comfort-fade-preview+json'},
        )
        return github.PullRequestReview.PullRequestReview(
            self._requester, headers, data, completed=True
        )

#pylint: disable=too-many-branches
#pylint: disable=broad-except

def _getenv_list(key, default=None, sep=':'):
    """
    'PATH' => ['/bin', '/usr/bin', ...]
    """
    value = os.getenv(key)
    if value is None:
        return default
    return value.split(sep)

BUILD_URL = os.getenv('BUILD_URL', None)

CHECKPATCH_ARGS = []

CHECKPATCH_PATHS = _getenv_list('CHECKPATCH_PATHS', ['checkpatch.pl'])
CHECKPATCH_EXTRA_ARGS = os.getenv('CHECKPATCH_ARGS', '--show-types -').split(' ')
CHECKPATCH_ARGS.extend(CHECKPATCH_EXTRA_ARGS)
CHECKPATCH_IGNORED_FILES = _getenv_list('CHECKPATCH_IGNORED_FILES', [
    'lustre/contrib/wireshark/packet-lustre.c',
    'lustre/ptlrpc/wiretest.c',
    'lustre/utils/wiretest.c',
    '*.patch'])
CHECKPATCH_IGNORED_KINDS = _getenv_list('CHECKPATCH_IGNORED_KINDS', [
    'LASSERT',
    'LCONSOLE',
    'LEADING_SPACE'])
STYLE_LINK = os.getenv('STYLE_LINK',
                       'https://wiki.hpdd.intel.com/display/DC/Coding+Rules')

USE_CODE_REVIEW_SCORE = False

# pylint: disable=too-many-locals
# pylint: disable=too-many-statements
def parse_checkpatch_output(out, path_line_comments, warning_count, files):
    """
    Parse string output out of CHECKPATCH into path_line_comments.
    Increment warning_count[0] for each warning.

    path_line_comments is { PATH: { LINE: [COMMENT, ...] }, ... }.
    """
    # pylint: disable=too-many-arguments
    def add_comment(path, line, level, kind, tag, message, in_files):
        """_"""
        if path.startswith("./"):
            path = path[2:]
        logging.debug("add_comment %s %d %s %s '%s'",
                      path, line, level, kind, message)
        if kind in CHECKPATCH_IGNORED_KINDS:
            return

        for pattern in CHECKPATCH_IGNORED_FILES:
            if fnmatch.fnmatch(path, pattern):
                logging.debug("But suppresing due to matching "
                              "CHECKPATCH_IGNORED_FILES")
                return

        path_comments = path_line_comments.setdefault(path, {})
        line_comments = path_comments.setdefault(line, [])
        message_tag = tag
        line_comments.append('(%s) %s' % (message_tag, message))

        if in_files:
            warning_count[0] += 1

    level = None    # 'ERROR', 'WARNING'
    kind = None     # 'CODE_INDENT', 'LEADING_SPACE', ...
    message = None  # 'code indent should use tabs where possible'

    for line in out.splitlines():
        # Checkpatch.pl output:
        # ERROR:CODE_INDENT: code indent should use tabs where possible
        # #404: FILE: lustre/liblustre/dir.c:103:
        # +        op_data.op_hash_offset = hash_x_index(page->index, 0);$
        # make/gcc/shellcheck output:
        # warn_source.c:19:1: warning: control reaches end of non-void
        # bad_yaml.yml:3:1: [error] too many blank lines (1 > 0) (empty-lines)
        # pylint output:
        # module.py:156: pylint-unused-variable: Unused variable 'idx'
        # ruby output:
        # bad_ruby.rb: error: line 2, column 2: undefined method j
        line = line.strip()
        if not line:
            level, kind, message = None, None, None
        elif line[0] == '#':
            # '#404: FILE: lustre/liblustre/dir.c:103:'
            tokens = line.split(':', 5)
            if len(tokens) != 5 or tokens[1] != ' FILE':
                continue

            path = tokens[2].strip()
            line_number_str = tokens[3].strip()
            if not line_number_str.isdigit():
                continue

            line_number = int(line_number_str)

            if path and level and kind and message:
                add_comment(path, line_number, level, kind, 'style', message, path in files)
        elif not line[0].isalpha() and line[0] != '.':
            continue
        else:
            if not level:
                # warn_source.c:19:1: warning: control reaches end of non-void
                # m.py:156: pylint-unused-variable: Unused variable 'idx'
                sections = line.count(': ')
                # Detect pylint output
                path = None
                idx = None
                if sections == 3:
                    kind = 'ruby-lint'
                    code = 'lint'
                    try:
                        parts = line.split(':', 4)
                        path = parts[0]
                        lvl = parts[1].strip().upper()
                        line_no_str = parts[2].split(',')[0].strip()
                        lnumber = line_no_str.split(' ', 1)[1].strip()
                        message = parts[3].strip()
                    except ValueError:
                        pass
                    except IndexError:
                        try:
                            # Extra :<sp> in the message part means this is
                            # actually a GCC/shellcheck mesage
                            path, lnumber, idx, lvl, message = \
                                line.split(':', 4)
                        except ValueError:
                            try:
                                path, lnumber, lvl, message = \
                                    line.split(':', 3)
                            except ValueError:
                                pass
                elif sections == 2:
                    try:
                        path, lnumber, idx, lvl, message = line.split(':', 4)
                    except ValueError:
                        try:
                            path, lnumber, lvl, message = line.split(':', 3)
                        except ValueError:
                            pass
                elif sections == 1:
                    try:
                        path, lnumber, idx, rest_line = line.split(':', 3)
                        lvl, message = rest_line.strip().split(' ', 1)
                    except ValueError:
                        pass
                if path is not None:
                    try:
                        if idx is None:
                            kind = 'pylint'
                            code = lvl.strip()
                        else:
                            kind = 'lint'
                            code = 'lint'
                        message = message.strip()
                        level = lvl.strip('[] ').upper()
                        if lnumber.isdigit() and level and kind:
                            line_number = int(lnumber)
                            add_comment(path, line_number, level,
                                        kind, code, message, path in files)
                            level = None
                            continue
                    except (ValueError, AttributeError):
                        # Fall back to Checkpatch.pl output
                        pass

            # ERROR:CODE_INDENT: code indent should use tabs where possible
            try:
                level, kind, message = line.split(':', 2)
            except ValueError:
                level, kind, message = None, None, None

            if level != 'ERROR' and level != 'WARNING':
                level, kind, message = None, None, None


def review_input_and_score(path_line_comments, warning_count):
    """
    Convert { PATH: { LINE: [COMMENT, ...] }, ... }, [11] to a
    ReviewInput() and score
    """
    review_comments = {}

    for path, line_comments in path_line_comments.items():
        path_comments = []
        for line, comment_list in line_comments.items():
            message = '\n'.join(comment_list)
            path_comments.append({'line': line, 'message': message})
        review_comments[path] = path_comments

    if warning_count[0] > 0:
        score = -1
    else:
        score = +1
    code_review_score = score

    if score < 0:
        return {
            'message': ('Style warning(s) for job %s\nPlease review %s' %
                        (BUILD_URL, STYLE_LINK)),
            'labels': {
                'Code-Review': code_review_score
                },
            'comments': review_comments,
            }, score
    return {}, score

def add_patch_linenos(review_input, patch):
    """
    Add patch relative line numbers to review_input.
    """

    hunknum = None
    filename = None
    new_start_line = None
    src_lineno = None
    patch_lineno = None
    for line in patch.split('\n'):
        if hunknum:
            patch_lineno += 1
        if line.startswith("--- a/"):
            filename = line.rstrip()[6:]
            continue
        if line.startswith("+++ /dev/null"):
            hunknum = 0
            patch_lineno = 0
            continue
        if line.startswith("+++ b/"):
            filename = line.rstrip()[6:]
            hunknum = 0
            patch_lineno = 0
            continue
        if line.startswith("@@ "):
            hunknum += 1
            matches = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*', line)
            if not matches:
                print("error parsing ", line)
                sys.exit(1)
            new_start_line = matches.group(3)
            src_lineno = int(new_start_line) - 1
        if new_start_line:
            if line.startswith(" ") or \
               line.startswith("+"):
                src_lineno += 1
        if line.startswith("+"):
            try:
                for comment in review_input['comments'][filename]:
                    if comment['line'] == src_lineno:
                        comment['in-patch'] = True
                    if 'start_line' in comment:
                        if src_lineno >= comment['start_line'] and src_lineno <= comment['line']:
                            comment['in-patch'] = True
            except KeyError:
                pass
        # to debug line mapping
        #print("{} {} {} {}".format(patch_lineno, filename, src_lineno, line))

class NotPullRequest(Exception):
    ''' An exception to signal that we are not in a PR'''
    pass

class Reviewer(object):
    """
    * Pipe changeset through checkpatch.
    * Convert checkpatch output to ReviewInput().
    * Post ReviewInput() to Github instance.

    """
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.project, self.repo = os.environ['GIT_URL'].split('/')[-2:]
        self.repo = self.repo[0:-4]
        # https://github.com/PyGithub/PyGithub/issues/693
        # effectively, GH puts a timeout of 10s on API request processing but
        # pygithub's default timeout is also 10s so pygithub can close the
        # socket before GitHub has had a chance to send a 502 response
        gh_context = Github(os.environ['GH_USER'], os.environ['GH_PASS'],
                            timeout=15)
        repo = gh_context.get_repo("{0}/{1}".format(self.project, self.repo))
        try:
            self.pull_request = repo.get_pull(int(os.environ['CHANGE_ID']))
            if not hasattr(self.pull_request, 'create_review2'):
                self.pull_request.create_review2 = pygithub_create_review2.__get__(self.pull_request)

        except KeyError:
            raise NotPullRequest
        self.commits = self.pull_request.get_commits()
        self.patch = None
        self.patch_files = set()

    def _debug(self, msg, *args):
        """_"""
        self.logger.debug(msg, *args)

    def _error(self, msg, *args):
        """_"""
        self.logger.error(msg, *args)

    def create_github_review(self, review_input, commit_sha, max_annotations=31):
        """_"""
        comments = []
        extra_annotations = ""
        extra_review_comment = ""

        # I don't trust review_input['labels']['Code-Review'] at this point
        # Since we have all of the data we need to determine score and are
        # goint to iterate through it right now, figure it out here
        score = 1
        try:
            num_annotations = 0
            comments = []
            for path in review_input['comments']:
                for comment in review_input['comments'][path]:
                    if path not in review_input['files']:
                        continue
                    if comment.get('in-patch', False):
                        if num_annotations < max_annotations:
                            github_comment = {'path': path, 'body': comment['message']}
                            for key in ('line', 'start_line', 'side', 'start_side'):
                                if key in comment:
                                    github_comment[key] = comment[key]

                            comments.append(github_comment)
                            num_annotations += 1
                        else:
                            if comment.get('include_in_extra', True):
                                extra_annotations += "\n[{0}:{1}](https://github.com/{4}" \
                                                     "/{5}/blob/{3}/{0}#L{1}):\n{2}\n".format(
                                                         path, comment['line'], comment['message'],
                                                         os.environ['GIT_COMMIT'], self.project,
                                                         self.repo)
                        score = -1
                    elif comment.get('include_in_extra', True):
                        extra_review_comment += "\n[{0}:{1}](https://github.com/{4}" \
                                                "/{5}/blob/{3}/{0}#L{1}):\n{2}\n".format(
                                                    path, comment['line'], comment['message'],
                                                    commit_sha, self.project, self.repo)
        except KeyError:
            pass

        try:
            review_comment = review_input['message']
        except KeyError:
            review_comment = ""

        if score < 0:
            event = "REQUEST_CHANGES"
        else:
            event = "COMMENT"
            review_comment = "LGTM.  No errors found by checkpatch."

        if extra_annotations != "":
            if review_comment != "":
                review_comment += "\n\n"
            review_comment += "Note: Error annotation limited to the " + \
                              "first " + str(max_annotations) + \
                              " errors.  Remaining unannotated errors:\n" + \
                              extra_annotations

        if extra_review_comment != "":
            if review_comment != "":
                review_comment += "\n\n"
            review_comment += "FYI: Errors found in lines "\
                              "not modified in the patch:\n" + \
                              extra_review_comment

        return score, event, comments, review_comment

    # pylint: disable=too-many-return-statements
    def post_review(self, review_input):
        """
        POST review_input for the given revision of change.
        """

        commit = None
        for commit in self.commits:
            if commit.sha == os.environ['GIT_COMMIT']:
                break
            commit = None

        if not commit:
            print("Couldn't find commit {} in:".format(os.environ['GIT_COMMIT']))
            for commit in self.commits:
                print(commit.sha)
            print("Environment:")
            for k in sorted(os.environ.keys()):
                print("%s=%s" % (k, os.environ[k]))
            sys.exit(1)

        score, event, comments, review_comment = \
            self.create_github_review(review_input, commit.sha)

        # only post if running in Jenkins
        if 'JENKINS_URL' in os.environ and \
            os.environ.get('DISPLAY_RESULTS', 'false') == 'false':
            # Github has a comment size limit of 64K, so truncate
            # we could post multiple comments but at a point where there
            # 64K of comment, more is probably useless anyway
            if len(review_comment) > 64*1024:
                review_comment = review_comment[0:64*1024-80] +           \
                                 "\n\nThere are more review comments but " \
                                 "review comment truncated to 64K."
            # dismiss any previous reviews as they could have been requesting
            # changes and this one could just be a comment (nothing wrong)
            for review in self.pull_request.get_reviews():
                if review.user and review.user.name and \
                   review.user.name.startswith(os.environ['GH_USER']) and \
                   review.state == "CHANGES_REQUESTED":
                    review.dismiss("Updated patch")

            tries = 0
            max_tries = 4
            force_comment = False
            while tries < max_tries:
                tries += 1
                try:
                    self._debug("Creating review on try %s" % tries)
                    if tries == max_tries -1:
                        # on the last try remove all of the annotations to see
                        # if it will post
                        score, event, comments, review_comment = \
                            self.create_github_review(review_input, commit.sha, 0)

                        review_comment += "\n\nNote: Unable to provide any " \
                                          "annotated comments due to GitHub " \
                                          "API limitations."

                    if force_comment:
                        event = 'COMMENT'

                    res = self.pull_request.create_review2(commit,
                                                           review_comment,
                                                           event=event,
                                                           comments=comments)
                    self._debug("Creating review on try %s complete: %s" % \
                                (tries, res))
                    print("Successfully posted review after %s tries: %s " % \
                          (tries, res))
                    return score
                except ssl.SSLError as excpn:
                    self._debug("Creating review on try %s got an SSLError" % tries)
                    if excpn.message == 'The read operation timed out':
                        continue
                    print(excpn)
                    raise
                except GithubException as excpn:
                    self._debug("Creating review on try %s got a GithubException" % tries)
                    if excpn.status == 422:
                        if excpn.data['errors'][0] == 'Path is invalid':
                            print("Tried to sumbit patch comments with a path " \
                                  "that is not in the patch.  Please raise a "\
                                  "ticket about this.")
                            print("Annotation data:")
                            import pprint
                            pprint.PrettyPrinter(indent=4).pprint(comments)
                            return score
                        elif excpn.data['errors'][0] == 'Position is invalid':
                            print("Error parsing the patch and mapping to lines " \
                                  "of code for annotation.  Please raise a "\
                                  "ticket about this.")
                            print("Annotation data:")
                            import pprint
                            pprint.PrettyPrinter(indent=4).pprint(comments)
                            return score
                        elif excpn.data['errors'][0] == 'was submitted too quickly':
                            # rate-limited
                            #import pprint
                            self._debug("Attempt to post was rate-limited")
                            if tries < max_tries + 1:
                                self._debug("Trying again in 60 seconds")
                                time.sleep(60)
                                self._debug("Done sleeping 422")
                            else:
                                self._debug("commit.sha: %s" % commit.sha)
                                self._debug("review_comment: %s" % review_comment)
                                self._debug("event: %s" % event)
                                self._debug("comments:")
                                #pprint.PrettyPrinter(indent=4).pprint(comments)
                                self._debug("Attempt to post was rate-limited. " \
                                            "See data above.")
                                return score
                        elif excpn.data['errors'][0] == 'Can not request changes on your own pull request':
                            force_comment = True
                        elif excpn.data['errors'][0] == 'Start line must be part of the same hunk as the line.':
                            print("exception: %s" % excpn)
                            import pprint
                            pprint.PrettyPrinter(indent=4).pprint(comments)
                            return score
                        else:
                            print("Unhandled 422 exception:")
                            print("exception: %s" % excpn)
                            print("exception.status: %s" % excpn.status)
                            print("exception.data: %s" % excpn.data)
                            return score
                    elif excpn.status == 502:
                        if excpn.data['message'] == 'Server Error':
                            self._debug("Got a 502 Server Error trying to post " \
                                        "review.  Probably exceeded the 10s API " \
                                        "time limit.  Will try again.")
                            time.sleep(5)
                            self._debug("Done sleeping 502")
                        else:
                            print("Unhandled 502 exception:")
                            print("exception: %s" % excpn)
                            print("exception.status: %s" % excpn.status)
                            print("exception.data: %s" % excpn.data)
                            return score
                    else:
                        raise
                self._debug("Bottom of while loop")
            self._debug("Exited while loop")
            print("Gave up trying to post the review after %s tries" % tries)
            return score
        else:
            import pprint
            pprinter = pprint.PrettyPrinter(indent=4)
            print("commit: ", commit)
            print("review_comment:\n", review_comment)
            print("event:", event)
            print("comments (%s):\n" % len(comments))
            pprinter.pprint(comments)

        return score

    def check_patch(self, patch, files):
        """
        Run each script in CHECKPATCH_PATHS on patch, return a
        ReviewInput() and score.
        """
        path_line_comments = {}
        warning_count = [0]
        my_env = os.environ
        my_env['FILELIST'] = ' '.join(files)
        self._debug("checking files: %s" % my_env['FILELIST'])

        for path in CHECKPATCH_PATHS:
            try:
                pipe = subprocess.Popen([path] + CHECKPATCH_ARGS,
                                        stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        env=my_env)
            except OSError as exception:
                if exception.errno == 2:
                    print("Could not find {0}".format(path))
                    sys.exit(1)

            out, err = pipe.communicate(patch.encode('utf-8'))
            self._debug("check_patch: path = %s %s, out = '%s...', err = '%s...'",
                        path, CHECKPATCH_ARGS, out[:80], err[:80])
            parse_checkpatch_output(out.decode('utf-8'), path_line_comments, warning_count, files)

        return review_input_and_score(path_line_comments, warning_count)

    def pull_patch(self):
        if self.patch:
            return self.patch
        try:
            if 'PATCHFILE' in os.environ:
                self._debug("Using patch in file %s" % os.environ['PATCHFILE'])
                self.patch = open(os.environ['PATCHFILE']).read()
            else:
                # I am sure there has got to be a way to arrive at this
                # patch from the local repo, ignoring merge commits, etc.
                ##cmd = ["git", "diff", "{}..{}".format(self.commits[0].sha,
                #                                      #self.commits[0].parents[0].sha)]
                # this is pretty much what we want *except* we need to know
                # know the name of the remote that the base.ref is in which
                # makes it pretty unportable
                # maybe we can revisit this all when we refactor for a git hook
                #cmd = ["git", "diff", "origin/{}...HEAD".format(
                #    self.pull_request.base.ref), '--stat']
                #print cmd
                #patch = subprocess.check_output(cmd)
                # so for now, just use this simple (but lazy and inefficient)
                # method
                session = requests.Session()
                url = "https://github.com/{}/{}/pull/{}.diff".format(self.project,
                                                                     self.repo,
                                                                     self.pull_request.number)
                resp = session.get(url)
                self.patch = resp.text
        except subprocess.CalledProcessError as excpn:
            if excpn.returncode == 128:
                print("""Got error 128 trying to run git diff.
Was there a race with getting the base from the pull request?
I.e. was a new revision of the patch pushed before we could get
the pull request data on the previous one?""")
            raise
        for line in self.patch.splitlines():
            if line.startswith("--- a/") or \
               line.startswith("+++ b/"):
                self.patch_files.add(line.rstrip()[6:])
        return self.patch

    def review_change(self):
        """
        Review the current patch on HEAD
        * Pipe the patch through checkpatch(es).
        * POST review to github.
        """
        score = 1
        patch = self.pull_patch()
        if not patch:
            self._debug("review_change: no patch")
            return score

        review_input, score = self.check_patch(patch, self.patch_files)
        review_input['files'] = self.patch_files
        self._debug("review_change: score = %d", score)

        self.run_from_diff(review_input)

        # add patch line numbers to review_input
        add_patch_linenos(review_input, patch)

        score = self.post_review(review_input)
        return score

    def update_single_change(self):
        """_"""

        return self.review_change()

    def run_from_diff(self, review_input):
        """Update review_input with new comments based on current
        contents of source tree
        """

        def create_comment(header, patch_segment):
            """returns a comment"""
            elems = header.split(' ')
            lineparts = elems[1].split(',')
            lineno = int(lineparts[0]) * -1

            in_patch = False
            add_count = 0
            remove_count = 0
            new_text = []
            append_text = []
            start_line = -1
            end_line = -1
            comment = {'include_in_extra': False}

            for line in patch_segment:
                if line.startswith('+'):
                    add_count += 1
                    if append_text:
                        new_text.extend(append_text)
                        lineno += len(append_text)
                        append_text = []
                    if not in_patch:
                        in_patch = True
                        start_line = lineno
                        append_text = []
                    new_text.append(line[1:])
                    if end_line < lineno:
                        end_line = lineno
                    lineno += 1
                elif line.startswith('-'):
                    remove_count += 1
                    if not in_patch:
                        lineno += len(append_text)
                        append_text = []
                        in_patch = True
                        start_line = lineno
                else:
                    append_text.append(line[1:])
            comment['message'] = '```suggestion\n{}\n```'.format('\n'.join(new_text))
            if start_line == end_line:
                comment['line'] = start_line
            else:
                comment['start_line'] = start_line
                comment['line'] = end_line

            if remove_count == 1 and add_count == 0:
                comment['side'] = 'LEFT'
            else:
                comment['side'] = 'RIGHT'
            if 'start_line' in comment:
                comment['start_side'] = comment['side']
            return comment

        cmd=['git', 'diff', '-U1']

        pipe = subprocess.Popen(cmd,
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

        out, err = pipe.communicate()

        skip_prefix = ['diff', 'index', '+++ b/']

        filename = None
        parts = []
        lineno = None

        if 'comments' not in review_input:
            review_input['comments'] = {}

        patch_segment = []
        header = None
        for line in out.decode('utf-8').splitlines():
            skip = False
            for prefix in skip_prefix:
                if line.startswith(prefix):
                    skip = True
                    continue
            if skip:
                continue
            if line.startswith('--- a/'):
                if patch_segment:
                    new_comment = create_comment(header, patch_segment)
                    if filename not in review_input['comments']:
                        review_input['comments'][filename] = [new_comment]
                    else:
                        review_input['comments'][filename].append(new_comment)
                patch_segment = []
                header = line

                _, filename = line.split('/', 1)

            elif line.startswith('@@ '):
                if patch_segment:
                    new_comment = create_comment(header, patch_segment)
                    if filename not in review_input['comments']:
                        review_input['comments'][filename] = [new_comment]
                    else:
                        review_input['comments'][filename].append(new_comment)
                patch_segment = []
                header = line
            elif line.startswith('-'):
                patch_segment.append(line)
            elif line.startswith('+'):
                patch_segment.append(line)
            else:
                patch_segment.append(line)

        if patch_segment:
            new_comment = create_comment(header, patch_segment)
            if filename not in review_input['comments']:
                review_input['comments'][filename] = [new_comment]
            else:
                review_input['comments'][filename].append(new_comment)

def main():
    """_"""
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.DEBUG)

    try:
        reviewer = Reviewer()
    except NotPullRequest:
        sys.exit(0)

    if False:
        review_comments = {}
        reviewer.run_from_diff(review_comments)
        # add patch line numbers to review_input
        add_patch_linenos(review_comments, reviewer.pull_patch())

        import pprint
        pprint.PrettyPrinter(indent=4).pprint(review_comments)
        return

    score = reviewer.update_single_change()
    if score > 0:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()

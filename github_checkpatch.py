#!/usr/bin/env python
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
# Copyright (c) 2014-2018, Intel Corporation.
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
import requests
from github import Github
from github import GithubException

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


CHECKPATCH_PATHS = _getenv_list('CHECKPATCH_PATHS', ['checkpatch.pl'])
CHECKPATCH_ARGS = os.getenv('CHECKPATCH_ARGS', '--show-types -').split(' ')
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
                return

        path_comments = path_line_comments.setdefault(path, {})
        line_comments = path_comments.setdefault(line, [])
        message_tag = tag
        line_comments.append('(%s) %s\n' % (message_tag, message))

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

    for path, line_comments in path_line_comments.iteritems():
        path_comments = []
        for line, comment_list in line_comments.iteritems():
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
        if line.startswith("+++ /dev/null"):
            hunknum = 0
            patch_lineno = 0
        if line.startswith("+++ b/"):
            filename = line.rstrip()[6:]
            hunknum = 0
            patch_lineno = 0
        if line.startswith("@@ "):
            hunknum += 1
            matches = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*', line)
            if not matches:
                print "error parsing ", line
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
                        comment['patch-line'] = patch_lineno
            except KeyError:
                pass
        # to debug line mapping
        #print "{} {} {} {}".format(patch_lineno, filename, src_lineno, line)

class NotPullRequest(Exception):
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
        gh_context = Github(os.environ['GH_USER'], os.environ['GH_PASS'])
        repo = gh_context.get_repo("{0}/{1}".format(self.project, self.repo))
        try:
            self.pull_request = repo.get_pull(int(os.environ['CHANGE_ID']))
        except KeyError:
            raise NotPullRequest
        self.commits = self.pull_request.get_commits()

    def _debug(self, msg, *args):
        """_"""
        self.logger.debug(msg, *args)

    def _error(self, msg, *args):
        """_"""
        self.logger.error(msg, *args)

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
            print "Couldn't find commit {} in:".format(os.environ['GIT_COMMIT'])
            for commit in self.commits:
                print commit.sha
            print "Environment:"
            for k in sorted(os.environ.keys()):
                print "%s=%s" % (k, os.environ[k])
            sys.exit(1)

        comments = []
        extra_annotations = ""
        extra_review_comment = ""

        try:
            num_annotations = 0
            comments = []
            for path in review_input['comments']:
                for comment in review_input['comments'][path]:
                    try:
                        if num_annotations < 31:
                            comments.append({
                                "path": path,
                                "position": comment['patch-line'],
                                "body": comment['message']
                            })
                        else:
                            extra_annotations += "[{0}:{1}](https://github.com/{4}" \
                                                "/{5}/blob/{3}/{0}#L{1}): {2}".format(
                                                    path, comment['line'], comment['message'],
                                                    os.environ['GIT_COMMIT'], self.project,
                                                    self.repo)
                        num_annotations += 1
                    except KeyError:
                        if path in review_input['files']:
                            # not a line modified in the patch, add it to the
                            # general message
                            extra_review_comment += "[{0}:{1}](https://github.com/{4}" \
                                                    "/{5}/blob/{3}/{0}#L{1}): {2}".format(
                                                        path, comment['line'], comment['message'],
                                                        commit.sha, self.project, self.repo)
        except KeyError:
            pass

        try:
            review_comment = review_input['message']
        except KeyError:
            review_comment = ""

        try:
            if review_input['labels']['Code-Review'] < 0:
                event = "REQUEST_CHANGES"
            else:
                event = "COMMENT"
                if review_comment == "":
                    review_comment = "LGTM.  No errors found by checkpatch."
        except KeyError:
            event = "COMMENT"
            if review_comment == "":
                review_comment = "LGTM.  No errors found by checkpatch."

        if extra_annotations != "":
            if review_comment != "":
                review_comment += "\n\n"
            review_comment += "Note: Error annotation limited to the first 30 "\
                              "errors.  Remaining unannotated errors:\n" + \
                              extra_annotations

        if extra_review_comment != "":
            if review_comment != "":
                review_comment += "\n\n"
            review_comment += "FYI: Errors found in lines "\
                              "not modified in the patch:\n" + \
                              extra_review_comment

        # only post if running in Jenkins
        if 'JENKINS_URL' in os.environ and \
            os.environ.get('DISPLAY_RESULTS', 'false') == 'false':
            try:
                res = self.pull_request.create_review(
                    commit,
                    review_comment,
                    event=event,
                    comments=comments)
                print res
            except GithubException as excpn:
                if excpn.status == 422:
                    # rate-limited
                    print "Attempt to post reivew was rate-limited"
                    print "commit.sha: %s" % commit.sha
                    print "review_comment: %s" % review_comment
                    print "event: %s" % event
                    print "comments: %s" % comments
                    # intentionally falling out to the raise below
                raise

        else:
            import pprint
            pprinter = pprint.PrettyPrinter(indent=4)
            print "commit: ", commit
            print "review_comment:\n", review_comment
            print "event:", event
            print "comments (%s):\n" % len(comments)
            pprinter.pprint(comments)

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
                    print "Could not find {0}".format(path)
                    sys.exit(1)

            out, err = pipe.communicate(patch.encode('utf-8'))
            self._debug("check_patch: path = %s %s, out = '%s...', err = '%s...'",
                        path, CHECKPATCH_ARGS, out[:80], err[:80])
            parse_checkpatch_output(out, path_line_comments, warning_count, files)

        return review_input_and_score(path_line_comments, warning_count)

    def review_change(self):
        """
        Review the current patch on HEAD
        * Pipe the patch through checkpatch(es).
        * POST review to github.
        """
        score = 1
        try:
            if 'PATCHFILE' in os.environ:
                self._debug("Using patch in file %s" % os.environ['PATCHFILE'])
                patch = open(os.environ['PATCHFILE']).read()
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
                patch = resp.text
        except subprocess.CalledProcessError as excpn:
            if excpn.returncode == 128:
                print """Got error 128 trying to run git diff.
Was there a race with getting the base from the pull request?
I.e. was a new revision of the patch pushed before we could get
the pull request data on the previous one?"""
            raise

        if not patch:
            self._debug("review_change: no patch")
            return score

        files = set()
        for line in patch.split('\n'):
            if line.startswith("--- a/") or \
               line.startswith("+++ b/"):
                filename = line.rstrip()[6:]
                files.add(filename)

        review_input, score = self.check_patch(patch, files)
        review_input['files'] = files
        self._debug("review_change: score = %d", score)

        # add patch line numbers to review_input
        add_patch_linenos(review_input, patch)

        self.post_review(review_input)
        return score

    def update_single_change(self):
        """_"""
        score = 1
        score = self.review_change()
        return score

def main():
    """_"""
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.DEBUG)

    try:
        reviewer = Reviewer()
    except NotPullRequest:
        sys.exit(0)

    score = reviewer.update_single_change()
    if score > 0:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()

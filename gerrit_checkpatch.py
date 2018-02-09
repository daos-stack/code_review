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
# Copyright (c) 2014, Intel Corporation.
#
# Author: John L. Hammond <john.hammond@intel.com>
#
# Modified to support pylint, gcc, and shellcheck warnings.
"""
Gerrit Checkpatch Reviewer Daemon
~~~~~~ ~~~~~~~~~~ ~~~~~~~~ ~~~~~~

* Watch for new change revisions in a gerrit instance.
* Pass new revisions through checkpatch script.
* POST reviews back to gerrit based on checkpatch output.
"""

import base64
import fnmatch
import logging
import json
import os
import sys
import subprocess
import requests

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

GERRIT_HOST = os.getenv('GERRIT_HOST', 'review.whamcloud.com')
GERRIT_AUTH_PATH = os.getenv('GERRIT_AUTH_PATH', 'GERRIT_AUTH')
GERRIT_USERNAME = os.getenv("GERRIT_USERNAME", None)
GERRIT_HTTP_TOKEN = os.getenv("GERRIT_HTTP_TOKEN", None)
GERRIT_CHANGE_NUMBER = os.getenv('GERRIT_CHANGE_NUMBER', None)
GERRIT_INSECURE = os.getenv('GERRIT_INSECURE', None)

# GERRIT_AUTH should contain a single JSON dictionary of the form:
# {
#     "review.example.com": {
#         "gerrit/http": {
#             "username": "example-checkpatch",
#             "password": "1234"
#         }
#     }
#     ...
# }

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
                       'http://wiki.lustre.org/Lustre_Coding_Style_Guidelines')

USE_CODE_REVIEW_SCORE = False

# pylint: disable=too-many-locals
# pylint: disable=too-many-statements
def parse_checkpatch_output(out, path_line_comments, warning_count):
    """
    Parse string output out of CHECKPATCH into path_line_comments.
    Increment warning_count[0] for each warning.

    path_line_comments is { PATH: { LINE: [COMMENT, ...] }, ... }.
    """
    # pylint: disable=too-many-arguments
    def add_comment(path, line, level, kind, tag, message):
        """_"""
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
                add_comment(path, line_number, level, kind, 'style', message)
        elif not line[0].isalpha():
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
                                        kind, code, message)
                            level = None
                            continue
                    except ValueError:
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
    Convert { PATH: { LINE: [COMMENT, ...] }, ... }, [11] to a gerrit
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
    # removing this code. it does not look to do anything
    # if you don't want check patch to post to the reviewers,
    # need to remove 'labels'
    #if not USE_CODE_REVIEW_SCORE:
    #    code_review_score = 0

    if score < 0:
        return {
            'message': ('%d style warning(s).\nFor more details please see %s'
                        % (warning_count[0], STYLE_LINK)),
            'labels': {
                'Code-Review': code_review_score
                },
            'comments': review_comments,
            'notify': 'OWNER',
            }, score
    return {
        'message': 'No errors found in files by check patch',
        'notify': 'NONE',
        }, score

class Reviewer(object):
    """
    * Pipe changeset through checkpatch.
    * Convert checkpatch output to gerrit ReviewInput().
    * Post ReviewInput() to gerrit instance.

    """
    def __init__(self, host, username, password):
        self.host = host
        self.auth = requests.auth.HTTPDigestAuth(username, password)
        self.logger = logging.getLogger(__name__)
        self.post_enabled = True
        self.request_timeout = 60

    def _debug(self, msg, *args):
        """_"""
        self.logger.debug(msg, *args)

    def _error(self, msg, *args):
        """_"""
        self.logger.error(msg, *args)

    def _url(self, path):
        """_"""
        if GERRIT_INSECURE is not None:
            return 'http://' + self.host + '/a' + path
        return 'https://' + self.host + '/a' + path

    def _get(self, path):
        """
        GET path return Response.
        """
        url = self._url(path)
        try:
            res = requests.get(url, auth=self.auth,
                               timeout=self.request_timeout)
        except Exception as exc:
            self._error("cannot GET '%s': exception = %s", url, str(exc))
            return None

        # pylint: disable=no-member
        if res.status_code != requests.codes.ok:
            self._error("cannot GET '%s': reason = %s, status_code = %d",
                        url, res.reason, res.status_code)
            return None

        return res

    def _post(self, path, obj):
        """
        POST json(obj) to path, return True on success.
        """
        url = self._url(path)
        data = json.dumps(obj)
        if not self.post_enabled:
            self._debug("_post: disabled: url = '%s', data = '%s'", url, data)
            return False

        try:
            res = requests.post(url, data=data,
                                headers={'Content-Type': 'application/json'},
                                auth=self.auth, timeout=self.request_timeout)
        except Exception as exc:
            self._error("cannot POST '%s': exception = %s", url, str(exc))
            return False

        # pylint: disable=no-member
        if res.status_code != requests.codes.ok:
            self._error("cannot POST '%s': reason = %s, status_code = %d",
                        url, res.reason, res.status_code)
            return False

        return True

    def get_changes(self, query):
        """
        GET a list of ChangeInfo()s for all changes matching query.

        {'status':'open', '-age':'60m'} =>
          GET /changes/?q=project:...+status:open+-age:60m&o=CURRENT_REVISION
            => [ChangeInfo()...]
        """
        query = dict(query)
        # pylint: disable=no-member
        path = ('/changes/?q=' +
                '+'.join(k + ':' + v for k, v in query.iteritems()) +
                '&o=CURRENT_REVISION')
        res = self._get(path)
        if not res:
            return []

        # Gerrit uses " )]}'" to guard against XSSI.
        return json.loads(res.content[5:])

    def decode_patch(self, content):
        """
        Decode gerrit's idea of base64.

        The base64 encoded patch returned by gerrit isn't always
        padded correctly according to b64decode. Don't know why. Work
        around this by appending more '=' characters or truncating the
        content until it decodes. But do try the unmodified content
        first.
        """
        for i in (0, 1, 2, 3, -1, -2, -3):
            if i >= 0:
                padded_content = content + (i * '=')
            else:
                padded_content = content[:i]

            try:
                return base64.b64decode(padded_content)
            except TypeError as exc:
                self._debug("decode_patch: len = %d, exception = %s",
                            len(padded_content), str(exc))

    def get_patch(self, change, revision='current'):
        """
        GET and decode the (current) patch for change.
        """
        path = '/changes/' + change['id'] + '/revisions/' + revision + '/patch'
        self._debug("get_patch: path = '%s'", path)
        res = self._get(path)
        if not res:
            return ''

        self._debug("get_patch: len(content) = %d, content = '%s...'",
                    len(res.content), res.content[:20])

        return self.decode_patch(res.content)

    def post_review(self, change, revision, review_input):
        """
        POST review_input for the given revision of change.
        """
        path = '/changes/' + change['id'] + '/revisions/' + \
               revision + '/review'
        self._debug("post_review: path = '%s'", path)
        return self._post(path, review_input)

    def check_patch(self, patch):
        """
        Run each script in CHECKPATCH_PATHS on patch, return a
        ReviewInput() and score.
        """
        path_line_comments = {}
        warning_count = [0]

        for path in CHECKPATCH_PATHS:
            pipe = subprocess.Popen([path] + CHECKPATCH_ARGS,
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            out, err = pipe.communicate(patch)
            self._debug("check_patch: path = %s, out = '%s...', err = '%s...'",
                        path, out[:80], err[:80])
            parse_checkpatch_output(out, path_line_comments, warning_count)

        return review_input_and_score(path_line_comments, warning_count)

    def change_needs_review(self, change):
        """
        * Bail if the change isn't open (status is not 'NEW').
        * Bail if we've already reviewed the current revision.
        """
        status = change.get('status')
        if status != 'NEW':
            self._debug("change_needs_review: status = %s", status)
            return False

        current_revision = change.get('current_revision')
        self._debug("change_needs_review: current_revision = '%s'",
                    current_revision)
        if not current_revision:
            return False

        return True

    def review_change(self, change):
        """
        Review the current revision of change.
        * Pipe the patch through checkpatch(es).
        * POST review to gerrit.
        """
        score = 1
        self._debug("review_change: change = %s, subject = '%s'",
                    change['id'], change.get('subject', ''))

        current_revision = change.get('current_revision')
        self._debug("change_needs_review: current_revision = '%s'",
                    current_revision)
        if not current_revision:
            return score

        patch = self.get_patch(change, current_revision)
        if not patch:
            self._debug("review_change: no patch")
            return score

        review_input, score = self.check_patch(patch)
        self._debug("review_change: score = %d", score)
        self.post_review(change, current_revision, review_input)
        return score

    def update_single_change(self, change):
        """_"""
        open_changes = self.get_changes({'status': 'open',
                                         'change': change})
        self._debug("update: got %d open_changes", len(open_changes))

        score = 1
        for open_change in open_changes:
            if self.change_needs_review(open_change):
                score = self.review_change(open_change)
        return score

def main():
    """_"""
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.DEBUG)

    if GERRIT_USERNAME and GERRIT_HTTP_TOKEN:
        username = GERRIT_USERNAME
        password = GERRIT_HTTP_TOKEN
    else:
        with open(GERRIT_AUTH_PATH) as auth_file:
            auth = json.load(auth_file)
            username = auth[GERRIT_HOST]['gerrit/http']['username']
            password = auth[GERRIT_HOST]['gerrit/http']['password']

    reviewer = Reviewer(GERRIT_HOST, username, password)

    if GERRIT_CHANGE_NUMBER:
        score = reviewer.update_single_change(GERRIT_CHANGE_NUMBER)
        if score > 0:
            sys.exit(0)
        sys.exit(1)


if __name__ == "__main__":
    main()

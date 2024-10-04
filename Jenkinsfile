#!/usr/bin/groovy
// SPDX-License-Identifier: BSD-2-Clause-Patent
// Copyright (c) 2019-2024 Intel Corporation

// To use a test branch (i.e. PR) until it lands to master
// I.e. for testing library changes
//@Library(value="pipeline-lib@your_branch") _
def sanitized_JOB_NAME = JOB_NAME.replaceAll('/', '-').toLowerCase()

pipeline {
    agent { label 'lightweight' }

    environment {
        BAHTTPS_PROXY = "${env.HTTP_PROXY ? '--build-arg HTTP_PROXY="' + env.HTTP_PROXY + '" --build-arg http_proxy="' + env.HTTP_PROXY + '"' : ''}"
        BAHTTP_PROXY = "${env.HTTP_PROXY ? '--build-arg HTTPS_PROXY="' + env.HTTPS_PROXY + '" --build-arg https_proxy="' + env.HTTPS_PROXY + '"' : ''}"
        UID=sh(script: "id -u", returnStdout: true)
        BUILDARGS = "--build-arg NOBUILD=1 --build-arg UID=$env.UID $env.BAHTTP_PROXY $env.BAHTTPS_PROXY"
    }

    options {
        // preserve stashes so that jobs can be started at the test stage
        preserveStashes(buildCount: 5)
        timestamps ()
    }

    stages {
        stage('Pre-build') {
            parallel {
                stage('checkpatch') {
                    agent {
                        dockerfile {
                            filename 'Dockerfile.code_review'
                            dir 'utils/docker'
                            label 'docker_runner'
                            additionalBuildArgs "-t ${sanitized_JOB_NAME}-cr " + '$BUILDARGS'
                        }
                    }
                    steps {
                        checkPatch review_creds: 'daos-jenkins-review-posting',
                                   ignored_files: "test/*"
                    }
                    post {
                        always {
                            archiveArtifacts artifacts: 'pylint.log', allowEmptyArchive: true
                            /* when JENKINS-39203 is resolved, can probably use stepResult
                               here and remove the remaining post conditions
                               stepResult name: env.STAGE_NAME,
                                          context: 'build/' + env.STAGE_NAME,
                                          result: ${currentBuild.currentResult}
                            */
                        }
                        /* temporarily moved into stepResult due to JENKINS-39203
                        success {
                            githubNotify credentialsId: 'daos-jenkins-commit-status',
                                         description: env.STAGE_NAME,
                                         context: 'pre-build/' + env.STAGE_NAME,
                                         status: 'SUCCESS'
                        }
                        unstable {
                            githubNotify credentialsId: 'daos-jenkins-commit-status',
                                         description: env.STAGE_NAME,
                                         context: 'pre-build/' + env.STAGE_NAME,
                                         status: 'FAILURE'
                        }
                        failure {
                            githubNotify credentialsId: 'daos-jenkins-commit-status',
                                         description: env.STAGE_NAME,
                                         context: 'pre-build/' + env.STAGE_NAME,
                                         status: 'ERROR'
                        }
                        */
                    }
                }
            }
        }
        stage('Test') {
            agent {
                dockerfile {
                    filename 'Dockerfile.code_review'
                    dir 'utils/docker'
                    label 'docker_runner'
                    additionalBuildArgs "-t ${sanitized_JOB_NAME}-cr " + '$BUILDARGS'
                }
            }
            steps {
                withCredentials([[$class: 'UsernamePasswordMultiBinding',
                                credentialsId: 'daos-jenkins-review-posting',
                                usernameVariable: 'GH_USER',
                                passwordVariable: 'GH_PASS']]) {
                    sh """
                        tmpfile=\$(mktemp)
                        trap 'rm -f \$tmpfile' EXIT

                        export PATCHFILE=test/test.patch
                        export DISPLAY_RESULTS=true

                        ./jenkins_github_checkwarn.sh |
                          sed -e '/^commit:/s/".*"/"..."/' \
                              -e 's/blob\\/.*\\/test/blob\\/...\\/test/' \
                              -e '/ Style warning(s) for job/s/https.*/.../' > "\$tmpfile"

                        diff -u "\$tmpfile" test/expected_output"""
                }
            }
        }
    }
}

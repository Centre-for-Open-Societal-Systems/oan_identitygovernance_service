// =============================================================================
//  Jenkinsfile - oan-identitygovernance-service (central RBAC)
//
//  Every branch:  lint the chart.
//  develop        -> build + push the image, package the chart with that image, deploy it to oan_dev
//                    (ci/deploy-helm-dev.sh), its APISIX route chart (ci/deploy-gateway.sh), then publish both
//                    charts to repository/develop of oan-helm.
//  staging        -> promote what develop built from the same source tree (ci/package_chart.py promote: the image
//                    gets the <version>-staging.<build> tag, the chart is repackaged), then deploy both charts to
//                    the QA cluster (qa.oanstaging.com) with oan-qa-kubeconfig and publish to repository/staging.
//
//  Tags:  <version of pyproject.toml>-<branch>.<build>, e.g. 0.0.0-develop.2 (= chart version), like oan_a2c.
//  Credentials: aws-ecr-creds (ECR push/retag), oan-dev-kubeconfig / oan-qa-kubeconfig (file; the deployer
//               ServiceAccount, which needs the namespace in deployNamespaces of oan-helm's cluster-access values),
//               oan-helm-fork-token (publishing the chart).
// =============================================================================
pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        timeout(time: 30, unit: 'MINUTES')
    }

    environment {
        AWS_REGION        = 'ap-south-1'
        IMAGE_REGISTRY    = '379220350808.dkr.ecr.ap-south-1.amazonaws.com'
        ECR_REPO          = 'eoan/oan-identitygovernance-service/identitygovernance-service'
        RBAC_NAMESPACE    = 'identitygovernance'
        CHART_NAME        = 'oan-identitygovernance-service'
    }

    stages {

        stage('Resolve') {
            steps {
                checkout scm
                script {
                    // develop and staging: tag = chart version = <version>-<branch>.<build> (ci/package_chart.py version).
                    env.IMMUTABLE_TAG = "${env.BRANCH_NAME}-${env.BUILD_NUMBER}"
                    if (env.BRANCH_NAME == 'develop' || env.BRANCH_NAME == 'staging') {
                        env.IMMUTABLE_TAG = sh(returnStdout: true, script: 'python3 ci/package_chart.py version').trim()
                    }
                    env.MOVING_TAG = "${env.BRANCH_NAME}-latest"
                    env.FULL_IMAGE = "${env.IMAGE_REGISTRY}/${env.ECR_REPO}:${env.IMMUTABLE_TAG}"
                    echo "Branch ${env.BRANCH_NAME} -> tag ${env.IMMUTABLE_TAG}, image ${env.FULL_IMAGE}"
                }
            }
        }

        stage('Lint chart') {
            steps {
                sh '''
                    helm repo add openg2p https://openg2p.github.io/openg2p-helm >/dev/null
                    helm dependency build helm/oan-identitygovernance-service
                    helm lint helm/oan-identitygovernance-service \
                        -f helm/environments/dev/oan-identitygovernance-service.yaml
                    helm lint helm/oan-identitygovernance-service \
                        -f helm/environments/staging/oan-identitygovernance-service.yaml
                '''
            }
        }

        stage('Build image') {
            when { branch 'develop' }
            steps {
                sh '''
                    DOCKER_BUILDKIT=1 docker build \
                        -t "$FULL_IMAGE" \
                        -t "$IMAGE_REGISTRY/$ECR_REPO:$MOVING_TAG" \
                        .
                    # Never push an image that cannot import the app
                    docker run --rm "$FULL_IMAGE" python -c "import app.main"
                '''
            }
        }

        stage('Push image') {
            when { branch 'develop' }
            steps {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: 'aws-ecr-creds'
                ]]) {
                    sh '''#!/usr/bin/env bash
                        set -euo pipefail
                        aws ecr get-login-password --region "$AWS_REGION" \
                            | docker login --username AWS --password-stdin "$IMAGE_REGISTRY"

                        # ECR does not create a repository on push.
                        aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$ECR_REPO" >/dev/null 2>&1 \
                            || aws ecr create-repository --region "$AWS_REGION" --repository-name "$ECR_REPO" >/dev/null

                        docker push "$FULL_IMAGE"
                        docker push "$IMAGE_REGISTRY/$ECR_REPO:$MOVING_TAG"   # same digest -> ECR dedups
                        docker rmi "$FULL_IMAGE" "$IMAGE_REGISTRY/$ECR_REPO:$MOVING_TAG" || true
                        echo "Pushed image for ${BRANCH_NAME}"
                    '''
                }
            }
        }

        // The chart with this build's image in its values; the deploy installs this archive and
        // the same archive is published to oan-helm afterwards.
        // staging: `promote` instead of `package`: develop's chart for this source tree, its image tagged for staging.
        stage('Package chart') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                script {
                    env.IMAGE_TAG = env.IMMUTABLE_TAG
                    env.SOURCE_COMMIT = sh(returnStdout: true, script: 'git rev-parse --verify HEAD').trim()
                    env.COMMIT_SUBJECT = sh(returnStdout: true, script: 'git log -1 --pretty=%s').trim()
                    env.IMAGE_REPOSITORY = env.ECR_REPO   // registry is a separate value of the chart
                }
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: 'aws-ecr-creds'
                ]]) {
                    script {
                        if (env.BRANCH_NAME == 'staging') {
                            // The workspace holds the staging branch only; promote needs the develop commits the chart index names.
                            withCredentials([gitUsernamePassword(credentialsId: scm.userRemoteConfigs[0].credentialsId, gitToolName: 'Default')]) {
                                sh 'git fetch --quiet --no-tags origin +refs/heads/develop:refs/remotes/origin/develop'
                            }
                        }
                    }
                    sh "python3 ci/package_chart.py ${env.BRANCH_NAME == 'staging' ? 'promote' : 'package'}"
                }
                // The deploy stage runs on the VPN deploy agent, which gets the archive from here.
                stash name: 'built-chart', includes: 'built-chart/**'
            }
        }

        // The route chart (apisix/k8s) is packaged and published like the app chart, with the version of the build.
        stage('Package gateway chart') {
            when {
                beforeAgent true
                anyOf { branch 'develop'; branch 'staging' }
            }
            steps {
                sh 'python3 ci/package_chart.py gateway-package'
                stash name: 'built-gateway-chart', includes: 'built-gateway-chart/**'
            }
        }

        stage('develop → oan_dev (helm)') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }   // VPN agent with helm and kubectl that reaches the oan_dev API
            options { timeout(time: 20, unit: 'MINUTES') }
            steps {
                checkout scm
                sh 'rm -rf built-chart'
                unstash 'built-chart'
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'bash ci/deploy-helm-dev.sh'
                }
            }
        }

        // The APISIX route of the public host (chart apisix/k8s), after the app deploy.
        stage('develop → gateway (apisix)') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                checkout scm
                sh 'rm -rf built-gateway-chart'
                unstash 'built-gateway-chart'
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'bash ci/deploy-gateway.sh'
                }
            }
        }

        // staging -> Helm release `identitygovernance` on the QA cluster from the promoted archive, then its APISIX routes.
        stage('staging → QA cluster (helm)') {
            when { beforeAgent true; branch 'staging' }
            agent { label 'oan-ci-deploy' }   // VPN agent with helm and kubectl; must reach the QA API
            options { timeout(time: 20, unit: 'MINUTES') }
            steps {
                checkout scm
                sh 'rm -rf built-chart'
                unstash 'built-chart'
                withCredentials([file(credentialsId: 'oan-qa-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'DEPLOY_ENV=staging bash ci/deploy-helm-dev.sh'
                }
            }
        }

        stage('staging → QA cluster (apisix)') {
            when { beforeAgent true; branch 'staging' }
            agent { label 'oan-ci-deploy' }
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                checkout scm
                sh 'rm -rf built-gateway-chart'
                unstash 'built-gateway-chart'
                withCredentials([file(credentialsId: 'oan-qa-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'GATEWAY_VALUES=helm/environments/staging/oan-identitygovernance-gateway.yaml bash ci/deploy-gateway.sh'
                }
            }
        }

        stage('Verify') {
            when { beforeAgent true; anyOf { branch 'develop'; branch 'staging' } }
            agent { label 'oan-ci-deploy' }
            steps {
                sh '''
                    host="identitygovernance-service.development.oanstaging.com"
                    [ "$BRANCH_NAME" = "staging" ] && host="identitygovernance-service.qa.oanstaging.com"
                    url="https://$host/ping"
                    for i in $(seq 1 10); do
                        code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 "$url" || true)
                        echo "attempt $i: $url -> $code"
                        case "$code" in 2??|401|403) exit 0 ;; esac
                        sleep 6
                    done
                    echo "identitygovernance service did not answer at $url"
                    exit 1
                '''
            }
        }

        // Adds the archive built by 'Package chart' to repository/<channel>/ of oan-helm, only after the deploy succeeded.
        stage('Publish chart') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                // UNSTABLE, not failed: the image is already deployed.
                catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                    withCredentials([usernamePassword(credentialsId: 'oan-helm-fork-token', usernameVariable: 'GH_USER', passwordVariable: 'GH_TOKEN')]) {
                        sh 'python3 ci/package_chart.py publish'
                    }
                }
            }
        }

        stage('Publish gateway chart') {
            when {
                beforeAgent true
                anyOf { branch 'develop'; branch 'staging' }
            }
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                    withCredentials([usernamePassword(credentialsId: 'oan-helm-fork-token', usernameVariable: 'GH_USER', passwordVariable: 'GH_TOKEN')]) {
                        sh 'python3 ci/package_chart.py gateway-publish'
                    }
                }
            }
        }
    }

    post {
        failure {
            echo "oan-identitygovernance-service ${env.BRANCH_NAME} build failed."
        }
    }
}

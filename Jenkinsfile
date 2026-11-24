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
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                script {
                    def sha = sh(returnStdout: true, script: 'git rev-parse --short=7 HEAD').trim()
                    env.IMMUTABLE_TAG = "${env.BRANCH_NAME}-${env.BUILD_NUMBER}-${sha}"
                    env.FULL_IMAGE    = "${env.IMAGE_REGISTRY}/${env.ECR_REPO}:${env.IMMUTABLE_TAG}"
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
                '''
            }
        }

        stage('Build image') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                sh '''
                    DOCKER_BUILDKIT=1 docker build \
                        -t "$FULL_IMAGE" \
                        -t "$IMAGE_REGISTRY/$ECR_REPO:develop" \
                        .
                    # Never push an image that cannot import the app
                    docker run --rm "$FULL_IMAGE" python -c "import app.main"
                '''
            }
        }

        stage('Push image') {
            when { anyOf { branch 'develop'; branch 'staging' } }
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
                        if [ "${BRANCH_NAME}" = "develop" ]; then
                            docker push "$IMAGE_REGISTRY/$ECR_REPO:develop"
                            docker rmi "$FULL_IMAGE" "$IMAGE_REGISTRY/$ECR_REPO:develop" || true
                        else
                            docker rmi "$FULL_IMAGE" || true
                        fi
                        echo "Pushed image for ${BRANCH_NAME}"
                    '''
                }
            }
        }

        stage('develop → oan_dev (helm)') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }   // VPN agent with helm and kubectl that reaches the oan_dev API
            options { timeout(time: 20, unit: 'MINUTES') }
            steps {
                checkout scm
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'bash ci/deploy-helm-dev.sh'
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

#!/bin/bash
# Shared ANSI colour codes for Curatarr's shell scripts. Sourced by
# run.sh and setup.sh, which used to each define these byte-identically
# (see the 2.10.18 audit-remediation pass).
#
# docker-entrypoint.sh has its own (still byte-identical, RED/YELLOW/NC
# subset only) copy rather than sourcing this file: .dockerignore
# excludes scripts/ wholesale from the Docker build context (see that
# file's comment on keeping the image to app code only, no dev
# tooling), so this would need a .dockerignore negation exception
# carved out just to thread a 3-line copy through - not worth the
# added Docker-build fragility for that one file.
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

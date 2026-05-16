# fileHeron updater-shim — perpetual, ~50 lines of bash.
#
# Polls /state/current_job.json every 5s. When status == "pending",
# pulls the matching `fileheron-updater-executor:<target_tag>` image
# and runs it with the socket mounted. The executor does the actual
# work + writes incremental status back to /state/current_job.json.
#
# Why so simple? Because this is the ONE container that can't update
# itself (containers can't `docker compose up -d` themselves without
# committing suicide mid-command). Keeping it trivial means it
# shouldn't ever need to be updated — there's nothing in it to break.
# The executor (changeable per-release) does all the real logic.
FROM alpine:3.20

# docker CLI for the spawn; jq for reading the state file; bash for
# the shim itself; curl is occasionally useful for debugging.
RUN apk add --no-cache docker-cli jq bash curl

ARG FH_VERSION=0.0.0-dev
ARG FH_GIT_SHA=unknown
ENV FH_VERSION=${FH_VERSION} \
    FH_GIT_SHA=${FH_GIT_SHA}
LABEL org.opencontainers.image.version="${FH_VERSION}" \
      org.opencontainers.image.revision="${FH_GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/phoen-ix/fileHeron"

COPY docker/updater-shim/shim.sh /shim.sh
RUN chmod +x /shim.sh

CMD ["/shim.sh"]

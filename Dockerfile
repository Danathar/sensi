# Pinned by digest rather than tracking :latest, so what a devcontainer runs is
# decided by this file and not by whatever was last pushed to a namespace this
# fork does not control. The tag is kept for readability; the digest is what
# actually resolves. Re-resolve deliberately to update:
#
#   docker buildx imagetools inspect ghcr.io/iprak/custom-integration-image:latest \
#     --format "{{.Manifest.Digest}}"
FROM ghcr.io/iprak/custom-integration-image:latest@sha256:98dd8a14b4647f106cb018570245d959ff3e81a0129bf2f3fe9bd492a124d9b6

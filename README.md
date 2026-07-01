# Zeno Images

Shared container base images for Zeno deployments.

## Ruby API Base

`ruby-api-base` publishes two GHCR tags for Ruby API services:

- `ghcr.io/zenomcpe/ruby-api-base:3.4-build`
- `ghcr.io/zenomcpe/ruby-api-base:3.4-runtime`

Use the `build` tag for bundle install and test stages. Use the `runtime` tag for the final deployed image.

## Lisette Go Base

`lisette-go-base` publishes a Go and Lisette build image for Lisette services:

- `ghcr.io/zenomcpe/lisette-go-base:go1.26.4`
- `ghcr.io/zenomcpe/lisette-go-base:go1.26.4-e819ec010fe9f3758d45e961a0d9a11acb3dd26f`

Use this image for Lisette build and test stages that need the pinned Go toolchain and Lisette compiler.

# Zeno Images

Shared container base images for Zeno deployments.

## Ruby API Base

`ruby-api-base` publishes two GHCR tags for Ruby API services:

- `ghcr.io/zenomcpe/ruby-api-base:3.4-build`
- `ghcr.io/zenomcpe/ruby-api-base:3.4-runtime`

Use the `build` tag for bundle install and test stages. Use the `runtime` tag for the final deployed image.


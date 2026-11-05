# Zeno Images

Shared container base images for Zeno deployments.

## Ruby API Base

`ruby-api-base` publishes two GHCR tags for Ruby API services:

- `ghcr.io/zenomcpe/ruby-api-base:3.4.12-signal-close-1565adc-build`
- `ghcr.io/zenomcpe/ruby-api-base:3.4.12-signal-close-1565adc-runtime`

Use the `build` tag for bundle install and test stages. Use the `runtime` tag for the final deployed image.

These images apply the functional change from the upstream [signal-context pipe-close backport](https://github.com/ruby/ruby/commit/1565adc5b9d2dfc6d288dc9bdefb19b2423aa28c) to Ruby 3.4.12, retaining the released patchlevel 148. The shared library is rebuilt with the original configuration and ABI; the base standard library and extensions stay intact. Both image targets must pass a real blocked-writer signal/close regression before publication. Commit-qualified tags and image digests identify built outputs. Use a digest when pinning a deployed image.

## Lisette Go Base

`lisette-go-base` publishes a Go and Lisette build image for Lisette services:

- `ghcr.io/zenomcpe/lisette-go-base:go1.26.4`
- `ghcr.io/zenomcpe/lisette-go-base:go1.26.4-e819ec010fe9f3758d45e961a0d9a11acb3dd26f`

Use this image for Lisette build and test stages that need the pinned Go toolchain and Lisette compiler.

## GoPlus Base

`goplus-base` publishes the pinned GoPlus toolchain used while GoPlus remains a maintained Go fork:

- `ghcr.io/zenomcpe/goplus-base:go1.28-devel`
- `ghcr.io/zenomcpe/goplus-base:go1.28-devel-ee3aaf3a1d05b3f378c2df051a0b43683233ab81`

Use the commit-qualified tag for reproducible builds. The shorter development tag follows the commit configured in the image workflow. The forked toolchain is exposed as `go+` so it remains visibly distinct from standard Go.

## Internal client native builders

The manual internal client workflow builds Windows and both macOS architectures
for the private client release pipeline. It stores no application source history.
See [builder instructions](client-native/README.md).

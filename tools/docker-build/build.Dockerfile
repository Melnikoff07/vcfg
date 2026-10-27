# VCFG build image: Linux amd64 + LLVM 18 + OpenOrbis toolchain v0.5.4 + GoldHEN Plugins SDK (built here).
# Build context: the dl/ directory filled by fetch-deps.sh (toolchain-llvm-18.tar.gz + goldhen-sdk at a pinned commit).
# PkgTool.Core is a .NET Core 3.x single-file app: it needs libicu66 and libssl1.1 (Ubuntu 20.04), as in the
# OpenOrbis Dockerfile. LLVM 18 comes from Ubuntu 24.04, so the two runtime libs are taken from a focal stage.
FROM --platform=linux/amd64 ubuntu:20.04 AS focal
RUN apt-get update && cd / && apt-get download libicu66 libssl1.1 && ls /*.deb
FROM --platform=linux/amd64 ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      clang-18 lld-18 llvm-18 make python3 libicu74 libssl3 ca-certificates file \
    && rm -rf /var/lib/apt/lists/* \
    && for t in clang clang++ ld.lld llvm-ar llc lli llvm-objdump llvm-readelf; do \
         p=$(ls /usr/bin/$t-18 2>/dev/null || ls /usr/lib/llvm-18/bin/$t); ln -sf "$p" /usr/local/bin/$t; done
COPY --from=focal /*.deb /tmp/focal/
RUN dpkg -i /tmp/focal/*.deb && rm -rf /tmp/focal
COPY toolchain-llvm-18.tar.gz /tmp/oo.tgz
RUN mkdir -p /opt/oo && tar -xzf /tmp/oo.tgz -C /opt/oo && rm /tmp/oo.tgz \
    && test -f /opt/oo/OpenOrbis/PS4Toolchain/link.x
ENV OO_PS4_TOOLCHAIN=/opt/oo/OpenOrbis/PS4Toolchain DOTNET_ROLL_FORWARD=Major
COPY goldhen-sdk /opt/goldhen-sdk
RUN cd /opt/goldhen-sdk && make all && test -f build/crtprx.o && test -f libGoldHEN_Hook.a

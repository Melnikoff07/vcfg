# Shader image for classic/gen_shaders.py: Ubuntu 24.04 + llc-22 from apt.llvm.org.
# llc 22.1.8 regenerates the versioned classic/source/shaders.h byte for byte; 18-21 do not. apt.llvm.org only
# serves the latest 22.x, so the build fails if it no longer installs the expected version. Override with
# --build-arg LLC_EXPECT=any and check <out>/shaders-regen.txt from build_all.sh to see if the header still matches.
FROM ubuntu:24.04
ARG LLC_EXPECT=22.1.8
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq wget gnupg lsb-release software-properties-common python3 >/dev/null && \
    wget -q https://apt.llvm.org/llvm.sh -O /tmp/llvm.sh && bash /tmp/llvm.sh 22 >/dev/null && apt-get install -y -qq llvm-22 >/dev/null && \
    rm -rf /var/lib/apt/lists/* /tmp/llvm.sh && \
    llc-22 --version | head -3 && \
    v=$(llc-22 --version | sed -n 's/.*LLVM version \([0-9.]*\).*/\1/p') && \
    { [ "$LLC_EXPECT" = any ] || [ "$v" = "$LLC_EXPECT" ] || { echo "llc-22 is $v, expected $LLC_EXPECT (see the comment at the top)" >&2; exit 1; }; }

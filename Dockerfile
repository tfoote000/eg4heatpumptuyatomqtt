FROM --platform=$BUILDPLATFORM rust:1-bookworm AS chef
RUN cargo install cargo-chef
WORKDIR /app

FROM chef AS planner
COPY . .
RUN cargo chef prepare --recipe-path recipe.json

FROM chef AS builder
ARG TARGETPLATFORM

# Install cross-compilation toolchains based on target
RUN case "$TARGETPLATFORM" in \
      "linux/arm64") \
        apt-get update && apt-get install -y gcc-aarch64-linux-gnu && \
        rustup target add aarch64-unknown-linux-gnu ;; \
      "linux/arm/v7") \
        apt-get update && apt-get install -y gcc-arm-linux-gnueabihf && \
        rustup target add armv7-unknown-linux-gnueabihf ;; \
      *) ;; \
    esac

# Set linker for cross-compilation
ENV CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
ENV CARGO_TARGET_ARMV7_UNKNOWN_LINUX_GNUEABIHF_LINKER=arm-linux-gnueabihf-gcc

COPY --from=planner /app/recipe.json recipe.json

# Build dependencies (cached layer)
RUN case "$TARGETPLATFORM" in \
      "linux/arm64") \
        cargo chef cook --release --target aarch64-unknown-linux-gnu --recipe-path recipe.json ;; \
      "linux/arm/v7") \
        cargo chef cook --release --target armv7-unknown-linux-gnueabihf --recipe-path recipe.json ;; \
      *) \
        cargo chef cook --release --recipe-path recipe.json ;; \
    esac

COPY . .

# Build the actual binary
RUN case "$TARGETPLATFORM" in \
      "linux/arm64") \
        cargo build --release --target aarch64-unknown-linux-gnu && \
        cp target/aarch64-unknown-linux-gnu/release/tuya-to-mqtt /app/tuya-to-mqtt ;; \
      "linux/arm/v7") \
        cargo build --release --target armv7-unknown-linux-gnueabihf && \
        cp target/armv7-unknown-linux-gnueabihf/release/tuya-to-mqtt /app/tuya-to-mqtt ;; \
      *) \
        cargo build --release && \
        cp target/release/tuya-to-mqtt /app/tuya-to-mqtt ;; \
    esac

FROM debian:bookworm-slim AS runtime
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/tuya-to-mqtt /usr/local/bin/tuya-to-mqtt
ENTRYPOINT ["tuya-to-mqtt"]

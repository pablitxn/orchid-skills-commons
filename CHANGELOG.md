# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- _No changes yet._

### Changed
- _No changes yet._

### Fixed
- _No changes yet._

## [0.1.0] - 2026-02-11

### Added
- Initial public release of `orchid-mcp-commons`.
- Typed configuration loading from `appsettings*.json` plus environment placeholder resolution.
- Async `ResourceManager` for startup/shutdown lifecycle, named resource lookup, and aggregated health reporting.
- Data connectors for SQLite, PostgreSQL, Redis, MongoDB, RabbitMQ, and Qdrant.
- S3-compatible blob layer for MinIO/S3, Cloudflare R2, and a multi-bucket router.
- Observability primitives for structured logging/correlation, Prometheus metrics, OpenTelemetry tracing, and a safe Langfuse wrapper.
- Unit, integration, and e2e coverage with CI jobs split between `integration` and `e2e`.

### Changed
- _N/A (initial release)._

### Fixed
- Pre-release hardening for lifecycle resilience, transient error classification, and fail-open observability fallbacks.

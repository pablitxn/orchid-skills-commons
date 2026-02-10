#!/usr/bin/env python3
"""
End-to-end integration test runner for orchid-skills-commons.

This script:
1. Checks if required services are running
2. Sets up environment variables
3. Runs pytest integration tests
4. Reports results

Usage:
    python tests/integration/run_e2e.py [--check-only] [--verbose]

Prerequisites:
    # Start infrastructure
    cd examples/infrastructure && docker compose up -d

    # Start observability (optional)
    cd examples/observability && docker compose up -d
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from urllib.error import URLError
from urllib.request import urlopen


def check_service(name: str, url: str, timeout: float = 2.0) -> bool:
    """Check if a service is reachable."""
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.status < 500
    except (URLError, TimeoutError, OSError):
        return False


def check_tcp_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP port is open."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, OSError):
        return False


def print_status(name: str, available: bool) -> None:
    """Print service status."""
    status = "\033[92m[OK]\033[0m" if available else "\033[91m[NOT AVAILABLE]\033[0m"
    print(f"  {name}: {status}")


def check_all_services() -> dict[str, bool]:
    """Check all required services."""
    services = {
        "MinIO": ("http://localhost:9000/minio/health/live", None),
        "MongoDB": (None, ("localhost", 27017)),
        "Qdrant": ("http://localhost:6333/healthz", None),
        "Redis": (None, ("localhost", 6379)),
        "PostgreSQL": (None, ("localhost", 5432)),
        "RabbitMQ": ("http://localhost:15672", None),
        "Prometheus": ("http://localhost:9090/-/ready", None),
        "Jaeger": ("http://localhost:16686", None),
        "Grafana": ("http://localhost:3300/api/health", None),
        "OTEL Collector": (None, ("localhost", 4317)),
    }

    results = {}
    print("\nChecking services...")
    print("-" * 40)

    for name, (url, tcp) in services.items():
        if url:
            available = check_service(name, url)
        else:
            host, port = tcp
            available = check_tcp_port(host, port)
        results[name] = available
        print_status(name, available)

    print("-" * 40)
    return results


def set_environment_variables() -> None:
    """Set environment variables for integration tests."""
    import os

    env_vars = {
        # MinIO
        "ORCHID_MINIO_ENDPOINT": "localhost:9000",
        "ORCHID_MINIO_ACCESS_KEY": "minioadmin",
        "ORCHID_MINIO_SECRET_KEY": "minioadmin",
        # Qdrant
        "ORCHID_QDRANT_HOST": "localhost",
        "ORCHID_QDRANT_PORT": "6333",
        # PostgreSQL
        "ORCHID_POSTGRES_DSN": "postgresql://postgres:postgres@localhost:5432/orchid",
        # MongoDB
        "ORCHID_MONGODB_URI": "mongodb://localhost:27017",
        # Redis
        "ORCHID_REDIS_URL": "redis://localhost:6379",
    }

    for key, value in env_vars.items():
        if key not in os.environ:
            os.environ[key] = value
            print(f"  Set {key}={value}")


def run_tests(verbose: bool = False, markers: list[str] | None = None) -> int:
    """Run pytest integration tests."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/integration/",
        "-m",
        "integration",
        "--tb=short",
    ]

    if verbose:
        cmd.extend(["-v", "-s"])

    if markers:
        for marker in markers:
            cmd.extend(["-k", marker])

    print("\nRunning tests...")
    print(f"Command: {' '.join(cmd)}")
    print("-" * 40)

    result = subprocess.run(cmd, cwd="/Users/pablitxn/repos/orchid_skills_commons_py")
    return result.returncode


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run E2E integration tests")
    parser.add_argument("--check-only", action="store_true", help="Only check services, don't run tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-k", "--keyword", action="append", help="Filter tests by keyword")
    args = parser.parse_args()

    print("=" * 50)
    print("Orchid Commons - E2E Integration Tests")
    print("=" * 50)

    # Check services
    results = check_all_services()

    if args.check_only:
        available = sum(1 for v in results.values() if v)
        print(f"\n{available}/{len(results)} services available")
        return 0 if available > 0 else 1

    # Check minimum required services
    required = ["MinIO", "Qdrant"]
    missing = [name for name in required if not results.get(name)]
    if missing:
        print(f"\nRequired services not available: {', '.join(missing)}")
        print("Start infrastructure: cd examples/infrastructure && docker compose up -d")
        return 1

    # Set environment
    print("\nSetting environment variables...")
    set_environment_variables()

    # Run tests
    return run_tests(verbose=args.verbose, markers=args.keyword)


if __name__ == "__main__":
    sys.exit(main())

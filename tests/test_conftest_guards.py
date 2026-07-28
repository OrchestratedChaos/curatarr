"""Direct tests for tests/conftest.py's own suite-wide safety-net
fixtures - specifically _block_non_loopback_sockets/
_is_allowed_test_socket_address, which every other test file relies on
implicitly (autouse) but nothing exercised directly until now."""

import socket

import pytest


class TestBlockNonLoopbackSockets:
    """Tests for the autouse _block_non_loopback_sockets fixture (see
    tests/conftest.py for the full rationale)."""

    def test_blocks_genuine_external_host(self):
        """A real, unambiguous non-loopback host must still be blocked -
        this is the guard's original, always-worked case."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(AssertionError, match="Blocked outbound"):
                sock.connect(("93.184.216.34", 443))
        finally:
            sock.close()

    def test_allows_loopback_on_an_ordinary_port(self):
        """This suite's own local test-server binds (test_web_routes.py's
        TestWaitForListening/TestBindRetry) connect to 127.0.0.1 on an
        OS-assigned ephemeral port - never Plex's default port - and
        must keep working unchanged."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.settimeout(2)
            client.connect(("127.0.0.1", port))  # must not raise
        finally:
            client.close()
            server.close()

    def test_blocks_loopback_resolving_plex_direct_style_address(self):
        """The real bug this closes: plex.direct hostnames (Plex's own
        DNS names for every real server - e.g. this project's own
        config/config.yml has a real 'https://127-0-0-1.<hash>.
        plex.direct:32400' URL) resolve straight to a loopback IP, which
        is standard, expected Plex behavior, not something exotic. A
        loopback-only check would silently ALLOW a test that ends up
        using a real config to connect straight through to a real, live
        Plex Media Server instance on this same machine - defeating the
        whole point of this guard. By the time a real HTTP client's
        socket.connect() fires, the hostname is already resolved to
        plain 127.0.0.1 (that's simulated directly here, rather than
        performing a real DNS lookup of a fake plex.direct name, which
        wouldn't resolve and would make this test network-dependent for
        no benefit - the address shape below is exactly what a real
        resolved connect() call receives either way). This must now be
        blocked specifically because of the Plex-default port, even
        though the host alone is loopback."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(AssertionError, match="32400"):
                sock.connect(("127.0.0.1", 32400))
        finally:
            sock.close()

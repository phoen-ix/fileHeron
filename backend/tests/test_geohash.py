"""ip_geohash5 - IP /24 (v4) or /64 (v6) neighborhood hash for the new-device
alert dedup. Not real geolocation."""
from __future__ import annotations

from app.utils.geohash import ip_geohash5


def test_ipv6_same_64_groups_together():
    # Two addresses in the same /64 (differ only in host bits) must hash the same.
    # A compressed "::" used to be string-split and absorb host bits, breaking this.
    a = ip_geohash5("2001:db8::1")
    b = ip_geohash5("2001:db8::abcd:1234:5678:9abc")
    assert a and a == b


def test_ipv6_different_64_differs():
    assert ip_geohash5("2001:db8:1::1") != ip_geohash5("2001:db8:2::1")


def test_ipv4_24_prefix_groups():
    assert ip_geohash5("10.0.0.5") == ip_geohash5("10.0.0.200")
    assert ip_geohash5("10.0.0.5") != ip_geohash5("10.0.1.5")


def test_empty_and_garbage_are_safe():
    assert ip_geohash5(None) == ""
    assert ip_geohash5("") == ""
    # Unparseable v6-looking string falls back to hashing the raw value, no raise.
    assert ip_geohash5("not:an:ip") != ""

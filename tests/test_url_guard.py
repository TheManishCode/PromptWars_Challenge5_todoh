"""check_url is the only thing between a hostile page or search result and the user's own network: pin every rule and boundary."""

import pytest

from eventmate.integrations.web_access import MAX_HOST, MAX_URL, UnsafeURL, _public, check_url

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def ok(url, resolver=PUBLIC):
    return check_url(url, resolver)


def refused(url, resolver=PUBLIC):
    with pytest.raises(UnsafeURL) as e:
        check_url(url, resolver)
    return str(e.value)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0", "100.64.0.1", "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254"])
def test_private_loopback_link_local_and_mapped_addresses_are_never_public(ip):
    assert not _public(ip)
    assert "not public" in refused(f"http://[{ip}]/" if ":" in ip else f"http://{ip}/")


def test_public_addresses_and_garbage_are_told_apart():
    assert _public("93.184.216.34") and _public("2606:4700:4700::1111") and _public("::ffff:93.184.216.34")
    assert not _public("not-an-ip") and not _public("")


def test_a_public_ip_literal_is_allowed_and_ipv6_keeps_its_brackets():
    assert ok("http://93.184.216.34/x?q=1") == ("http://93.184.216.34/x?q=1", "93.184.216.34", ["93.184.216.34"])
    norm, host, ips = ok("https://[2606:4700:4700::1111]/p")
    assert norm == "https://[2606:4700:4700::1111]/p" and host == "2606:4700:4700::1111" and ips == [host]


def test_a_name_is_normalised_lowercased_and_resolved():
    norm, host, ips = ok("HTTPS://Example.COM./a/b?x=1#frag")
    assert (norm, host, ips) == ("https://example.com/a/b?x=1", "example.com", ["93.184.216.34"])
    assert ok("http://example.com")[0] == "http://example.com/"  # an empty path becomes /
    assert ok("http://bücher.example/")[1] == "xn--bcher-kva.example"  # international names are converted, then checked


@pytest.mark.parametrize("host", ["localhost", "a.localhost", "printer.local", "db.internal", "nas.localdomain"])
def test_local_style_names_are_refused_before_any_lookup(host):
    def never(h):
        raise AssertionError("must not resolve")

    assert "not public" in refused(f"http://{host}/", never)


def test_a_name_that_resolves_anywhere_private_is_refused_even_if_one_address_is_public():
    assert "public address" in refused("http://example.com/", lambda h: ["93.184.216.34", "10.0.0.1"])
    assert "public address" in refused("http://example.com/", lambda h: [])


def test_syntax_rules():
    assert "http and https" in refused("ftp://example.com/") and "http and https" in refused("file:///etc/passwd") and "http and https" in refused("javascript:alert(1)")
    assert "credentials" in refused("http://user:pw@example.com/") and "credentials" in refused("http://user@example.com/")
    assert "default port" in refused("http://example.com:8080/") and "default port" in refused("https://example.com:80/")
    assert ok("http://example.com:80/")[1] == "example.com" and ok("https://example.com:443/")[1] == "example.com"
    assert "port is invalid" in refused("http://example.com:99999/") and "port is invalid" in refused("http://example.com:abc/")
    assert "no host" in refused("http:///path")
    assert "malformed" in refused(None) and "malformed" in refused(123)


def test_name_shape_rules():
    for bad in ("http://singlelabel/", "http://-bad.example/", "http://bad-.example/", "http://a..example/", "http://example.123/", "http://exa_mple.com/"):
        assert "host name is invalid" in refused(bad), bad
    assert "host name is invalid" in refused("http://" + "a" * 64 + ".example/")  # a label over 63 characters
    assert "host name is invalid" in refused("http://\u0430\u0430" + "\u200b" * 0 + "..example/")  # empty label after idna


def test_length_and_control_character_boundaries():
    prefix = "http://example.com/"
    assert ok(prefix + "a" * (MAX_URL - len(prefix)))[1] == "example.com"  # exactly the limit is allowed
    assert "malformed" in refused(prefix + "a" * (MAX_URL - len(prefix) + 1))
    assert "malformed" in refused("http://example.com/a b") and "malformed" in refused("http://example.com/a\tb") and "malformed" in refused("http://example.com/a\x7fb")
    assert ok("http://example.com/a!b")[0].endswith("/a!b")  # the first printable character after space is fine
    name = ".".join(["a" * 63] * 3 + ["b" * (MAX_HOST - 63 * 3 - 3)])  # exactly MAX_HOST characters
    assert len(name) == MAX_HOST and ok(f"http://{name}/")[1] == name
    assert "host name is invalid" in refused(f"http://{name}x/")

"""scripts/store_submit.py — the Partner Center submission, against a fake Store."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "store_submit", Path(__file__).parent.parent / "scripts" / "store_submit.py"
)
store_submit = importlib.util.module_from_spec(_spec)
sys.modules["store_submit"] = store_submit
_spec.loader.exec_module(store_submit)

ENV = {
    "STORE_TENANT_ID": "tenant",
    "STORE_CLIENT_ID": "client",
    "STORE_CLIENT_SECRET": "s3cret",
    "STORE_SELLER_ID": "12345",
    "STORE_PRODUCT_ID": "XP0000000000",
}
OLD_URL = "https://downloads.example.com/cadent/v0.6.0/Cadent-Setup-0.6.0.exe"
NEW_URL = "https://downloads.example.com/cadent/v0.7.0/Cadent-Setup-0.7.0.exe"
BASE = f"{store_submit.API}/submission/v1/product/XP0000000000"


def package(url=OLD_URL):
    return {
        "packageId": "pack1",
        "packageUrl": url,
        "languages": ["en-us"],
        "architectures": ["X64"],
        "isSilentInstall": True,
        "installerParameters": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
        "packageType": "exe",
    }


class FakeStore:
    """Answers the way the API does, and records what it was asked."""

    def __init__(self, packages=None, ongoing="", ready_after=1, status_errors=None):
        self.packages = [package()] if packages is None else packages
        self.ongoing = ongoing
        self.ready_after = ready_after
        self.status_errors = status_errors or []
        self.calls = []
        self.status_polls = 0
        self.committed = False

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if "login.microsoftonline.com" in url:
            return 200, {"access_token": "tok"}
        path = url.removeprefix(BASE)
        if (method, path) == ("GET", "/status"):
            if not self.committed:
                return 200, ok({"isReady": True, "ongoingSubmissionId": self.ongoing})
            self.status_polls += 1
            ready = self.status_polls >= self.ready_after
            return 200, ok({"isReady": ready, "ongoingSubmissionId": ""},
                           errors=self.status_errors)
        if (method, path) == ("GET", "/packages"):
            return 200, ok({"packages": self.packages})
        if (method, path) == ("PUT", "/packages"):
            self.packages = json.loads(body)["packages"]
            return 200, ok({})
        if (method, path) == ("POST", "/packages/commit"):
            self.committed = True
            return 200, ok({"pollingUrl": "/status"})
        if (method, path) == ("POST", "/submit"):
            return 200, ok({"submissionId": "sub-1"})
        raise AssertionError(f"unexpected call: {method} {url}")

    def made(self, method, path):
        return [c for c in self.calls if c[0] == method and c[1] == BASE + path]


def ok(data, errors=None):
    return {"isSuccess": True, "errors": errors or [], "responseData": data}


def client(store):
    return store_submit.StoreClient(store_submit.resolve_credentials(ENV), store)


def run(store, url=NEW_URL, **kwargs):
    return store_submit.submit(client(store), url, sleep=lambda _: None, **kwargs)


class TestResolveCredentials:
    def test_none_set_is_a_skip(self):
        assert store_submit.resolve_credentials({}) is None

    def test_all_set(self):
        credentials = store_submit.resolve_credentials(ENV)
        assert credentials.product_id == "XP0000000000"
        assert credentials.seller_id == "12345"

    def test_partial_set_names_what_is_missing(self):
        partial = {k: v for k, v in ENV.items() if k != "STORE_SELLER_ID"}
        with pytest.raises(store_submit.StoreError, match="STORE_SELLER_ID"):
            store_submit.resolve_credentials(partial)

    def test_blank_counts_as_unset(self):
        # An unset GitHub secret reaches the step as an empty string.
        assert store_submit.resolve_credentials(dict.fromkeys(ENV, "")) is None


class TestSubmit:
    def test_repoints_the_package_and_submits(self):
        store = FakeStore()
        assert run(store) == "sub-1"
        assert store.packages[0]["packageUrl"] == NEW_URL
        assert store.made("POST", "/packages/commit")
        assert store.made("POST", "/submit")

    def test_carries_the_rest_of_the_package_over_untouched(self):
        store = FakeStore()
        run(store)
        assert store.packages == [package(NEW_URL)]

    def test_drops_the_space_the_store_puts_before_the_switches(self):
        # What the API really returns, and one character over what it accepts.
        padded = {**package(), "installerParameters":
                  " /VERYSILENT /SUPPRESSMSGBOXES /NORESTART"}
        store = FakeStore(packages=[padded])
        run(store)
        assert store.packages[0]["installerParameters"] == \
            "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"

    def test_sends_the_seller_id_and_token(self):
        store = FakeStore()
        run(store)
        headers = store.made("GET", "/packages")[0][2]
        assert headers["X-Seller-Account-Id"] == "12345"
        assert headers["Authorization"] == "Bearer tok"

    def test_refuses_while_a_submission_is_in_flight(self):
        store = FakeStore(ongoing="sub-0")
        with pytest.raises(store_submit.StoreError, match="sub-0"):
            run(store)
        assert not store.made("PUT", "/packages")

    def test_already_pointing_at_the_url_does_nothing(self):
        store = FakeStore(packages=[package(NEW_URL)])
        assert run(store) is None
        assert not store.made("PUT", "/packages")
        assert not store.made("POST", "/submit")

    def test_force_submits_a_draft_that_already_points_there(self):
        store = FakeStore(packages=[package(NEW_URL)])
        assert run(store, force=True) == "sub-1"

    @pytest.mark.parametrize("packages", [[], [package(), package()]])
    def test_refuses_a_draft_it_does_not_recognise(self, packages):
        store = FakeStore(packages=packages)
        with pytest.raises(store_submit.StoreError, match="exactly one package"):
            run(store)
        assert not store.made("PUT", "/packages")

    def test_waits_for_the_store_to_scan_the_package(self):
        store = FakeStore(ready_after=3)
        assert run(store) == "sub-1"
        assert store.status_polls == 3

    def test_a_failed_upload_stops_before_submitting(self):
        store = FakeStore(ready_after=99, status_errors=[
            {"code": "packageuploaderror", "message": "unsigned", "target": "packages"}])
        with pytest.raises(store_submit.StoreError, match="unsigned"):
            run(store)
        assert not store.made("POST", "/submit")

    def test_never_ready_times_out_without_submitting(self):
        store = FakeStore(ready_after=10**6)
        ticks = iter(range(0, 10**6, 60))
        with pytest.raises(store_submit.StoreError, match="not ready"):
            run(store, clock=lambda: next(ticks), timeout=300)
        assert not store.made("POST", "/submit")


class TestRefusals:
    def test_an_api_refusal_carries_the_stores_own_message(self):
        def transport(method, url, headers, body):
            if "login.microsoftonline.com" in url:
                return 200, {"access_token": "tok"}
            return 400, {"isSuccess": False, "errors": [
                {"code": "badrequest", "message": "no such product", "target": "product"}]}

        with pytest.raises(store_submit.StoreError, match="no such product"):
            run(transport)

    def test_is_success_false_fails_even_on_a_200(self):
        def transport(method, url, headers, body):
            if "login.microsoftonline.com" in url:
                return 200, {"access_token": "tok"}
            return 200, {"isSuccess": False, "errors": [
                {"code": "invalidstate", "message": "draft locked"}]}

        with pytest.raises(store_submit.StoreError, match="draft locked"):
            run(transport)

    def test_a_rejected_login_does_not_leak_the_secret(self):
        def transport(method, url, headers, body):
            return 401, {"error": "invalid_client",
                         "error_description": "AADSTS7000222: client secret expired"}

        with pytest.raises(store_submit.StoreError) as raised:
            run(transport)
        assert "expired" in str(raised.value)
        assert "s3cret" not in str(raised.value)


class TestMain:
    def test_no_credentials_skips(self, monkeypatch, capsys):
        for name in ENV:
            monkeypatch.delenv(name, raising=False)
        assert store_submit.main(["--url", NEW_URL]) == 0
        assert "skipping" in capsys.readouterr().out

    def test_require_turns_the_skip_into_a_failure(self, monkeypatch):
        for name in ENV:
            monkeypatch.delenv(name, raising=False)
        assert store_submit.main(["--url", NEW_URL, "--require"]) == 1

    def test_partial_credentials_fail(self, monkeypatch, capsys):
        for name in ENV:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("STORE_PRODUCT_ID", "XP0000000000")
        assert store_submit.main(["--url", NEW_URL]) == 1
        assert "STORE_TENANT_ID" in capsys.readouterr().err

    def test_rejects_a_plain_http_url(self, monkeypatch, capsys):
        for name, value in ENV.items():
            monkeypatch.setenv(name, value)
        assert store_submit.main(["--url", "http://example.com/a.exe"]) == 1
        assert "https" in capsys.readouterr().err

"""Submit a released installer to the Microsoft Store.

The last manual step of a Windows release was pasting the R2 package URL into
Partner Center. The Store's submission API for MSI/EXE apps does the same
thing, so this does it instead:

    python scripts/store_submit.py --url https://downloads.example.com/cadent/v1.2.3/Cadent-Setup-1.2.3.exe

What a run does, in order, and why each step is there:

1. Checks the URL answers 200 without redirecting. Partner Center rejects a
   redirect, and finding that here costs a second where finding it in
   certification costs days.
2. Refuses if a submission is already in flight. The Store allows one at a
   time, and the draft cannot be edited underneath it.
3. Reads the draft's package and changes only its URL. Languages,
   architectures and the silent-install switches were entered by a human in
   the first submission and are carried over untouched, so this script holds
   no second copy of them to drift.
4. Commits, then waits for the Store to fetch and scan the installer.
5. Submits for certification, and stops. Certification takes hours to days;
   the result arrives by email and in Partner Center, not in a CI job that
   would have to stay alive for it.

The API cannot create an app or its first submission - the listing, age
rating and installer switches have to exist already. See
scripts/store_setup_wizard.sh for that, and docs/agents/releases.md for where
this sits in a release.

Credentials come from the environment, and their absence is a supported state
(see `resolve_credentials`), the same shape as signing and R2 publishing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

API = "https://api.store.microsoft.com"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SCOPE = "https://api.store.microsoft.com/.default"
USER_AGENT = "cadent-store-submit"

CREDENTIAL_VARS = (
    "STORE_TENANT_ID",
    "STORE_CLIENT_ID",
    "STORE_CLIENT_SECRET",
    "STORE_SELLER_ID",
    "STORE_PRODUCT_ID",
)

# The Store downloads and scans the installer between commit and ready. A
# ~110 MB package has been taking a few minutes; the cap is for a fetch that
# never finishes, not a tight estimate.
POLL_INTERVAL = 10.0
POLL_TIMEOUT = 20 * 60.0

# (method, url, headers, body) -> (status, parsed JSON body). The seam the
# tests replace, so nothing in them reaches Microsoft.
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict]]


class StoreError(Exception):
    """Anything that should fail the job with a message rather than a traceback."""


@dataclass(frozen=True)
class Credentials:
    tenant_id: str
    client_id: str
    client_secret: str
    seller_id: str
    product_id: str


def resolve_credentials(env: Mapping[str, str]) -> Credentials | None:
    """All five, or none.

    None set means "no Store automation configured", which callers treat as a
    documented skip. Some set is a mistake, and skipping there would ship a
    release that quietly never reached the Store - so it raises while the
    secrets are still the obvious thing to look at.
    """
    values = {name: env.get(name, "").strip() for name in CREDENTIAL_VARS}
    missing = [name for name, value in values.items() if not value]
    if len(missing) == len(CREDENTIAL_VARS):
        return None
    if missing:
        raise StoreError(
            f"Store submission needs all five STORE_* secrets - unset: {', '.join(missing)}"
        )
    return Credentials(
        tenant_id=values["STORE_TENANT_ID"],
        client_id=values["STORE_CLIENT_ID"],
        client_secret=values["STORE_CLIENT_SECRET"],
        seller_id=values["STORE_SELLER_ID"],
        product_id=values["STORE_PRODUCT_ID"],
    )


def http_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, dict]:
    request = urllib.request.Request(
        url, data=body, headers={"User-Agent": USER_AGENT, **headers}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        # The API describes its refusals in the body of a 4xx, so the body is
        # the useful part and the exception is not.
        status, raw = error.code, error.read()
    except urllib.error.URLError as error:
        raise StoreError(f"{method} {url} could not be reached: {error.reason}") from error
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = {"raw": raw.decode("utf-8", "replace")[:500]}
    return status, parsed if isinstance(parsed, dict) else {"raw": parsed}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check_package_url(url: str) -> None:
    """The URL must answer 200 itself. Following a redirect would hide the
    exact thing Partner Center rejects."""
    if not url.startswith("https://"):
        raise StoreError(f"package URL must be https: {url}")
    opener = urllib.request.build_opener(_NoRedirect)
    # Named, because Cloudflare answers 403 to urllib's default User-Agent and
    # that would read as a broken URL when it is only a blocked client.
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=60) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except urllib.error.URLError as error:
        raise StoreError(f"{url} could not be reached: {error.reason}") from error
    if status != 200:
        raise StoreError(
            f"{url} answered {status}, not 200. Partner Center rejects a package "
            "URL that redirects or is missing."
        )


def describe_errors(payload: dict) -> str:
    errors = payload.get("errors") or []
    lines = [
        f"{e.get('code', '?')}: {e.get('message', '')}"
        + (f" [{e['target']}]" if e.get("target") else "")
        for e in errors
        if isinstance(e, dict)
    ]
    if lines:
        return "; ".join(lines)
    return json.dumps(payload)[:500] if payload else "(empty response)"


class StoreClient:
    def __init__(self, credentials: Credentials, transport: Transport = http_transport):
        self.credentials = credentials
        self.transport = transport
        self._token: str | None = None

    def token(self) -> str:
        if self._token is None:
            c = self.credentials
            body = urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": c.client_id,
                "client_secret": c.client_secret,
                "scope": SCOPE,
            }).encode()
            status, payload = self.transport(
                "POST",
                TOKEN_URL.format(tenant=c.tenant_id),
                {"Content-Type": "application/x-www-form-urlencoded"},
                body,
            )
            token = payload.get("access_token")
            if status != 200 or not token:
                # error_description is Entra's own wording (expired secret,
                # unknown client) and never echoes the secret back.
                reason = payload.get("error_description") or payload.get("error") or "no token"
                raise StoreError(f"could not authenticate to the Store API ({status}): {reason}")
            self._token = token
        return self._token

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        """One API call, returning `responseData`. Raises on a refusal."""
        payload = self.call_raw(method, path, body)
        return payload.get("responseData") or {}

    def call_raw(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token()}",
            "X-Seller-Account-Id": self.credentials.seller_id,
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        status, payload = self.transport(method, f"{API}{path}", headers, data)
        if status >= 400 or payload.get("isSuccess") is False:
            raise StoreError(f"{method} {path} failed ({status}): {describe_errors(payload)}")
        # A success can still carry warnings, and a warning about the listing
        # is worth reading before certification reads it for you.
        for error in payload.get("errors") or []:
            if isinstance(error, dict):
                print(f"  note from the Store - {describe_errors({'errors': [error]})}")
        return payload


def wait_until_ready(
    client: StoreClient,
    product: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout: float = POLL_TIMEOUT,
) -> None:
    deadline = clock() + timeout
    while True:
        payload = client.call_raw("GET", f"/submission/v1/product/{product}/status")
        upload_errors = [
            e for e in payload.get("errors") or []
            if isinstance(e, dict) and e.get("code") == "packageuploaderror"
        ]
        if upload_errors:
            raise StoreError(
                "the Store could not take the package: "
                + describe_errors({"errors": upload_errors})
            )
        if (payload.get("responseData") or {}).get("isReady"):
            return
        if clock() >= deadline:
            raise StoreError(
                f"the draft was still not ready after {int(timeout // 60)} minutes. "
                "Nothing was submitted; check the draft in Partner Center."
            )
        sleep(POLL_INTERVAL)


def submit(client: StoreClient, url: str, *, force: bool = False, **poll) -> str | None:
    """Point the draft at `url` and submit it. Returns the submission id, or
    None when the draft was already there and nothing was done."""
    product = client.credentials.product_id
    base = f"/submission/v1/product/{product}"

    status = client.call("GET", f"{base}/status")
    ongoing = status.get("ongoingSubmissionId")
    if ongoing:
        raise StoreError(
            f"submission {ongoing} is still in flight, and the Store takes one at a "
            "time. Wait for it to finish (or cancel it in Partner Center) and rerun."
        )

    packages = client.call("GET", f"{base}/packages").get("packages") or []
    if len(packages) != 1:
        # One installer, one architecture is what the first submission set
        # up. Anything else was changed by hand, and guessing which package
        # a new URL belongs to is not this script's call.
        raise StoreError(
            f"expected exactly one package in the draft, found {len(packages)}. "
            "Update this submission in Partner Center."
        )
    package = packages[0]

    if package.get("packageUrl") == url and not force:
        print(f"The draft already points at {url} - nothing to submit.")
        print("If an earlier run stopped before submitting, rerun with --force.")
        return None

    print(f"Package URL: {package.get('packageUrl')} -> {url}")
    client.call("PUT", f"{base}/packages", {"packages": [{**package, "packageUrl": url}]})
    client.call("POST", f"{base}/packages/commit")

    print("Waiting for the Store to fetch and scan the installer...", flush=True)
    wait_until_ready(client, product, **poll)

    submission = client.call("POST", f"{base}/submit").get("submissionId")
    if not submission:
        raise StoreError("the Store accepted the submission but returned no id")
    return submission


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Submit a released installer to the Microsoft Store.")
    parser.add_argument("--url", required=True,
                        help="versioned, direct URL of the signed installer")
    parser.add_argument("--require", action="store_true",
                        help="fail instead of skipping when no credentials are set")
    parser.add_argument("--force", action="store_true",
                        help="submit even if the draft already points at this URL")
    args = parser.parse_args(argv)

    try:
        credentials = resolve_credentials(os.environ)
        if credentials is None:
            if args.require:
                raise StoreError(
                    f"no Store credentials - set {', '.join(CREDENTIAL_VARS)}.")
            print("No STORE_* secrets - skipping. The release is published, but the "
                  "Store still needs this version submitted by hand.")
            return 0

        check_package_url(args.url)
        submission = submit(StoreClient(credentials), args.url, force=args.force)
    except StoreError as error:
        print(f"::error::{error}" if os.environ.get("GITHUB_ACTIONS") else f"error: {error}",
              file=sys.stderr)
        return 1

    if submission is None:
        return 0

    print(f"Submitted for certification: submission {submission}.")
    print("Certification takes hours to days; the result arrives by email and in "
          "Partner Center.")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Microsoft Store\nSubmission `{submission}` sent for "
                    f"certification with `{args.url}`\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

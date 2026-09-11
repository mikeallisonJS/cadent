"""Authenticode-sign the Windows build: payload PE files, then the installer.

The Microsoft Store's MSI/EXE submission path requires that the installer
"and all of its Portable Executable (PE) files" chain to a CA in the Microsoft
Trusted Root Program. That is a stronger claim than "sign the installer": a
PyInstaller onedir ships hundreds of .dll and .pyd files out of third-party
wheels, and the unsigned ones are part of the submitted binary.

So this runs twice per release, on either side of Inno Setup:

    python scripts/sign_windows.py dist/Cadent           # before ISCC
    python scripts/sign_windows.py dist/installer/*.exe  # after ISCC

Signing the payload first is not an ordering preference. Inno compresses the
payload into the installer, so a file signed afterwards is signed in dist/ and
unsigned in the artefact anyone actually downloads.

Credentials come from the environment, and their absence is a supported state
(see `resolve_signer`) - the macOS leg builds ad-hoc signed when no Developer
ID secrets are set, and an unsigned Windows build stays just as buildable.
What changes is that an unsigned build is not Store-submittable and keeps the
SmartScreen warning the M3 charter accepted.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# What Authenticode can carry. PyInstaller also collects .zip, .onnx, .json and
# Qt resources, and handing those to signtool is an error rather than a no-op.
PE_SUFFIXES = {".exe", ".dll", ".pyd", ".sys", ".ocx", ".cpl", ".scr"}

# signtool takes a file list per invocation, and batching is the difference
# between one Azure round trip and a thousand. Kept well under the ~32k command
# line limit: these are long absolute paths inside _internal/.
BATCH = 100


@dataclass(frozen=True)
class Signer:
    """How to prove identity to signtool, plus the timestamp authority.

    `args` is spliced in ahead of the file list. Timestamping is not optional:
    without it every signature expires when the certificate does, and a release
    already in the Store would start failing validation on a date nobody set.
    """

    args: list[str]
    timestamp_url: str
    description: str


def resolve_signer() -> Signer | None:
    """Pick a signing mechanism from the environment, newest option first.

    Three, because they serve three situations and only the first is the one a
    release goes out with:

    * Azure Artifact Signing (formerly Trusted Signing) - the release path.
      The private key never exists locally; signtool calls out through a dlib
      and Azure returns a short-lived certificate. Auth is DefaultAzureCredential
      inside that dlib, so AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_CLIENT_SECRET
      are read by it and never by us.
    * A .pfx - any conventional OV/EV certificate exported to a file, which is
      what a CI secret can hold.
    * A thumbprint - a certificate already in the local user's store, which is
      the only one of the three that works on a developer's machine without
      handing that machine a key file.

    None means "no signing configured", which callers treat as a documented
    skip rather than a failure.
    """
    metadata = os.environ.get("CADENT_SIGN_METADATA")
    if metadata:
        dlib = os.environ.get("CADENT_SIGN_DLIB")
        if not dlib:
            raise SystemExit(
                "CADENT_SIGN_METADATA is set but CADENT_SIGN_DLIB is not - Azure "
                "Artifact Signing needs the path to Azure.CodeSigning.Dlib.dll."
            )
        return Signer(
            args=["/dlib", dlib, "/dmdf", metadata],
            # Artifact Signing's certificates are valid for minutes, not years:
            # the timestamp is what makes the signature outlive them, so this
            # authority in particular is load-bearing rather than hygiene.
            timestamp_url=os.environ.get(
                "CADENT_SIGN_TIMESTAMP", "http://timestamp.acs.microsoft.com"),
            description="Azure Artifact Signing",
        )

    pfx = os.environ.get("CADENT_SIGN_PFX")
    if pfx:
        args = ["/f", pfx]
        password = os.environ.get("CADENT_SIGN_PFX_PASSWORD")
        if password:
            args += ["/p", password]
        return Signer(args=args,
                      timestamp_url=os.environ.get(
                          "CADENT_SIGN_TIMESTAMP",
                          "http://timestamp.digicert.com"),
                      description=f"certificate file {Path(pfx).name}")

    thumbprint = os.environ.get("CADENT_SIGN_THUMBPRINT")
    if thumbprint:
        return Signer(args=["/sha1", thumbprint],
                      timestamp_url=os.environ.get(
                          "CADENT_SIGN_TIMESTAMP",
                          "http://timestamp.digicert.com"),
                      description=f"store certificate {thumbprint[:8]}")

    return None


def find_signtool() -> str:
    """Locate signtool.exe, preferring the newest Windows SDK on the machine.

    Not on PATH by default, including on GitHub's windows runners. The SDK
    installs one per version, and older ones predate some of the flags used
    here (/tr in particular), so the sort is a compatibility floor and not a
    tidiness preference.

    CADENT_SIGNTOOL overrides the search outright, because "newest on the
    machine" and "new enough for the dlib" are not the same question: Artifact
    Signing loads through /dlib, and signtool builds older than the client
    library fail that handshake with a bare 0x80004005 rather than anything
    naming the version. Pointing at a NuGet-supplied signtool is the fix, and
    it needs a way in that doesn't depend on what the runner image ships.
    """
    from shutil import which

    override = os.environ.get("CADENT_SIGNTOOL")
    if override:
        if not Path(override).is_file():
            raise SystemExit(f"CADENT_SIGNTOOL points at nothing: {override}")
        return override

    found = which("signtool")
    if found:
        return found

    roots = [Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
             Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))]
    candidates: list[Path] = []
    for root in roots:
        candidates += list((root / "Windows Kits" / "10" / "bin").glob("*/x64/signtool.exe"))

    if not candidates:
        raise SystemExit(
            "signtool.exe not found - install the Windows SDK signing tools "
            "(the 'Windows SDK Signing Tools for Desktop Apps' component)."
        )
    # Version directories sort lexically in release order (10.0.22621.0 ...).
    return str(sorted(candidates, key=lambda path: path.parent.parent.name)[-1])


def already_signed(signtool: str, path: Path) -> bool:
    """True when the file carries a signature that already satisfies the Store.

    Worth asking per file rather than signing everything unconditionally, for
    two reasons that point the same way. Most of the payload - Qt, CPython,
    parts of the ONNX and CUDA stacks - arrives signed by its vendor, and those
    signatures chain to the same trusted roots the Store checks; replacing them
    buys nothing and quietly erases the vendor's provenance. And Artifact
    Signing bills per signature, so re-signing an already-valid PySide6 tree on
    every release is a recurring charge for no change in the artefact.

    /pa is the Authenticode policy (the Store's question), not the driver
    policy signtool defaults to - the default would reject perfectly good
    user-mode DLLs and send us re-signing the whole tree.
    """
    return subprocess.run([signtool, "verify", "/pa", "/q", str(path)],
                          capture_output=True).returncode == 0


def candidates(targets: list[Path]) -> list[Path]:
    """Expand the arguments into PE files, directories walked recursively."""
    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files += [path for path in sorted(target.rglob("*"))
                      if path.is_file() and path.suffix.lower() in PE_SUFFIXES]
        elif target.is_file():
            files.append(target)
        else:
            raise SystemExit(f"no such file or directory: {target}")
    return files


def sign(signtool: str, signer: Signer, batch: list[Path]) -> None:
    command = [signtool, "sign", *signer.args,
               # SHA-256 throughout. SHA-1 signatures are no longer accepted by
               # the Store, and /td is a separate flag from /fd - a file digest
               # of SHA-256 with a default-SHA-1 timestamp is a real and easy
               # way to produce a signature that fails validation.
               "/fd", "SHA256",
               "/tr", signer.timestamp_url, "/td", "SHA256",
               *[str(path) for path in batch]]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        # signtool puts the useful line on stdout and a generic one on stderr.
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"signtool failed on a batch of {len(batch)} file(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", type=Path,
                        help="files to sign, or directories to walk for PE files")
    parser.add_argument("--require", action="store_true",
                        help="fail instead of skipping when no credentials are set")
    parser.add_argument("--force", action="store_true",
                        help="re-sign files that already carry a valid signature")
    args = parser.parse_args()

    signer = resolve_signer()
    if signer is None:
        if args.require:
            raise SystemExit(
                "no signing credentials - set CADENT_SIGN_METADATA (+ CADENT_SIGN_DLIB), "
                "CADENT_SIGN_PFX or CADENT_SIGN_THUMBPRINT."
            )
        print("No signing credentials - leaving the build unsigned. "
              "It will run, but SmartScreen will warn and the Store will reject it.")
        return 0

    signtool = find_signtool()
    files = candidates(args.targets)
    if not files:
        raise SystemExit(f"no PE files found under {', '.join(map(str, args.targets))}")

    pending = files if args.force else [
        path for path in files if not already_signed(signtool, path)]
    skipped = len(files) - len(pending)

    print(f"Signing {len(pending)} file(s) with {signer.description}"
          f"{f' ({skipped} already signed)' if skipped else ''}.", flush=True)

    for start in range(0, len(pending), BATCH):
        batch = pending[start:start + BATCH]
        sign(signtool, signer, batch)
        print(f"  {min(start + BATCH, len(pending))}/{len(pending)}", flush=True)

    # Verifying after the fact rather than trusting signtool's exit code: a
    # batch that signed but failed to timestamp is the failure this catches,
    # and it is invisible until a certificate expires months later.
    unverified = [path for path in pending if not already_signed(signtool, path)]
    if unverified:
        print("SIGNING INCOMPLETE - these files still fail verification:",
              file=sys.stderr)
        for path in unverified[:20]:
            print(f"  {path}", file=sys.stderr)
        return 1

    print(f"OK: {len(pending)} file(s) signed and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

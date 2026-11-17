# Compliance VPN Client

A minimal Windows desktop manager for VPN profiles you provide and are authorized to use. It uses Windows' built-in VPN support; it does not provide VPN servers, accounts, or a VPN service.

## Open on Windows

The app is currently source code, not an installed program or packaged `.exe`. Download or clone this repository, then double-click **`start-vpn-client.bat`** in the repository folder. The launcher tries the Windows Python launcher (`py -3`), then `python`.

Requires Windows 10 or later and Python 3.10+ with Tcl/Tk (the standard Python installer includes Tcl/Tk):

```powershell
cd path\to\the\repository
python vpn_client.py
```

The desktop shortcut may launch `pythonw.exe`, which hides console errors. If it does not open or reports an error, open PowerShell in the repository folder and launch with `python.exe` so the full error remains visible:

```powershell
python.exe .\vpn_client.py
```

If `python.exe` is not recognized, use `py -3 .\vpn_client.py` or install Python from [python.org](https://www.python.org/downloads/windows/) with the Python launcher and Tcl/Tk enabled. This repository does not yet include an `.exe` installer.

## First use and limitations

Opening the app does not create an account or provide a VPN service. To connect, you must already have an authorized VPN server address and authentication details from your organization/provider, plus a compatible IKEv2 or SSTP configuration. Without a valid configuration and a reachable server, the app cannot connect.

On first launch, the profile line says **No profile loaded** and the status asks you to import an authorized VPN configuration; this is expected. Choose **Import config**, select a JSON file in the documented format below, and confirm installing the Windows user profile. Then use **Connect** and **Disconnect**. The app does not include a profile or create a VPN service. **Create desktop shortcut** adds a quick launcher for this app. Credentials are not accepted in the JSON file or passed by this app; configure authentication through Windows VPN settings. Failures are shown immediately and are not automatically retried. While connected, the app checks local tunnel status every five seconds and alerts on a detected disconnect; it never auto-reconnects and cannot guarantee a connection will never drop. **Diagnose / repair** compares the local Windows profile with the imported settings and asks before restoring a mismatch.

Example configuration:

```json
{
  "name": "Company VPN",
  "server_address": "vpn.example.com",
  "tunnel_type": "IKEv2",
  "authentication_method": "Eap"
}
```

Supported tunnel types are `IKEv2` and `SSTP`. Authentication methods are `Eap` and `MachineCertificate` for IKEv2, and those plus `MSChapv2` for SSTP. The profile is installed for the current Windows user and its non-secret settings are saved under `%LOCALAPPDATA%\ComplianceVpnClient`.

## Signed releases and update limits

After `.github/workflows/release-signing.yml` is merged to the default branch, publishing a GitHub Release from a tag triggers the workflow. It first runs the VPN client's unit tests on Windows, then uses GitHub Actions OIDC to create keyless Sigstore signatures for GitHub's tag source archives. It verifies them against this repository's exact workflow identity and GitHub's OIDC issuer before attaching the archives and `.sigstore.json` bundles to the Release. Actions are pinned to full commit SHAs; no signing secret or private key is stored in this repository. Repository Actions must be allowed to publish release assets.

To verify a downloaded archive manually, install the Sigstore CLI (`python -m pip install sigstore`) and run, replacing `<tag>` with the release tag:

```powershell
sigstore verify identity --bundle <tag>.zip.sigstore.json --cert-identity "https://github.com/zhoushengyu0214-jpg/carljohn/.github/workflows/release-signing.yml@refs/tags/<tag>" --cert-oidc-issuer https://token.actions.githubusercontent.com <tag>.zip
```

The desktop app does not yet check for, download, verify, or install updates. The signing workflow establishes a release-signing foundation, not an app updater. Updates remain disabled until the app has a tested verifier that rejects a missing/invalid bundle and enforces the expected workflow identity and issuer, followed by an explicit user confirmation before installation.

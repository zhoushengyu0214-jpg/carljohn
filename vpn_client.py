import base64
import ipaddress
import json
import os
import re
import subprocess
import sys
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


_TUNNEL_TYPES = {"ikev2": "Ikev2", "sstp": "Sstp"}
_AUTH_METHODS = {
    "eap": "Eap",
    "machinecertificate": "MachineCertificate",
    "mschapv2": "MSChapv2",
}
_ALLOWED_AUTH = {
    "Ikev2": {"Eap", "MachineCertificate"},
    "Sstp": {"Eap", "MachineCertificate", "MSChapv2"},
}
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_PROFILE_FIELDS = {
    "name",
    "server_address",
    "tunnel_type",
    "authentication_method",
}


class VpnOperationError(Exception):
    pass


@dataclass(frozen=True)
class VpnProfile:
    name: str
    server_address: str
    tunnel_type: str
    authentication_method: str = "Eap"

    @classmethod
    def from_mapping(cls, data):
        if not isinstance(data, dict):
            raise ValueError("The configuration must be a JSON object.")
        if set(data) - _PROFILE_FIELDS:
            raise ValueError("The configuration contains unsupported fields.")
        if not {"name", "server_address", "tunnel_type"}.issubset(data):
            raise ValueError("Required fields: name, server_address, tunnel_type.")

        name = data["name"]
        server = data["server_address"]
        tunnel_value = data["tunnel_type"]
        auth_value = data.get("authentication_method", "Eap")
        if not all(isinstance(value, str) for value in (name, server, tunnel_value, auth_value)):
            raise ValueError("Profile fields must be strings.")

        name = name.strip()
        server = server.strip()
        if not name or len(name) > 128 or any(ord(char) < 32 for char in name):
            raise ValueError("Profile name must contain 1-128 printable characters.")
        if not _is_server_address(server):
            raise ValueError("Server address must be a hostname or IP address, without a scheme or port.")

        tunnel_type = _TUNNEL_TYPES.get(tunnel_value.casefold())
        if tunnel_type is None:
            raise ValueError("Supported tunnel types are IKEv2 and SSTP.")
        authentication_method = _AUTH_METHODS.get(auth_value.casefold())
        if authentication_method not in _ALLOWED_AUTH[tunnel_type]:
            raise ValueError(f"{auth_value!r} is not supported with {tunnel_type}.")

        return cls(name, server, tunnel_type, authentication_method)


def _is_server_address(value):
    if not value or len(value) > 253 or any(char.isspace() for char in value):
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    hostname = value[:-1] if value.endswith(".") else value
    labels = hostname.split(".")
    return all(_HOST_LABEL.fullmatch(label) for label in labels)


def _run_powershell(script, input_text, timeout=30):
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    encoded_input = base64.b64encode(input_text.encode("utf-8")).decode("ascii")
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded_script,
            ],
            input=encoded_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VpnOperationError(f"Could not run Windows VPN management: {error}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise VpnOperationError(detail or "Windows VPN management failed.")
    return result.stdout.strip()


class WindowsVpn:
    def install_profile(self, profile):
        script = r"""
$json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd()))
$data = $json | ConvertFrom-Json
$params = @{
    Name = $data.name
    ServerAddress = $data.server_address
    TunnelType = $data.tunnel_type
    AuthenticationMethod = @($data.authentication_method)
    EncryptionLevel = 'Required'
    RememberCredential = $false
}
$existing = Get-VpnConnection -Name $data.name -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    Add-VpnConnection @params -Force -ErrorAction Stop | Out-Null
} else {
    Set-VpnConnection @params -Force -ErrorAction Stop | Out-Null
}
"""
        _run_powershell(script, json.dumps(asdict(profile)))

    def connect(self, profile):
        self._run_rasdial(["rasdial.exe", profile.name])

    def disconnect(self, profile):
        self._run_rasdial(["rasdial.exe", profile.name, "/disconnect"])

    def diagnose(self, profile):
        script = r"""
$json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd()))
$name = $json | ConvertFrom-Json
$vpn = Get-VpnConnection -Name $name -ErrorAction Stop
[pscustomobject]@{
    Name = [string]$vpn.Name
    ServerAddress = [string]$vpn.ServerAddress
    TunnelType = [string]$vpn.TunnelType
    AuthenticationMethod = @($vpn.AuthenticationMethod | ForEach-Object { [string]$_ })
    ConnectionStatus = [string]$vpn.ConnectionStatus
} | ConvertTo-Json -Compress
"""
        output = _run_powershell(script, json.dumps(profile.name), timeout=10)
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise VpnOperationError("Windows returned an unreadable VPN profile status.") from error

    @staticmethod
    def _run_rasdial(command):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise VpnOperationError(f"Could not run Windows VPN connection: {error}") from error
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise VpnOperationError(detail or "Windows VPN connection failed.")


def create_desktop_shortcut():
    python_path = Path(sys.executable)
    if python_path.name.casefold() == "python.exe":
        windowed_python = python_path.with_name("pythonw.exe")
        if windowed_python.exists():
            python_path = windowed_python
    app_path = Path(__file__).resolve()
    script = r"""
$json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd()))
$data = $json | ConvertFrom-Json
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut(
    (Join-Path $desktop 'Compliance VPN Client.lnk')
)
$shortcut.TargetPath = $data.python
$shortcut.Arguments = '"' + $data.app + '"'
$shortcut.WorkingDirectory = $data.directory
$shortcut.Description = 'Compliance VPN Client'
$shortcut.Save()
"""
    _run_powershell(
        script,
        json.dumps(
            {
                "python": str(python_path),
                "app": str(app_path),
                "directory": str(app_path.parent),
            }
        ),
    )


def _settings_path():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise VpnOperationError("Windows local application data is unavailable.")
    return Path(base) / "ComplianceVpnClient" / "profile.json"


class VpnClientApp:
    MONITOR_INTERVAL_MS = 5000

    def __init__(self, root):
        self.root = root
        self.vpn = WindowsVpn()
        self.profile = None
        self.monitoring_connection = False
        self.status = tk.StringVar(
            value="No profile loaded. Import an authorized VPN configuration to begin."
        )
        self.profile_summary = tk.StringVar(value="No profile loaded")

        root.title("Compliance VPN Client")
        root.minsize(480, 250)
        root.resizable(False, False)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Windows VPN Manager", font=("", 16, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Uses a VPN profile you provide and are authorized to use.",
            wraplength=430,
        ).pack(anchor="w", pady=(6, 18))
        ttk.Label(frame, textvariable=self.profile_summary).pack(anchor="w")
        ttk.Label(frame, textvariable=self.status, wraplength=430).pack(anchor="w", pady=(10, 18))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Import config", command=self.import_profile).pack(side="left")
        ttk.Button(buttons, text="Connect", command=self.connect).pack(side="left", padx=8)
        ttk.Button(buttons, text="Disconnect", command=self.disconnect).pack(side="left")
        tools = ttk.Frame(frame)
        tools.pack(fill="x", pady=(12, 0))
        ttk.Button(tools, text="Diagnose / repair", command=self.diagnose).pack(side="left")
        ttk.Button(tools, text="Create desktop shortcut", command=self.make_shortcut).pack(
            side="left", padx=8
        )
        ttk.Button(tools, text="VPN settings", command=self.open_vpn_settings).pack(side="right")

        self._load_saved_profile()

    def _load_saved_profile(self):
        try:
            data = json.loads(_settings_path().read_text(encoding="utf-8"))
            self.profile = VpnProfile.from_mapping(data)
            self._show_profile()
            try:
                state = self.vpn.diagnose(self.profile)
            except VpnOperationError as error:
                self.status.set(f"Profile loaded; connection status unavailable: {error}")
                messagebox.showerror("VPN status unavailable", str(error), parent=self.root)
                return
            connection_status = str(state.get("ConnectionStatus", "Unknown")).casefold()
            self.status.set(f"VPN status: {connection_status}.")
            if connection_status == "connected":
                self.monitoring_connection = True
                self.root.after(self.MONITOR_INTERVAL_MS, self._check_connection)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError, ValueError, VpnOperationError) as error:
            messagebox.showerror("Could not load VPN profile", str(error), parent=self.root)

    def import_profile(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Import VPN configuration",
            filetypes=[("JSON configuration", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            profile = VpnProfile.from_mapping(data)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            messagebox.showerror("Invalid VPN configuration", str(error), parent=self.root)
            return
        if not messagebox.askyesno(
            "Install VPN profile",
            f"Install or update {profile.name!r} for this Windows user?",
            parent=self.root,
        ):
            return
        try:
            self.vpn.install_profile(profile)
        except VpnOperationError as error:
            messagebox.showerror("Could not install VPN profile", str(error), parent=self.root)
            return
        self.profile = profile
        self._show_profile()
        try:
            settings = _settings_path()
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")
        except OSError as error:
            messagebox.showerror(
                "Profile installed, but not saved",
                f"The Windows VPN profile was installed, but the client could not save its local settings: {error}",
                parent=self.root,
            )
            return
        self.status.set("Profile installed. Connect only when you choose.")

    def connect(self):
        if not self._require_profile():
            return
        self.monitoring_connection = False
        try:
            self.vpn.connect(self.profile)
        except VpnOperationError as error:
            self.status.set(f"Connection failed: {error}")
            messagebox.showerror("Connection failed", str(error), parent=self.root)
            return
        self.status.set("Connected.")
        self.monitoring_connection = True
        self.root.after(self.MONITOR_INTERVAL_MS, self._check_connection)

    def disconnect(self):
        if not self._require_profile():
            return
        self.monitoring_connection = False
        try:
            self.vpn.disconnect(self.profile)
        except VpnOperationError as error:
            self.status.set(f"Disconnection failed: {error}")
            messagebox.showerror("Disconnection failed", str(error), parent=self.root)
            return
        self.status.set("Disconnected.")

    def _check_connection(self):
        if not self.monitoring_connection:
            return
        try:
            state = self.vpn.diagnose(self.profile)
        except VpnOperationError as error:
            self.monitoring_connection = False
            self.status.set(f"Connection monitoring stopped: {error}")
            messagebox.showerror(
                "VPN status unavailable",
                f"Monitoring stopped and no reconnection was attempted.\n\n{error}",
                parent=self.root,
            )
            return

        connection_status = str(state.get("ConnectionStatus", "Unknown")).casefold()
        if connection_status == "connected":
            self.root.after(self.MONITOR_INTERVAL_MS, self._check_connection)
            return

        self.monitoring_connection = False
        if connection_status != "disconnected":
            report = f"Connection monitoring stopped: unexpected Windows status {connection_status!r}."
            self.status.set(report)
            messagebox.showerror(
                "VPN status unavailable",
                f"{report}\nNo reconnection was attempted.",
                parent=self.root,
            )
            return
        self.status.set(f"VPN disconnected unexpectedly (status: {connection_status}).")
        messagebox.showerror(
            "VPN disconnected",
            "The VPN tunnel is no longer connected. No reconnection was attempted.",
            parent=self.root,
        )

    def diagnose(self):
        if not self._require_profile():
            return
        try:
            actual = self.vpn.diagnose(self.profile)
        except VpnOperationError as error:
            self.status.set(f"Diagnosis failed: {error}")
            messagebox.showerror("VPN diagnosis failed", str(error), parent=self.root)
            return

        actual_auth = actual.get("AuthenticationMethod", [])
        if isinstance(actual_auth, str):
            actual_auth = [actual_auth]
        matches = (
            actual.get("ServerAddress") == self.profile.server_address
            and str(actual.get("TunnelType", "")).casefold() == self.profile.tunnel_type.casefold()
            and self.profile.authentication_method.casefold()
            in {str(method).casefold() for method in actual_auth}
        )
        connection_status = actual.get("ConnectionStatus", "Unknown")
        if matches:
            report = f"Profile settings are intact. Windows connection status: {connection_status}."
            self.status.set(report)
            messagebox.showinfo("VPN diagnosis", report, parent=self.root)
            return

        report = "The Windows profile differs from the imported configuration."
        self.status.set(report)
        if not messagebox.askyesno(
            "Repair VPN profile",
            f"{report}\n\nRestore the imported settings for this Windows user?",
            parent=self.root,
        ):
            return
        try:
            self.vpn.install_profile(self.profile)
        except VpnOperationError as error:
            self.status.set(f"Repair failed: {error}")
            messagebox.showerror("VPN repair failed", str(error), parent=self.root)
            return
        self.status.set("VPN profile repaired. No connection retry was started.")
        messagebox.showinfo("VPN profile repaired", self.status.get(), parent=self.root)

    def make_shortcut(self):
        try:
            create_desktop_shortcut()
        except VpnOperationError as error:
            messagebox.showerror("Could not create desktop shortcut", str(error), parent=self.root)
            return
        messagebox.showinfo(
            "Desktop shortcut created",
            "Compliance VPN Client is now available from your desktop.",
            parent=self.root,
        )

    def open_vpn_settings(self):
        try:
            os.startfile("ms-settings:network-vpn")
        except OSError as error:
            messagebox.showerror("Could not open VPN settings", str(error), parent=self.root)

    def _require_profile(self):
        if self.profile is None:
            messagebox.showerror("No VPN profile", "Import a VPN configuration first.", parent=self.root)
            return False
        return True

    def _show_profile(self):
        self.profile_summary.set(
            f"{self.profile.name}  |  {self.profile.server_address}  |  {self.profile.tunnel_type}"
        )


def main():
    if sys.platform != "win32":
        print("Compliance VPN Client requires Windows.", file=sys.stderr)
        return 1
    root = tk.Tk()
    VpnClientApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

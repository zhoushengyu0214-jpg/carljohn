import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import vpn_client


class VpnProfileTests(unittest.TestCase):
    def test_imports_supported_profile_and_applies_default_authentication(self):
        profile = vpn_client.VpnProfile.from_mapping(
            {
                "name": "Company VPN",
                "server_address": "vpn.example.com",
                "tunnel_type": "IKEv2",
            }
        )

        self.assertEqual(profile.tunnel_type, "Ikev2")
        self.assertEqual(profile.authentication_method, "Eap")

    def test_rejects_unsupported_fields_and_unsafe_server_addresses(self):
        base = {
            "name": "Company VPN",
            "server_address": "vpn.example.com",
            "tunnel_type": "SSTP",
        }
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            vpn_client.VpnProfile.from_mapping({**base, "password": "secret"})
        with self.assertRaisesRegex(ValueError, "Server address"):
            vpn_client.VpnProfile.from_mapping({**base, "server_address": "https://vpn.example.com"})

    def test_rejects_authentication_incompatible_with_tunnel(self):
        with self.assertRaisesRegex(ValueError, "not supported"):
            vpn_client.VpnProfile.from_mapping(
                {
                    "name": "Company VPN",
                    "server_address": "vpn.example.com",
                    "tunnel_type": "IKEv2",
                    "authentication_method": "MSChapv2",
                }
            )


class WindowsVpnTests(unittest.TestCase):
    @patch("vpn_client.subprocess.run")
    def test_connect_passes_profile_name_without_credentials(self, run):
        run.return_value.returncode = 0

        vpn_client.WindowsVpn().connect(
            vpn_client.VpnProfile("Company VPN", "vpn.example.com", "Ikev2")
        )

        self.assertEqual(run.call_args.args[0], ["rasdial.exe", "Company VPN"])

    @patch("vpn_client.subprocess.run")
    def test_connection_error_is_reported_without_retry(self, run):
        run.return_value.returncode = 691
        run.return_value.stdout = "Authentication failed"
        run.return_value.stderr = ""

        with self.assertRaisesRegex(vpn_client.VpnOperationError, "Authentication failed"):
            vpn_client.WindowsVpn().connect(
                vpn_client.VpnProfile("Company VPN", "vpn.example.com", "Ikev2")
            )

        run.assert_called_once()

    @patch("vpn_client._run_powershell")
    def test_diagnose_returns_native_profile_state(self, run_powershell):
        run_powershell.return_value = (
            '{"Name":"Company VPN","ServerAddress":"vpn.example.com",'
            '"TunnelType":"Ikev2","AuthenticationMethod":["Eap"],'
            '"ConnectionStatus":"Disconnected"}'
        )
        profile = vpn_client.VpnProfile("Company VPN", "vpn.example.com", "Ikev2")

        state = vpn_client.WindowsVpn().diagnose(profile)

        self.assertEqual(state["ConnectionStatus"], "Disconnected")
        self.assertEqual(json.loads(run_powershell.call_args.args[1]), "Company VPN")

    @patch("vpn_client.messagebox.showerror")
    def test_monitor_alerts_on_disconnect_without_reconnecting(self, showerror):
        app = object.__new__(vpn_client.VpnClientApp)
        app.monitoring_connection = True
        app.profile = vpn_client.VpnProfile("Company VPN", "vpn.example.com", "Ikev2")
        app.vpn = SimpleNamespace(
            diagnose=lambda profile: {"ConnectionStatus": "Disconnected"},
            connect=lambda profile: self.fail("monitor must not reconnect"),
        )
        app.root = SimpleNamespace(after=lambda *args: self.fail("monitor must stop"))
        app.status = SimpleNamespace(set=lambda value: setattr(app, "last_status", value))

        app._check_connection()

        self.assertFalse(app.monitoring_connection)
        self.assertIn("disconnected unexpectedly", app.last_status)
        showerror.assert_called_once()


if __name__ == "__main__":
    unittest.main()

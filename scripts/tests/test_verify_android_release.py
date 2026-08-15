from __future__ import annotations

from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_android_release as verifier  # noqa: E402


VALID_MANIFEST = """\
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    android:versionCode="5" android:versionName="0.5.0-companion"
    package="au.com.ldcs.flexdisplay.rook.companion">
  <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="33" />
  <uses-permission android:name="android.permission.CAMERA" />
  <uses-permission android:name="android.permission.INTERNET" />
  <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
  <uses-permission android:name="android.permission.ACCESS_WIFI_STATE" />
  <uses-permission android:name="android.permission.RECORD_AUDIO" />
  <application android:allowBackup="false">
    <activity android:name="au.com.ldcs.flexdisplay.rook.MainActivity"
        android:exported="true">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
    </activity>
    <service android:name="au.com.ldcs.flexdisplay.rook.CompanionDockTileService"
        android:permission="android.permission.BIND_QUICK_SETTINGS_TILE"
        android:exported="true">
      <intent-filter>
        <action android:name="android.service.quicksettings.action.QS_TILE" />
      </intent-filter>
    </service>
  </application>
</manifest>
"""


def verify(xml: str) -> None:
    verifier._verify_manifest(
        ET.fromstring(xml),
        expected_package=verifier.EXPECTED_PACKAGE,
        expected_version_name="0.5.0-companion",
        expected_version_code=5,
        expected_min_sdk=24,
        expected_target_sdk=33,
    )


class ManifestContractTests(unittest.TestCase):
    def test_exact_manifest_is_accepted(self) -> None:
        verify(VALID_MANIFEST)

    def test_extra_dangerous_permission_is_rejected(self) -> None:
        xml = VALID_MANIFEST.replace(
            "  <application",
            '  <uses-permission android:name="android.permission.READ_SMS" />\n'
            "  <application",
        )
        with self.assertRaises(verifier.VerificationError):
            verify(xml)

    def test_alternate_permission_element_is_rejected(self) -> None:
        xml = VALID_MANIFEST.replace(
            "  <application",
            '  <uses-permission-sdk-23 android:name="android.permission.CAMERA" />\n'
            "  <application",
        )
        with self.assertRaises(verifier.VerificationError):
            verify(xml)

    def test_exported_activity_alias_is_rejected(self) -> None:
        xml = VALID_MANIFEST.replace(
            "  </application>",
            '    <activity-alias android:name=".Alias" android:exported="true" '
            'android:targetActivity="au.com.ldcs.flexdisplay.rook.MainActivity" />\n'
            "  </application>",
        )
        with self.assertRaises(verifier.VerificationError):
            verify(xml)

    def test_receiver_and_provider_are_rejected(self) -> None:
        for component in ("receiver", "provider"):
            with self.subTest(component=component):
                xml = VALID_MANIFEST.replace(
                    "  </application>",
                    f'    <{component} android:name=".Unexpected" />\n  </application>',
                )
                with self.assertRaises(verifier.VerificationError):
                    verify(xml)

    def test_instrumentation_is_rejected(self) -> None:
        xml = VALID_MANIFEST.replace(
            "  <application",
            '  <instrumentation android:name=".Unexpected" />\n  <application',
        )
        with self.assertRaises(verifier.VerificationError):
            verify(xml)

    def test_shared_user_and_intent_data_are_rejected(self) -> None:
        shared_user = VALID_MANIFEST.replace(
            'package="au.com.ldcs.flexdisplay.rook.companion"',
            'package="au.com.ldcs.flexdisplay.rook.companion" '
            'android:sharedUserId="au.com.ldcs.shared"',
        )
        with self.assertRaises(verifier.VerificationError):
            verify(shared_user)
        intent_data = VALID_MANIFEST.replace(
            '        <category android:name="android.intent.category.LAUNCHER" />',
            '        <category android:name="android.intent.category.LAUNCHER" />\n'
            '        <data android:scheme="https" />',
        )
        with self.assertRaises(verifier.VerificationError):
            verify(intent_data)

    def test_debuggable_backup_and_test_only_are_rejected(self) -> None:
        for original, replacement in (
            (
                'android:allowBackup="false"',
                'android:allowBackup="false" android:debuggable="true"',
            ),
            ('android:allowBackup="false"', 'android:allowBackup="true"'),
            (
                'android:allowBackup="false"',
                'android:allowBackup="false" android:testOnly="true"',
            ),
        ):
            with self.subTest(replacement=replacement):
                xml = VALID_MANIFEST.replace(original, replacement)
                with self.assertRaises(verifier.VerificationError):
                    verify(xml)

    def test_target_sdk_downgrade_is_rejected(self) -> None:
        with self.assertRaises(verifier.VerificationError):
            verify(
                VALID_MANIFEST.replace(
                    'android:targetSdkVersion="33"',
                    'android:targetSdkVersion="28"',
                )
            )


if __name__ == "__main__":
    unittest.main()

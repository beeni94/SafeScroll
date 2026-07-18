import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"


def test_manifest_v3_declares_background_popup_and_required_content_scripts():
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "service-worker.js"
    assert manifest["action"]["default_popup"] == "popup.html"
    assert {"storage", "alarms", "tabs"}.issubset(manifest["permissions"])
    scripts = {
        script
        for content_script in manifest["content_scripts"]
        for script in content_script["js"]
    }
    assert {"pairing-bridge.js", "youtube.js"}.issubset(scripts)
    assert "https://www.youtube.com/*" in manifest["host_permissions"]


def test_popup_contains_sprint_five_controls():
    popup = (EXTENSION / "popup.html").read_text(encoding="utf-8")

    for control in (
        "data-account-name",
        "data-mode-name",
        "data-connection-pill",
        "data-last-sync",
        "data-sync-button",
        "data-pause-button",
        "data-dashboard-button",
    ):
        assert control in popup


def test_background_client_covers_all_automatic_sync_triggers():
    worker = (EXTENSION / "service-worker.js").read_text(encoding="utf-8")
    bridge = (EXTENSION / "pairing-bridge.js").read_text(encoding="utf-8")

    for trigger in (
        "onStartup",
        "onAlarm",
        "YOUTUBE_OPENED",
        "CONFIGURATION_CHANGED",
        "SYNC_NOW",
    ):
        assert trigger in worker
    assert "SAFESCROLL_PAIR" in bridge
    assert "chrome.storage.local" in worker

import io
import unittest

from scare.web import create_app

from .helpers import AppTestCase


class WebTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.ctl.speaker.scare = lambda text, mode: True
        self.client = create_app(self.ctl).test_client()

    def upload(self, kind, *names, mode="kids"):
        files = [(io.BytesIO(b"RIFF"), n) for n in names]
        return self.client.post(f"/api/sounds/{mode}/{kind}", data={"files": files},
                                content_type="multipart/form-data")

    def test_page_loads_and_is_not_cached(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_status(self):
        s = self.client.get("/api/status").get_json()
        self.assertEqual(s["mode"], "scary")
        self.assertEqual(list(s["modes"]), ["scary", "kids", "auto", "off"])
        self.assertFalse(s["camera"])
        self.assertEqual(s["messages_pending"], 4)

    def test_no_camera_stream(self):
        self.assertEqual(self.client.get("/stream.mjpg").status_code, 503)

    def test_settings_are_clamped(self):
        s = self.client.post("/api/settings", json={"volume": 5, "mode": "kids"}).get_json()
        self.assertEqual(s["volume"], 1.5)
        self.assertEqual(s["base_mode"], "kids")

    def test_bad_settings_are_a_400_not_a_crash(self):
        for body in ({"nope": 1}, {"mode": "gore"}, {"volume": "loud"}):
            r = self.client.post("/api/settings", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn("error", r.get_json())

    def test_arm_and_disarm(self):
        self.assertFalse(self.client.post("/api/arm", json={"armed": False}).get_json()["armed"])
        self.assertTrue(self.client.post("/api/arm", json={"armed": True}).get_json()["armed"])

    def test_test_button(self):
        self.assertTrue(self.client.post("/api/test", json={"text": "Boo"}).get_json()["started"])
        self.client.post("/api/settings", json={"mode": "off"})
        self.assertFalse(self.client.post("/api/test", json={}).get_json()["started"])

    def test_calibrate_without_camera_is_a_400(self):
        self.assertEqual(self.client.post("/api/calibrate", json={"height_cm": 180}).status_code, 400)

    def test_edit_messages_per_mode(self):
        r = self.client.post("/api/messages?mode=kids", json={"text": "Hei\nHallo\n\n"})
        self.assertEqual(r.get_json()["count"], 2)
        self.assertEqual(self.client.get("/api/messages?mode=kids").get_json()["text"], "Hei\nHallo\n")
        self.assertIn("scary one", self.client.get("/api/messages?mode=scary").get_json()["text"])
        self.assertEqual(self.client.get("/api/messages?mode=../..").status_code, 400)

    def test_upload_keeps_only_sound_files(self):
        r = self.upload("scare", "boo.wav", "evil.sh", "song.MP3")
        self.assertEqual(r.get_json()["saved"], ["boo.wav", "song.MP3"])
        self.assertEqual([p.name for p in self.ctl.speaker.scare_sounds("kids")], ["boo.wav", "song.MP3"])

    def test_upload_cannot_escape_the_sound_folder(self):
        r = self.upload("ambient", "../../../config.toml.wav")
        self.assertEqual(r.get_json()["saved"], ["config.toml.wav"])
        self.assertTrue((self.root / "modes/kids/ambient/config.toml.wav").exists())
        self.assertEqual(self.upload("music", "a.wav").status_code, 400)
        self.assertEqual(self.upload("scare", "a.wav", mode="nope").status_code, 400)

    def test_preview_and_delete(self):
        self.upload("scare", "boo.wav")
        with self.client.get("/sounds/kids/scare/boo.wav") as r:
            self.assertEqual(r.data, b"RIFF")
        self.assertEqual(self.client.delete("/api/sounds/kids/scare/boo.wav").status_code, 200)
        self.assertEqual(self.ctl.speaker.scare_sounds("kids"), [])
        self.assertEqual(self.client.get("/sounds/kids/scare/boo.wav").status_code, 404)

    def test_preview_cannot_read_other_files(self):
        self.assertEqual(self.client.get("/sounds/kids/scare/..%2Fmessages.txt").status_code, 404)
        self.assertEqual(self.client.get("/sounds/kids/scare/../../../config.toml").status_code, 404)

    def test_delete_cannot_escape_the_sound_folder(self):
        self.client.delete("/api/sounds/kids/scare/..%2Fmessages.txt")
        self.assertTrue((self.root / "modes/kids/messages.txt").exists())


if __name__ == "__main__":
    unittest.main()

import threading
import unittest

from .helpers import AppTestCase


class SpeakerTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.sp = self.ctl.speaker

    def test_messages_skip_comments_and_blank_lines(self):
        self.assertEqual(self.sp.messages("kids"), ["kids one", "kids two"])

    def test_missing_messages_file_is_empty(self):
        self.sp.messages_file("kids").unlink()
        self.assertEqual(self.sp.messages("kids"), [])
        self.assertIsNone(self.sp.next_message("kids"))

    def test_no_repeats_until_all_messages_are_used(self):
        self.sp.messages_file("kids").write_text("a\nb\nc\n")
        first = [self.sp.next_message("kids") for _ in range(3)]
        self.assertCountEqual(first, ["a", "b", "c"])
        second = [self.sp.next_message("kids") for _ in range(3)]
        self.assertCountEqual(second, ["a", "b", "c"])

    def test_deleted_message_is_not_played(self):
        self.sp.messages_file("kids").write_text("a\nb\nc\n")
        self.sp.next_message("kids")
        self.sp.messages_file("kids").write_text("only this\n")
        self.assertEqual(self.sp.next_message("kids"), "only this")

    def test_only_sound_files_count(self):
        d = self.sp.sound_dir("scare", "scary")
        for name in ("b.WAV", "a.mp3", "c.ogg", "d.flac", "notes.txt", "e.m4a"):
            (d / name).write_bytes(b"")
        self.assertEqual([p.name for p in self.sp.scare_sounds("scary")],
                         ["a.mp3", "b.WAV", "c.ogg", "d.flac"])
        self.assertEqual(self.sp.ambient_sounds("kids"), [])

    def test_missing_sound_folder_is_empty(self):
        self.sp.sound_dir("ambient", "kids").rmdir()
        self.assertEqual(self.sp.ambient_sounds("kids"), [])

    def test_bad_kind_and_mode_are_rejected(self):
        with self.assertRaises(ValueError):
            self.sp.sound_dir("../../etc")
        with self.assertRaises(ValueError):
            self.sp.check_mode("auto")

    def test_each_mode_has_its_own_voice(self):
        self.assertIn("en_GB", self.sp.voice_model("scary"))
        self.assertIn("no_NO", self.sp.voice_model("kids"))

    def test_piper_settings_from_config(self):
        args = self.sp._piper_args("scary")
        self.assertEqual(args[args.index("--length-scale") + 1], "1.2")
        self.assertIn("--noise-w-scale", args)

    def test_speech_cache_key_changes_with_text_and_voice_effects(self):
        a = self.sp._speech_path("Boo", "kids")
        self.assertEqual(a, self.sp._speech_path("Boo", "kids"))
        self.assertNotEqual(a, self.sp._speech_path("Boo!", "kids"))
        self.assertNotEqual(a, self.sp._speech_path("Boo", "scary"))
        self.sp.modes["kids"]["effects"] = "reverb 50"
        self.assertNotEqual(a, self.sp._speech_path("Boo", "kids"))

    def test_pending_messages_counts_unrendered_speech(self):
        self.assertEqual(self.sp.pending_messages(), 4)
        self.sp._speech_path("kids one", "kids").write_bytes(b"")
        self.assertEqual(self.sp.pending_messages(), 3)

    def test_no_speaker_configured(self):
        self.assertIsNone(self.sp.connected())

    def test_only_one_scare_at_a_time(self):
        done = threading.Event()

        def run(text, mode):
            done.wait(5)
            self.sp._lock.release()
        self.sp._run = run
        self.assertTrue(self.sp.scare("hi", "kids"))
        self.assertTrue(self.sp.busy)
        self.assertFalse(self.sp.scare("hi", "kids"))
        done.set()
        for _ in range(100):
            if not self.sp.busy:
                break
            threading.Event().wait(0.01)
        self.assertFalse(self.sp.busy)


class ControllerScareTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.played = []
        self.ctl.speaker.scare = lambda text, mode: self.played.append((text, mode)) or True

    def test_scare_plays_and_counts(self):
        self.assertTrue(self.ctl.scare("Boo", reason="manual"))
        self.assertEqual(self.played, [("Boo", "scary")])
        self.assertEqual(self.ctl.scare_count, 1)
        self.assertEqual(self.ctl.last_scare_reason, "manual, Skummel")

    def test_off_mode_never_scares(self):
        self.ctl.update_settings({"mode": "off"})
        self.assertFalse(self.ctl.scare("Boo"))
        self.assertEqual(self.played, [])

    def test_auto_without_camera_scares_with_kid_mode(self):
        self.ctl.update_settings({"mode": "auto"})
        self.ctl.scare()
        self.assertEqual(self.played, [(None, "kids")])


if __name__ == "__main__":
    unittest.main()

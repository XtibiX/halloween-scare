import hashlib
import logging
import random
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

SOUND_TYPES = {".wav", ".ogg", ".flac", ".mp3"}


class Speaker:
    """Plays a random scare sound followed by a spoken message, with an
    optional quiet ambient loop running underneath.

    Each mode (scary, kids, ...) has its own folder in modes/<name>/ with
    messages.txt, scare/ and ambient/, plus its own voice effects.

    Only one scare plays at a time; scare() returns False while busy.
    Speech is rendered ahead of time (prerender) because Piper takes a few
    seconds per sentence on a Pi 4; playback itself is just a file.
    """

    def __init__(self, root: Path, cfg):
        self.mac = cfg["speaker"]["mac"].strip()
        self.volume = cfg["speaker"]["volume"]
        self.ambient_volume = cfg["speaker"]["ambient_volume"]
        self.model = cfg["voice"]["model"]
        self.modes = {k: v for k, v in cfg["modes"].items() if isinstance(v, dict)}
        self.mode = cfg["modes"].get("default", next(iter(self.modes)))
        self.root = root
        self.cache_dir = root / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._unused = {}  # mode -> messages not yet used this round
        self._connected = None
        self._connected_checked = 0.0
        self._ambient_on = False
        self._ambient_proc = None
        self.silent = False

    @property
    def busy(self):
        return self._lock.locked()

    # --- Modes ---------------------------------------------------------------

    def mode_dir(self, mode=None):
        return self.root / "modes" / (mode or self.mode)

    def sound_dir(self, kind, mode=None):
        if kind not in ("scare", "ambient"):
            raise ValueError("kind must be scare or ambient")
        return self.mode_dir(mode) / kind

    def messages_file(self, mode=None):
        return self.mode_dir(mode) / "messages.txt"

    def check_mode(self, mode):
        if mode not in self.modes:
            raise ValueError(f"unknown mode {mode!r}")
        return mode

    def set_mode(self, mode):
        """The base mode: used for the ambient loop and scares without an explicit mode."""
        self.check_mode(mode)
        if mode == self.mode:
            return
        log.info("Mode: %s", self.modes[mode]["label"])
        self.mode = mode
        # Switch the background sound right away instead of after the current file
        self._stop_ambient()

    def set_silent(self, silent):
        """Off: no background sound (scares are blocked by the controller)."""
        if silent == self.silent:
            return
        log.info("Sound %s", "off" if silent else "on")
        self.silent = silent
        if silent:
            self._stop_ambient()

    def _stop_ambient(self):
        proc = self._ambient_proc
        if proc and proc.poll() is None:
            proc.terminate()

    # --- Bluetooth ---------------------------------------------------------

    def connected(self):
        """True/False, or None when no speaker MAC is configured. Cached for 10 s."""
        if not self.mac:
            return None
        if time.monotonic() - self._connected_checked > 10:
            try:
                out = subprocess.run(["bluetoothctl", "info", self.mac],
                                     capture_output=True, text=True, timeout=5).stdout
                self._connected = "Connected: yes" in out
            except (OSError, subprocess.TimeoutExpired):
                self._connected = False
            self._connected_checked = time.monotonic()
        return self._connected

    def ensure_connected(self):
        if not self.mac or self.connected():
            return
        log.info("Connecting to speaker %s", self.mac)
        try:
            subprocess.run(["bluetoothctl", "connect", self.mac],
                           capture_output=True, timeout=15)
            time.sleep(2)  # give PipeWire a moment to pick up the new sink
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("Speaker connect failed: %s", e)
        self._connected_checked = 0.0

    # --- Messages and sounds -----------------------------------------------

    def messages(self, mode=None):
        try:
            lines = self.messages_file(mode).read_text().splitlines()
        except FileNotFoundError:
            return []
        return [l.strip() for l in lines if l.strip() and not l.lstrip().startswith("#")]

    def next_message(self, mode):
        """Random message, without repeats until all have been used."""
        current = self.messages(mode)
        unused = [m for m in self._unused.get(mode, []) if m in current]
        if not unused:
            unused = random.sample(current, len(current))
        self._unused[mode] = unused
        return unused.pop() if unused else None

    @staticmethod
    def _sound_files(folder):
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.iterdir() if p.suffix.lower() in SOUND_TYPES)

    def scare_sounds(self, mode=None):
        return self._sound_files(self.sound_dir("scare", mode))

    def ambient_sounds(self, mode=None):
        return self._sound_files(self.sound_dir("ambient", mode))

    # --- Speech --------------------------------------------------------------

    def _piper_args(self, mode):
        """Optional per-mode Piper settings, see config.toml."""
        m = self.modes[mode]
        args = []
        for key, flag in (("length_scale", "--length-scale"),
                          ("sentence_silence", "--sentence-silence"),
                          ("noise_scale", "--noise-scale"),
                          ("noise_w", "--noise-w-scale")):
            if key in m:
                args += [flag, str(m[key])]
        return args

    def _speech_path(self, text, mode):
        effects = self.modes[mode]["effects"]
        piper = " ".join(self._piper_args(mode))
        key = hashlib.sha1(f"{text}|{self.model}|{effects}|{piper}".encode()).hexdigest()
        return self.cache_dir / f"tts-{key[:16]}.wav"

    def speech_file(self, text, mode=None):
        """Renders text with Piper plus the mode's sox effects, cached by content."""
        mode = mode or self.mode
        out = self._speech_path(text, mode)
        if out.exists():
            return out
        with self._render_lock:
            if out.exists():
                return out
            raw = self.cache_dir / "tts-raw.wav"
            # nice: rendering must not starve the motion detection
            subprocess.run(["nice", "-n", "10", sys.executable, "-m", "piper",
                            "-m", str(self.root / self.model), "-f", str(raw),
                            *self._piper_args(mode)],
                           input=text.encode(), check=True, capture_output=True, timeout=120)
            effects = shlex.split(self.modes[mode]["effects"])
            if effects:
                result = subprocess.run(["sox", str(raw), str(out), *effects],
                                        capture_output=True, timeout=30)
                if result.returncode == 0:
                    raw.unlink()
                    return out
                log.warning("sox effects failed, using plain speech: %s", result.stderr.decode().strip())
            raw.replace(out)
            return out

    def prerender(self):
        """Renders all messages of all modes in the background (active mode
        first) so scares start instantly."""
        def work():
            # Remove speech for messages or voice settings that no longer exist
            keep = {self._speech_path(t, m) for m in self.modes for t in self.messages(m)}
            for f in self.cache_dir.glob("tts-*.wav"):
                if f not in keep and f.name != "tts-raw.wav":
                    f.unlink(missing_ok=True)
            order = [self.mode] + [m for m in self.modes if m != self.mode]
            todo = [(t, m) for m in order for t in self.messages(m)
                    if not self._speech_path(t, m).exists()]
            if todo:
                log.info("Rendering %d message(s)...", len(todo))
            for text, mode in todo:
                try:
                    self.speech_file(text, mode)
                except Exception as e:
                    log.error("Could not render %r: %s", text, e)
            if todo:
                log.info("Messages ready")
        threading.Thread(target=work, daemon=True).start()

    def pending_messages(self):
        return sum(1 for mode in self.modes for m in self.messages(mode)
                   if not self._speech_path(m, mode).exists())

    def silence_file(self):
        """Bluetooth speakers often swallow the first half second after being
        idle, so a short silence is played first to wake them up."""
        out = self.cache_dir / "silence.wav"
        if not out.exists():
            subprocess.run(["sox", "-n", "-r", "44100", "-c", "2", str(out), "trim", "0", "0.6"],
                           check=True, timeout=10)
        return out

    # --- Playback ----------------------------------------------------------

    def _pw_play(self, path, volume):
        return subprocess.Popen(["pw-play", "--volume", f"{volume:.2f}", str(path)])

    def play(self, path, volume=None):
        proc = self._pw_play(path, self.volume if volume is None else volume)
        try:
            proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()

    def scare(self, text=None, mode=None):
        mode = self.check_mode(mode or self.mode)
        if not self._lock.acquire(blocking=False):
            return False
        threading.Thread(target=self._run, args=(text, mode), daemon=True).start()
        return True

    def _run(self, text, mode):
        try:
            self.ensure_connected()
            text = text or self.next_message(mode)
            speech = self.speech_file(text, mode) if text else None
            sounds = self.scare_sounds(mode)
            if not self._ambient_on:
                self.play(self.silence_file())
            if sounds:
                sound = random.choice(sounds)
                log.info("Sound: %s", sound.name)
                self.play(sound)
            if speech:
                log.info("Saying: %s", text)
                self.play(speech)
        except Exception:
            log.exception("Scare playback failed")
        finally:
            self._lock.release()

    def start_ambient(self):
        """Loops random ambient sounds of the current mode quietly forever
        (PipeWire mixes them with the scares)."""
        def loop():
            while True:
                sounds = self.ambient_sounds()
                if not sounds or self.ambient_volume <= 0 or self.silent:
                    self._ambient_on = False
                    time.sleep(1)
                    continue
                self._ambient_on = True
                try:
                    self.ensure_connected()
                    self._ambient_proc = self._pw_play(random.choice(sounds), self.ambient_volume)
                    self._ambient_proc.wait()
                except Exception as e:
                    log.warning("Ambient playback failed: %s", e)
                    time.sleep(5)
        threading.Thread(target=loop, daemon=True).start()

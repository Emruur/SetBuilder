import os
import subprocess
import sys
import threading
import tempfile
import numpy as np
import sounddevice as sd
import soundfile as sf

# --- FIX FOR LIBROSA/NUMBA SLOWDOWN IN PYINSTALLER ---
# Numba needs a writable directory to cache JIT-compiled functions.
# In a PyInstaller bundle, the default path is read-only, causing recompilation on every call.
app_name = "SetBuilder"
if sys.platform == "darwin":
    cache_dir = os.path.join(os.path.expanduser('~/Library/Application Support'), app_name, "numba_cache")
elif sys.platform == "win32":
    cache_dir = os.path.join(os.environ.get('APPDATA', ''), app_name, "numba_cache")
else:
    cache_dir = os.path.join(os.path.expanduser('~'), f'.{app_name}', "numba_cache")
    
os.makedirs(cache_dir, exist_ok=True)
os.environ['NUMBA_CACHE_DIR'] = cache_dir
# -----------------------------------------------------

import librosa
from pedalboard import Pedalboard, Compressor, HighShelfFilter, LowShelfFilter, PeakFilter, Distortion, Limiter, Gain, load_plugin

class AudioEngine:
    def __init__(self):
        self.is_paused = False
        self.current_track = None
        self.volume = 1.0
        
        # Audio stream variables
        self.stream = None
        self.audio_data = None
        self.noise_data = None   # separated noise stem for denoise mix
        self.denoise_mix = 1.0   # 1.0 = fully denoised, 0.0 = original
        self.spectrum_buffer = np.zeros(2048, dtype=np.float32)  # rolling mono buffer for spectrum display
        self.samplerate = 44100
        self.current_frame = 0
        self.total_frames = 0
        self.channels = 2
        self.current_rms = 0.0
        self.current_lufs = -70.0
        
        # Lock to prevent thread collisions between UI and Audio Callback
        self.audio_lock = threading.Lock()
        
        # Real-time DSP State
        self._dsp_state = {
            'master_bypass': True,
            'chain_order': ['eq', 'dyn'],
            'eq_bypass': True, 'eq_low': 0.0, 'eq_mid': 0.0, 'eq_high': 0.0,
            'dyn_bypass': True, 'dyn_threshold': 0.0, 'dyn_ratio': 1.0, 'dyn_attack': 5.0, 'dyn_release': 100.0, 'dyn_makeup': 0.0,
            'limiter_bypass': True, 'limiter_softclip': False, 'limiter_input': 0.0, 'limiter_output': 0.0,
            'vsts': {}
        }
        
        # Initialize persistent plugin instances to preserve DSP state history (prevents clicks)
        self._eq_low = LowShelfFilter(cutoff_frequency_hz=250, gain_db=0.0)
        self._eq_mid = PeakFilter(cutoff_frequency_hz=1000, gain_db=0.0, q=1.0)
        self._eq_high = HighShelfFilter(cutoff_frequency_hz=4000, gain_db=0.0)
        
        self._compressor = Compressor(threshold_db=0.0, ratio=1.0, attack_ms=5.0, release_ms=100.0)
        
        self._soft_clipper = Distortion(drive_db=0.0) # Uses a tanh function ideal for soft clipping
        self._limiter_in_gain = Gain(gain_db=0.0)
        self._limiter_out_gain = Gain(gain_db=0.0)
        self._limiter = Limiter(threshold_db=-0.1)
        self.vst_instances = {}

        self.active_plugins = []
        initial_state = self._dsp_state
        self._dsp_state = {}
        self.dsp_state = initial_state

    @property
    def dsp_state(self):
        return self._dsp_state

    @dsp_state.setter
    def dsp_state(self, new_state):
        # Clean up removed VSTs from memory to prevent memory leaks
        current_vsts = new_state.get('vsts', {})
        for mod in list(self.vst_instances.keys()):
            if mod not in current_vsts:
                del self.vst_instances[mod]

        # 1. Load any missing VSTs OUTSIDE the lock to prevent audio thread stuttering
        for mod in new_state.get('chain_order', []):
            if mod.startswith('vst_'):
                vst_info = new_state.get('vsts', {}).get(mod)
                if vst_info and mod not in self.vst_instances:
                    try:
                        self.vst_instances[mod] = load_plugin(vst_info['path'])
                    except Exception as e:
                        print(f"Failed to load VST {vst_info['path']}: {e}")

        # 2. Build the new active plugin array OUTSIDE the lock
        new_active_plugins = []
        if not new_state.get('master_bypass', False):
            for mod in new_state.get('chain_order', ['eq', 'dyn']):
                if mod == 'eq' and not new_state.get('eq_bypass', False):
                    new_active_plugins.extend([self._eq_low, self._eq_mid, self._eq_high])
                elif mod == 'dyn' and not new_state.get('dyn_bypass', False):
                    new_active_plugins.append(self._compressor)
                elif mod.startswith('vst_'):
                    vst_info = new_state.get('vsts', {}).get(mod)
                    if vst_info and not vst_info.get('bypass', False) and mod in self.vst_instances:
                        new_active_plugins.append(self.vst_instances[mod])
            
            if not new_state.get('limiter_bypass', False):
                new_active_plugins.append(self._limiter_in_gain)
                if new_state.get('limiter_softclip', False):
                    new_active_plugins.append(self._soft_clipper)
                new_active_plugins.append(self._limiter)
                new_active_plugins.append(self._limiter_out_gain)

        # 3. Swap the parameters and lists INSIDE the lock (instantly)
        with self.audio_lock:
            self._dsp_state = new_state
            
            # Mutate existing native instances instead of creating new ones
            self._eq_low.cutoff_frequency_hz = self._dsp_state.get('eq_low_freq', 250.0)
            self._eq_low.gain_db = self._dsp_state.get('eq_low', 0.0)
            self._eq_low.q = self._dsp_state.get('eq_low_q', 0.707)
            self._eq_mid.cutoff_frequency_hz = self._dsp_state.get('eq_mid_freq', 1000.0)
            self._eq_mid.gain_db = self._dsp_state.get('eq_mid', 0.0)
            self._eq_mid.q = self._dsp_state.get('eq_mid_q', 1.0)
            self._eq_high.cutoff_frequency_hz = self._dsp_state.get('eq_high_freq', 4000.0)
            self._eq_high.gain_db = self._dsp_state.get('eq_high', 0.0)
            self._eq_high.q = self._dsp_state.get('eq_high_q', 0.707)
            
            self._compressor.threshold_db = self._dsp_state.get('dyn_threshold', 0.0)
            self._compressor.ratio = self._dsp_state.get('dyn_ratio', 1.0)
            self._compressor.attack_ms = self._dsp_state.get('dyn_attack', 5.0)
            self._compressor.release_ms = self._dsp_state.get('dyn_release', 100.0)

            self._limiter_in_gain.gain_db = self._dsp_state.get('limiter_input', 0.0)
            self._limiter_out_gain.gain_db = self._dsp_state.get('limiter_output', 0.0)

            self.active_plugins = new_active_plugins

    def _audio_callback(self, outdata, frames, time, status):
        """This function is called by the soundcard every few milliseconds to get the next chunk of audio."""
        if status:
            print(status)
            
        with self.audio_lock:
            if self.is_paused or self.audio_data is None:
                outdata.fill(0)
                return

            # 1. Slice the raw chunk from RAM
            end_frame = self.current_frame + frames
            if end_frame > self.total_frames:
                frames_read = self.total_frames - self.current_frame
                if frames_read <= 0:
                    outdata.fill(0)
                    self.is_paused = True # Auto-stop at end
                    return
                outdata[:frames_read] = self.audio_data[self.current_frame:self.total_frames]
                outdata[frames_read:].fill(0)
                self.is_paused = True
            else:
                frames_read = frames
                outdata[:] = self.audio_data[self.current_frame:end_frame]

            self.current_frame += frames_read

            # 1b. Blend noise stem back in for denoise mix < 1.0
            if self.noise_data is not None and self.denoise_mix < 0.9999:
                n_start = self.current_frame - frames_read
                n_end = min(self.current_frame, len(self.noise_data))
                n_len = n_end - n_start
                if n_len > 0:
                    outdata[:n_len] += self.noise_data[n_start:n_end] * (1.0 - self.denoise_mix)

            # 2. Process the chunk in real-time bypassing Pedalboard clones
            if len(self.active_plugins) > 0:
                audio_chunk = outdata[:frames_read].T
                for plugin in self.active_plugins:
                    audio_chunk = plugin.process(audio_chunk, self.samplerate, reset=False)
                outdata[:frames_read] = audio_chunk.T
                
                # Apply Makeup Gain for Dynamics if active
                if not self._dsp_state.get('master_bypass', False) and not self._dsp_state.get('dyn_bypass', False):
                    makeup_linear = 10 ** (self._dsp_state['dyn_makeup'] / 20.0)
                    outdata[:frames_read] *= makeup_linear

            # 4. Apply Master UI Volume
            outdata[:frames_read] *= self.volume

            # Calculate real-time RMS and pseudo-LUFS for the UI
            if frames_read > 0:
                rms = float(np.sqrt(np.mean(outdata[:frames_read]**2)))
                # Fast attack, slow release for VU meter ballistics
                if rms > self.current_rms:
                    self.current_rms = (0.6 * rms) + (0.4 * self.current_rms)
                else:
                    self.current_rms = (0.1 * rms) + (0.9 * self.current_rms)
                    
                # Update spectrum display buffer (mono mix, no lock — minor race is fine)
                mono = outdata[:frames_read].mean(axis=1)
                n = len(mono)
                self.spectrum_buffer = np.roll(self.spectrum_buffer, -n)
                self.spectrum_buffer[-n:] = mono

                # Calculate instant LUFS, then heavily smooth it for readable text
                instant_lufs = float((20 * np.log10(rms)) + 3.0) if rms > 1e-6 else -70.0
                self.current_lufs = (0.05 * instant_lufs) + (0.95 * self.current_lufs)
            else:
                self.current_rms = 0.9 * self.current_rms
                self.current_lufs = (0.05 * -70.0) + (0.95 * self.current_lufs)

    def play(self, filepath, volume=1.0, noise_path=None, denoise_mix=1.0):
        self.play_from(filepath, 0.0, volume, noise_path=noise_path, denoise_mix=denoise_mix)

    def play_from(self, filepath, start_time, volume=1.0, noise_path=None, denoise_mix=1.0):
        self.is_paused = True
        self.volume = volume
        self.current_track = os.path.basename(filepath)

        try:
            data, sr = sf.read(filepath, dtype='float32')
            if len(data.shape) == 1:
                data = np.column_stack((data, data))

            noise_data = None
            if noise_path and os.path.exists(noise_path):
                try:
                    ndata, _ = sf.read(noise_path, dtype='float32')
                    if len(ndata.shape) == 1:
                        ndata = np.column_stack((ndata, ndata))
                    noise_data = ndata
                except Exception as e:
                    print(f"Failed to load noise stem: {e}")

            need_new_stream = (self.stream is None or self.samplerate != sr)
            if need_new_stream and self.stream is not None:
                self.stream.stop()
                self.stream.close()
                self.stream = None

            with self.audio_lock:
                self.audio_data = data
                self.noise_data = noise_data
                self.denoise_mix = denoise_mix
                self.channels = 2
                self.total_frames = len(data)
                self.samplerate = sr
                self.current_frame = int(start_time * sr)

                if need_new_stream:
                    self.stream = sd.OutputStream(
                        samplerate=self.samplerate, channels=self.channels,
                        callback=self._audio_callback, blocksize=2048)
                    self.stream.start()

                self.is_paused = False
        except Exception as e:
            print(f"Error starting playback: {e}")

    def toggle_pause(self):
        self.is_paused = not self.is_paused

    def set_realtime_volume(self, volume):
        self.volume = volume

    def stop(self):
        self.is_paused = True
        with self.audio_lock:
            self.audio_data = None
            self.noise_data = None
            self.total_frames = 0
            self.current_track = None

    def unload(self):
        self.is_paused = True
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.audio_data = None
        self.noise_data = None
        self.total_frames = 0
        self.current_track = None

    def get_pos(self):
        """Returns current playback position in milliseconds (to match pygame behavior)"""
        if self.samplerate == 0 or self.current_frame == 0:
            return 0
        return (self.current_frame / self.samplerate) * 1000

    # ==========================================
    # ANALYSIS & EXPORT (Unchanged from original)
    # ==========================================
    @staticmethod
    def analyze_track(filepath):
        try:
            duration = float(librosa.get_duration(path=filepath))
            y, sr = librosa.load(filepath, sr=22050, mono=True, duration=30.0)
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            bpm = int(round(tempo[0])) if isinstance(tempo, np.ndarray) else int(round(tempo))
            
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
            pitches = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
            tone = str(pitches[np.argmax(np.mean(chroma, axis=1))])
            
            rms = np.mean(librosa.feature.rms(y=y))
            lufs = float((20 * np.log10(rms)) + 3.0) if rms > 0 else -14.0
            size_mb = float(os.path.getsize(filepath) / (1024 * 1024))
            
            return bpm, tone, duration, lufs, size_mb
        except Exception as e:
            print(f"Analysis error: {e}")
            return 0, "?", 0.0, -14.0, 0.0

    @staticmethod
    def denoise_model_dir():
        """Returns the directory where the denoising model is cached."""
        if sys.platform == 'darwin':
            return os.path.join(os.path.expanduser('~/Library/Application Support'),
                                'SetBuilder', 'separator_models')
        return os.path.join(os.path.expanduser('~'), '.SetBuilder', 'separator_models')

    DENOISE_MODEL = 'denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt'

    @staticmethod
    def find_denoiser_companion(required_version=None):
        """Return (path, is_script) for the SetBuilderDenoiser companion, or (None, False).

        If required_version is given, companions without a matching version stamp
        in their install dir are rejected (triggers re-download).
        """
        if not getattr(sys, 'frozen', False):
            here = os.path.dirname(os.path.abspath(__file__))
            worker = os.path.join(here, 'denoiser_worker.py')
            if os.path.isfile(worker):
                return worker, True
            return None, False

        exe_dir = os.path.dirname(sys.executable)
        apps_dir = os.path.normpath(os.path.join(exe_dir, '..', '..', '..'))
        app_binary = os.path.join('SetBuilderDenoiser.app', 'Contents', 'MacOS', 'SetBuilderDenoiser')
        app_support = os.path.expanduser('~/Library/Application Support/SetBuilder')

        for search_dir in [app_support,
                           apps_dir,
                           os.path.expanduser('~/Applications'),
                           '/Applications']:
            p = os.path.normpath(os.path.join(search_dir, app_binary))
            if not (os.path.isfile(p) and os.access(p, os.X_OK)):
                continue
            # For the managed App Support location, enforce version stamp
            if required_version and search_dir == app_support:
                stamp = os.path.join(app_support, 'denoiser_version.txt')
                try:
                    installed = open(stamp).read().strip()
                except OSError:
                    installed = None
                if installed != required_version:
                    import shutil
                    shutil.rmtree(os.path.join(app_support, 'SetBuilderDenoiser.app'),
                                  ignore_errors=True)
                    continue
            return p, False

        return None, False

    @staticmethod
    def run_denoiser_companion(companion_path, is_script, source_path, dest_path,
                               noise_dest_path=None, download_cb=None,
                               download_done_cb=None, inference_cb=None):
        """Spawn the companion process and relay JSON progress to callbacks."""
        import json

        import json, shutil

        # Companion only takes --input; base app handles WAV→MP3 via pedalboard
        cmd = ([sys.executable, companion_path] if is_script else [companion_path]) + [
            '--input', str(source_path),
        ]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        tmp_dir = None
        try:
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = msg.get('type')
                if t == 'progress':
                    stage, pct = msg.get('stage'), msg.get('pct', 0.0)
                    if stage == 'download' and download_cb:
                        download_cb(pct)
                    elif stage == 'inference' and inference_cb:
                        inference_cb(pct)
                elif t == 'download_done':
                    if download_done_cb:
                        download_done_cb()
                elif t == 'done':
                    # Companion finished inference — convert WAVs to MP3 using pedalboard
                    # (avoids ffmpeg subprocess which Gatekeeper blocks on unsigned apps)
                    from pedalboard.io import AudioFile
                    import mutagen.id3 as _id3
                    import mutagen.mp3 as _mp3

                    dry_wav   = msg.get('dry_wav')
                    noise_wav = msg.get('noise_wav')
                    tmp_dir   = msg.get('tmp_dir')

                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with AudioFile(dry_wav) as f:
                        audio = f.read(f.frames)
                        sr    = f.samplerate
                    with AudioFile(str(dest_path), 'w', samplerate=sr,
                                   num_channels=audio.shape[0], quality='V0') as f:
                        f.write(audio)

                    # Copy ID3 tags from source to denoised file
                    try:
                        src_tags = _mp3.MP3(str(source_path))
                        dst_tags = _mp3.MP3(str(dest_path))
                        if src_tags.tags:
                            dst_tags.tags = src_tags.tags
                            dst_tags.save()
                    except Exception:
                        pass

                    if noise_dest_path and noise_wav:
                        os.makedirs(os.path.dirname(noise_dest_path), exist_ok=True)
                        with AudioFile(noise_wav) as f:
                            n_audio = f.read(f.frames)
                            n_sr    = f.samplerate
                        with AudioFile(str(noise_dest_path), 'w', samplerate=n_sr,
                                       num_channels=n_audio.shape[0], quality='V5') as f:
                            f.write(n_audio)

                    if inference_cb:
                        inference_cb(100.0)
                elif t == 'error':
                    proc.wait()
                    raise RuntimeError(msg.get('message', 'Denoiser companion error'))
        finally:
            proc.wait()
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        if proc.returncode != 0:
            raise RuntimeError(f'Denoiser companion exited with code {proc.returncode}')

    def convert_to_mp3(self, source_path, dest_path, normalize=False, lufs=-14.0, volume=1.0):
        """Transcodes imported wav/flac/mp3 files to mp3 using pedalboard (no ffmpeg)."""
        from pedalboard.io import AudioFile
        import pyloudnorm as pyln
        import mutagen

        with AudioFile(str(source_path)) as f:
            audio = f.read(f.frames)   # shape: (channels, samples)
            sr    = f.samplerate

        if volume != 1.0:
            audio = audio * volume

        if normalize:
            meter   = pyln.Meter(sr)
            # pyloudnorm expects (samples, channels)
            loudness = meter.integrated_loudness(audio.T)
            if loudness > -70:  # skip if silence
                audio = pyln.normalize.loudness(audio.T, loudness, lufs).T

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with AudioFile(str(dest_path), 'w', samplerate=sr,
                       num_channels=audio.shape[0], quality='V0') as f:
            f.write(audio)

        # Copy tags + artwork from source using mutagen
        try:
            src = mutagen.File(str(source_path))
            dst = mutagen.File(str(dest_path))
            if src and dst and src.tags:
                # Copy cover art
                for tag in list(src.tags.keys()):
                    try:
                        dst.tags[tag] = src.tags[tag]
                    except Exception:
                        pass
                dst.save()
        except Exception:
            pass

    def render_export_track(self, source_path, dest_path, normalize=False, quality_flag="0", lufs=-14.0, volume=1.0):
        """Render using Pedalboard for 1:1 sonic parity, then FFmpeg for final encode/normalize."""
        # 1. Load full audio into RAM
        data, sr = sf.read(source_path, dtype='float32')
        if len(data.shape) == 1:
            data = np.column_stack((data, data))

        # 2. Build Pedalboard based on current state (same logic as live playback)
        plugins = []
        if not self._dsp_state.get('master_bypass', False):
            for mod in self._dsp_state.get('chain_order', ['eq', 'dyn']):
                if mod == 'eq' and not self._dsp_state.get('eq_bypass', False):
                    plugins.append(LowShelfFilter(cutoff_frequency_hz=250, gain_db=self._dsp_state['eq_low']))
                    plugins.append(PeakFilter(cutoff_frequency_hz=1000, gain_db=self._dsp_state['eq_mid'], q=1.0))
                    plugins.append(HighShelfFilter(cutoff_frequency_hz=4000, gain_db=self._dsp_state['eq_high']))
                elif mod == 'dyn' and not self._dsp_state.get('dyn_bypass', False):
                    plugins.append(Compressor(threshold_db=self._dsp_state['dyn_threshold'], ratio=self._dsp_state['dyn_ratio'], attack_ms=self._dsp_state['dyn_attack'], release_ms=self._dsp_state['dyn_release']))
                elif mod.startswith('vst_'):
                    vst_info = self._dsp_state.get('vsts', {}).get(mod)
                    # Capture the exact VST instance containing user's live UI tweaks
                    if vst_info and not vst_info.get('bypass', False) and mod in self.vst_instances:
                        plugins.append(self.vst_instances[mod])
            
            if not self._dsp_state.get('limiter_bypass', False):
                plugins.append(Gain(gain_db=self._dsp_state.get('limiter_input', 0.0)))
                if self._dsp_state.get('limiter_softclip', False):
                    plugins.append(Distortion(drive_db=0.0))
                plugins.append(Limiter(threshold_db=-0.1))
                plugins.append(Gain(gain_db=self._dsp_state.get('limiter_output', 0.0)))

        # 3. Process Audio Offline
        if len(plugins) > 0:
            audio_chunk = data.T
            for plugin in plugins:
                audio_chunk = plugin.process(audio_chunk, sr, reset=False)
            data = audio_chunk.T

            if not self._dsp_state.get('master_bypass', False) and not self._dsp_state.get('dyn_bypass', False):
                makeup_linear = 10 ** (self._dsp_state['dyn_makeup'] / 20.0)
                data *= makeup_linear

        # 4. Apply base volume slider
        if volume != 1.0:
            data *= volume

        # 5. Loudnorm + encode to MP3 using pedalboard (no ffmpeg subprocess)
        from pedalboard.io import AudioFile
        import pyloudnorm as pyln
        import mutagen

        if normalize:
            meter    = pyln.Meter(sr)
            loudness = meter.integrated_loudness(data)
            if loudness > -70:
                data = pyln.normalize.loudness(data, loudness, lufs)

        # Map ffmpeg -q:a 0-9 → pedalboard VBR quality string
        quality_map = {'0': 'V0', '1': 'V1', '2': 'V2', '3': 'V3',
                       '4': 'V4', '5': 'V5', '6': 'V6', '7': 'V7',
                       '8': 'V8', '9': 'V9'}
        pb_quality = quality_map.get(str(quality_flag), 'V0')

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        audio_out = np.clip(data, -1.0, 1.0).T  # (channels, samples)
        with AudioFile(str(dest_path), 'w', samplerate=sr,
                       num_channels=audio_out.shape[0], quality=pb_quality) as f:
            f.write(audio_out)

        # Copy tags + artwork from source
        try:
            src_tags = mutagen.File(str(source_path))
            dst_tags = mutagen.File(str(dest_path))
            if src_tags and dst_tags and src_tags.tags:
                for tag in list(src_tags.tags.keys()):
                    try:
                        dst_tags.tags[tag] = src_tags.tags[tag]
                    except Exception:
                        pass
                dst_tags.save()
        except Exception:
            pass
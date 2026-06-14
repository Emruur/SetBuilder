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

def get_ffmpeg_path():
    """Determines the correct path for the ffmpeg binary, whether bundled or in system PATH."""
    ffmpeg_exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        meipass = getattr(sys, '_MEIPASS', None)
        candidate_dirs = [
            exe_dir,
            os.path.join(exe_dir, '..', 'Frameworks'),
            os.path.join(exe_dir, '..', 'Resources'),
            meipass,
        ]
        # Write diagnostics so we can see what's happening
        try:
            debug_path = os.path.expanduser('~/Desktop/setbuilder_ffmpeg_debug.txt')
            with open(debug_path, 'w') as _f:
                _f.write(f"sys.executable: {sys.executable}\n")
                _f.write(f"sys._MEIPASS: {meipass}\n")
                for d in candidate_dirs:
                    if d:
                        p = os.path.normpath(os.path.join(d, ffmpeg_exe))
                        _f.write(f"  checking: {p} -> exists={os.path.isfile(p)}\n")
        except Exception as _e:
            pass
        for d in candidate_dirs:
            if d:
                p = os.path.normpath(os.path.join(d, ffmpeg_exe))
                if os.path.isfile(p):
                    return p
    return ffmpeg_exe
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
    def find_denoiser_companion():
        """Return (path, is_script) for the SetBuilderDenoiser companion, or (None, False)."""
        if not getattr(sys, 'frozen', False):
            here = os.path.dirname(os.path.abspath(__file__))
            worker = os.path.join(here, 'denoiser_worker.py')
            if os.path.isfile(worker):
                return worker, True
            return None, False

        exe_dir = os.path.dirname(sys.executable)
        # From SetBuilder.app/Contents/MacOS/ go up three levels to get the parent folder
        apps_dir = os.path.normpath(os.path.join(exe_dir, '..', '..', '..'))
        app_binary = os.path.join('SetBuilderDenoiser.app', 'Contents', 'MacOS', 'SetBuilderDenoiser')

        for search_dir in [apps_dir,
                           os.path.expanduser('~/Applications'),
                           '/Applications']:
            p = os.path.normpath(os.path.join(search_dir, app_binary))
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p, False

        # Flat build: companion binary sits next to the main executable
        flat = os.path.join(exe_dir, 'SetBuilderDenoiser')
        if os.path.isfile(flat) and os.access(flat, os.X_OK):
            return flat, False

        return None, False

    @staticmethod
    def run_denoiser_companion(companion_path, is_script, source_path, dest_path,
                               noise_dest_path=None, download_cb=None,
                               download_done_cb=None, inference_cb=None):
        """Spawn the companion process and relay JSON progress to callbacks."""
        import json

        cmd = ([sys.executable, companion_path] if is_script else [companion_path]) + [
            '--input', str(source_path),
            '--output', str(dest_path),
            '--ffmpeg', get_ffmpeg_path(),
        ]
        if noise_dest_path:
            cmd += ['--noise-output', str(noise_dest_path)]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
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
                    if inference_cb:
                        inference_cb(100.0)
                elif t == 'error':
                    proc.wait()
                    raise RuntimeError(msg.get('message', 'Denoiser companion error'))
        finally:
            proc.wait()

        if proc.returncode != 0:
            raise RuntimeError(f'Denoiser companion exited with code {proc.returncode}')

    @staticmethod
    def denoise_track(source_path, dest_path, noise_dest_path=None, download_cb=None, download_done_cb=None, inference_cb=None):
        """Denoise using Mel-Band-Roformer (audio-separator). SDR ~28dB, music-safe.

        download_cb(pct)      — 0-100 during model download (omitted if already cached)
        download_done_cb()    — called once download finishes, before inference starts
        inference_cb(pct)     — 0-100 during chunk inference
        """
        from audio_separator.separator import Separator
        import audio_separator.separator.separator as _sep_mod
        import audio_separator.separator.architectures.mdxc_separator as _mdxc_mod
        import tempfile, shutil

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        model_dir = AudioEngine.denoise_model_dir()
        os.makedirs(model_dir, exist_ok=True)

        # --- Download proxy (update/close pattern used by separator.py) ---
        if download_cb:
            class _DownloadProxy:
                def __init__(self, total=0, **_kw):
                    self._total = total
                    self._done = 0
                def update(self, n=1):
                    self._done += n
                    if self._total > 0:
                        download_cb(self._done / self._total * 100.0)
                def close(self): pass
                def __enter__(self): return self
                def __exit__(self, *_): pass
            _orig_sep_tqdm = _sep_mod.tqdm
            _sep_mod.tqdm = _DownloadProxy

        # --- Inference proxy (iterable pattern used by mdxc_separator.py) ---
        if inference_cb:
            class _InferenceProxy:
                def __init__(self, iterable=None, **_kw):
                    self._items = list(iterable) if iterable is not None else []
                    self._n = len(self._items)
                def __iter__(self):
                    for i, item in enumerate(self._items):
                        yield item
                        if self._n > 0:
                            inference_cb((i + 1) / self._n * 98.0)
                def __enter__(self): return self
                def __exit__(self, *_): pass
            _orig_mdxc_tqdm = _mdxc_mod.tqdm
            _mdxc_mod.tqdm = _InferenceProxy

        tmp_dir = tempfile.mkdtemp()
        try:
            sep = Separator(
                model_file_dir=model_dir,
                output_dir=tmp_dir,
                output_format='WAV',
                log_level=30,
                use_autocast=True,
                mdxc_params={
                    'segment_size': 256,
                    'batch_size': 1,
                    'overlap': 4,
                    'override_model_segment_size': False,
                    'pitch_shift': 0,
                },
            )
            sep.load_model(AudioEngine.DENOISE_MODEL)
            if download_cb:
                _sep_mod.tqdm = _orig_sep_tqdm
            if download_done_cb:
                download_done_cb()
            output_files = sep.separate(source_path)

            wavs = [f for f in os.listdir(tmp_dir) if f.lower().endswith('.wav')]
            if not wavs:
                raise RuntimeError(f"No output WAV found in {tmp_dir}. "
                                   f"Separator returned: {output_files}")
            dry = [f for f in wavs if '(dry)' in f.lower()]
            noise_wavs = [f for f in wavs if f not in dry]
            if not dry:
                raise RuntimeError(f"No dry stem found in output: {wavs}")
            denoised_wav = os.path.join(tmp_dir, dry[0])

            # Convert dry stem to MP3 (copy artwork/tags from source)
            cmd = [get_ffmpeg_path(), '-y',
                   '-i', denoised_wav, '-i', str(source_path),
                   '-map', '0:a:0', '-map', '1:v:0?', '-map_metadata', '1',
                   '-c:v', 'copy', '-codec:a', 'libmp3lame', '-q:a', '0',
                   '-id3v2_version', '3', str(dest_path)]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Convert noise stem to MP3 if caller wants it
            if noise_dest_path and noise_wavs:
                os.makedirs(os.path.dirname(noise_dest_path), exist_ok=True)
                cmd_n = [get_ffmpeg_path(), '-y',
                         '-i', os.path.join(tmp_dir, noise_wavs[0]),
                         '-codec:a', 'libmp3lame', '-q:a', '2', str(noise_dest_path)]
                subprocess.run(cmd_n, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            if download_cb:
                _sep_mod.tqdm = _orig_sep_tqdm
            if inference_cb:
                _mdxc_mod.tqdm = _orig_mdxc_tqdm
                inference_cb(100.0)
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def convert_to_mp3(self, source_path, dest_path, normalize=False, lufs=-14.0, volume=1.0):
        """Transcodes imported wav/flac files to mp3 and optionally normalizes them."""
        filters = []
        if normalize: filters.append(f'loudnorm=I={lufs}:TP=-1.0:LRA=11')
        if volume != 1.0: filters.append(f'volume={volume}')

        cmd = [get_ffmpeg_path(), '-y', '-i', str(source_path)]
        if filters: cmd.extend(['-af', ','.join(filters)])
        cmd.extend([
            '-map', '0:a:0',
            '-map', '0:v:0?',
            '-map_metadata', '0',
            '-c:v', 'copy',
            '-codec:a', 'libmp3lame', '-q:a', '0', 
            '-id3v2_version', '3',
            str(dest_path)
        ])
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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

        # 5. Write to a temporary WAV file for FFmpeg to encode/normalize
        temp_fd, temp_path = tempfile.mkstemp(suffix='.wav')
        os.close(temp_fd)
        
        try:
            sf.write(temp_path, data, sr)

            filters = []
            if normalize: filters.append(f'loudnorm=I={lufs}:TP=-1.0:LRA=11')

            cmd = [get_ffmpeg_path(), '-y', '-i', str(temp_path), '-i', str(source_path)]
            if filters: cmd.extend(['-af', ','.join(filters)])
            cmd.extend([
                '-map', '0:a:0',
                '-map', '1:v:0?',
                '-map_metadata', '1',
                '-c:v', 'copy',
                '-codec:a', 'libmp3lame', '-q:a', str(quality_flag), 
                '-id3v2_version', '3',
                str(dest_path)
            ])
            
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
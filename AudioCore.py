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
    # The name of the executable
    ffmpeg_exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    # If running in a PyInstaller bundle
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        bundle_dir = sys._MEIPASS
        bundled_path = os.path.join(bundle_dir, ffmpeg_exe)
        if os.path.exists(bundled_path):
            return bundled_path
    return ffmpeg_exe # Fallback to system PATH
class AudioEngine:
    def __init__(self):
        self.is_paused = False
        self.current_track = None
        self.volume = 1.0
        
        # Audio stream variables
        self.stream = None
        self.audio_data = None
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
            self._eq_low.gain_db = self._dsp_state.get('eq_low', 0.0)
            self._eq_mid.gain_db = self._dsp_state.get('eq_mid', 0.0)
            self._eq_high.gain_db = self._dsp_state.get('eq_high', 0.0)
            
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
                    
                # Calculate instant LUFS, then heavily smooth it for readable text
                instant_lufs = float((20 * np.log10(rms)) + 3.0) if rms > 1e-6 else -70.0
                self.current_lufs = (0.05 * instant_lufs) + (0.95 * self.current_lufs)
            else:
                self.current_rms = 0.9 * self.current_rms
                self.current_lufs = (0.05 * -70.0) + (0.95 * self.current_lufs)

    def play(self, filepath, volume=1.0):
        self.play_from(filepath, 0.0, volume)

    def play_from(self, filepath, start_time, volume=1.0):
        self.is_paused = True  # Soft pause while loading new data
        self.volume = volume
        self.current_track = os.path.basename(filepath)
        
        try:
            data, sr = sf.read(filepath, dtype='float32')
            if len(data.shape) == 1:
                data = np.column_stack((data, data))
            
            with self.audio_lock:
                self.audio_data = data
                self.channels = 2
                self.total_frames = len(data)
                self.current_frame = int(start_time * sr)
                
                if self.stream is None or self.samplerate != sr:
                    if self.stream:
                        self.stream.stop()
                        self.stream.close()
                    
                    self.samplerate = sr
                    self.stream = sd.OutputStream(samplerate=self.samplerate, channels=self.channels, callback=self._audio_callback, blocksize=2048)
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
            self.total_frames = 0
            self.current_track = None

    def unload(self):
        self.is_paused = True
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.audio_data = None
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
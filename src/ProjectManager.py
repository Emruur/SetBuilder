import os
import json
import re
import sys
from pathlib import Path

class ProjectState:
    def __init__(self):
        self.tracks = []
        self.current_folder = ""
        self.is_saved_set = False
        self.settings = {"normalized": False, "lufs_target": -14.0}
        self.needs_save = False
        
        app_name = "SetBuilder"
        if sys.platform == "darwin":
            self.state_dir = os.path.join(os.path.expanduser('~/Library/Application Support'), app_name)
        elif sys.platform == "win32":
            self.state_dir = os.path.join(os.environ['APPDATA'], app_name)
        else:
            self.state_dir = os.path.join(os.path.expanduser('~'), f'.{app_name}')
            
        os.makedirs(self.state_dir, exist_ok=True)
        self.state_file = os.path.join(self.state_dir, "last_state.json")

    @staticmethod
    def clean_name(filename):
        path_obj = Path(filename)
        clean_stem = re.sub(r'^[\d\._\-\s]+', '', path_obj.stem)
        return f"{clean_stem}{path_obj.suffix.lower()}"

    def save_app_memory(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({"last_folder": self.current_folder}, f)
        except Exception:
            pass

    def load_app_memory(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    return data.get("last_folder", "")
            except Exception:
                pass
        return ""

    def shift_track(self, idx, offset):
        if not self.tracks: return idx
        new_idx = max(0, min(len(self.tracks) - 1, idx + offset))
        if idx == new_idx: return new_idx
        target_inactive = self.tracks[new_idx].get('inactive', False)
        track = self.tracks.pop(idx)
        track['inactive'] = target_inactive
        self.tracks.insert(new_idx, track)
        return new_idx

    def toggle_inactive(self, idx):
        if idx < 0 or idx >= len(self.tracks): return idx
        track = self.tracks.pop(idx)
        new_inactive = not track.get('inactive', False)
        track['inactive'] = new_inactive
        if new_inactive:
            self.tracks.append(track)
            return len(self.tracks) - 1
        # Restoring: drop just above the first inactive track (bottom of active section)
        first_inactive = next((i for i, t in enumerate(self.tracks) if t.get('inactive', False)), len(self.tracks))
        self.tracks.insert(first_inactive, track)
        return first_inactive

    def resort_by_bpm(self):
        actives = [t for t in self.tracks if not t.get('inactive', False)]
        inactives = [t for t in self.tracks if t.get('inactive', False)]
        actives.sort(key=lambda x: x['bpm'])
        inactives.sort(key=lambda x: x['bpm'])
        self.tracks = actives + inactives

    def is_bpm_sorted(self):
        """Checks if the current tracklist is still perfectly sorted by BPM (actives first, then inactives)"""
        if len(self.tracks) <= 1:
            return True
        seen_inactive = False
        for t in self.tracks:
            if t.get('inactive', False):
                seen_inactive = True
            elif seen_inactive:
                return False
        actives = [t for t in self.tracks if not t.get('inactive', False)]
        inactives = [t for t in self.tracks if t.get('inactive', False)]
        return (all(actives[i]['bpm'] <= actives[i+1]['bpm'] for i in range(len(actives)-1))
                and all(inactives[i]['bpm'] <= inactives[i+1]['bpm'] for i in range(len(inactives)-1)))

    def modify_bpm(self, idx, multiplier):
        track = self.tracks[idx]
        track['bpm'] = round(track['bpm'] * multiplier)
        return track

    def get_track_metadata(self, filepath):
        try:
            import mutagen
            file = mutagen.File(filepath, easy=True)
            if file:
                artist = file.get('artist', [''])[0]
                album = file.get('album', [''])[0]
                song = file.get('title', [''])[0]
                return str(artist), str(album), str(song)
        except Exception:
            pass
        return "", "", ""

    def get_track_artwork_bytes(self, filepath):
        """Returns embedded cover-art bytes from any common audio format, or None."""
        try:
            import mutagen
            f = mutagen.File(filepath)
            if f is None:
                return None

            # MP3 / ID3: APIC (v2.3/4) or PIC (v2.2), regardless of key suffix
            tags = getattr(f, 'tags', None)
            if tags is not None:
                try:
                    for frame in tags.values():
                        fid = getattr(frame, 'FrameID', None) or type(frame).__name__
                        data = getattr(frame, 'data', None)
                        if fid in ('APIC', 'PIC') and data:
                            return data
                except Exception:
                    pass

            # FLAC / OGG: pictures attribute
            pics = getattr(f, 'pictures', None)
            if pics:
                return pics[0].data

            # MP4 / M4A: covr atom
            try:
                covr = f.tags.get('covr') if hasattr(f, 'tags') and f.tags else None
                if covr:
                    return bytes(covr[0])
            except Exception:
                pass
        except Exception:
            pass
        return None

    def save_metadata(self, dest_folder):
        meta_dir = os.path.join(dest_folder, "metadata")
        os.makedirs(meta_dir, exist_ok=True)
        
        export_metadata = []
        for track in self.tracks:
            export_metadata.append({
                "current_file": track['filename'],
                "original_name": track['original_name'],
                "artist": track.get('artist', ''),
                "album": track.get('album', ''),
                "song": track.get('song', track['original_name']),
                "thumb_filename": track.get('thumb_filename', ''),
                "bpm": track['bpm'],
                "tone": track['tone'],
                "duration": track['duration'],
                "volume": track.get('volume', 100.0),
                "lufs": track.get('lufs', -14.0),
                "size_mb": track.get('size_mb', 0.0),
                "is_normalized": track.get('is_normalized', False),
                "inactive": track.get('inactive', False),
                "denoised_filename": track.get('denoised_filename', ''),
                "noise_filename": track.get('noise_filename', ''),
                "use_denoised": track.get('use_denoised', False),
                "denoise_mix": float(track.get('denoise_mix', 1.0)),
                "dsp_state": track.get('dsp_state', {
                    'master_bypass': True,
                    'chain_order': ['eq', 'dyn'],
                    'eq_bypass': True, 'eq_low': 0.0, 'eq_mid': 0.0, 'eq_high': 0.0,
                    'dyn_bypass': True, 'dyn_threshold': 0.0, 'dyn_ratio': 1.0, 'dyn_attack': 5.0, 'dyn_release': 100.0, 'dyn_makeup': 0.0,
                    'limiter_bypass': True, 'limiter_softclip': False, 'limiter_input': 0.0, 'limiter_output': 0.0,
                    'vsts': {}
                })
            })
            
        with open(os.path.join(meta_dir, "tracks.json"), "w") as f:
            json.dump(export_metadata, f, indent=4)
            
        with open(os.path.join(meta_dir, "settings.json"), "w") as f:
            json.dump(self.settings, f, indent=4)
            
        self.needs_save = False
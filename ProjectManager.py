import os
import json
import re
from pathlib import Path
from mutagen.id3 import ID3, APIC

class ProjectState:
    def __init__(self):
        self.tracks = []
        self.current_folder = ""
        self.is_saved_set = False
        self.settings = {"normalized": False, "lufs_target": -14.0}
        self.needs_save = False
        
        self.state_dir = os.path.join(os.getcwd(), "app_states")
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
        new_idx = max(0, min(len(self.tracks) - 1, idx + offset))
        if idx == new_idx: return new_idx
        track = self.tracks.pop(idx)
        self.tracks.insert(new_idx, track)
        return new_idx

    def resort_by_bpm(self):
        self.tracks.sort(key=lambda x: x['bpm'])

    def is_bpm_sorted(self):
        """Checks if the current tracklist is still perfectly sorted by BPM"""
        if len(self.tracks) <= 1:
            return True
        return all(self.tracks[i]['bpm'] <= self.tracks[i+1]['bpm'] for i in range(len(self.tracks)-1))

    def modify_bpm(self, idx, multiplier):
        track = self.tracks[idx]
        track['bpm'] = round(track['bpm'] * multiplier)
        return track

    def get_track_artwork_bytes(self, filepath):
        """Attempts to extract binary ID3 APIC data from an MP3 file."""
        if not filepath.lower().endswith('.mp3'):
            return None
        try:
            tags = ID3(filepath)
            # Fetch all Attached Picture tags
            apic_tags = tags.getall('APIC')
            if apic_tags:
                return apic_tags[0].data
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
                "bpm": track['bpm'],
                "tone": track['tone'],
                "duration": track['duration'],
                "volume": track.get('volume', 100.0),
                "lufs": track.get('lufs', -14.0),
                "size_mb": track.get('size_mb', 0.0),
                "is_normalized": track.get('is_normalized', False),
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
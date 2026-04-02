import os
import shutil
import threading
import time
import datetime
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

class ProjectActions:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.project = app.project
        self.audio = app.audio

    def get_workspace_dir(self):
        last_dir = self.project.current_folder or self.project.load_app_memory()
        if last_dir and os.path.exists(last_dir):
            return os.path.dirname(last_dir) 
        return None

    def create_new_project(self):
        initial_ws = self.get_workspace_dir()
        parent_dir = filedialog.askdirectory(title="Select Location for New Project", initialdir=initial_ws, parent=self.root)
        if not parent_dir: return

        project_name = simpledialog.askstring("Project Name", "Enter a name for your DJ Set:", parent=self.root)
        if not project_name: return

        project_name = "".join(x for x in project_name if x.isalnum() or x in " _-").strip()
        if not project_name: project_name = "Untitled_DJ_Set"

        project_dir = os.path.join(parent_dir, project_name)
        os.makedirs(project_dir, exist_ok=True)

        self.audio.unload()
        self.project.current_folder = project_dir
        self.project.is_saved_set = True
        self.project.tracks = []
        self.project.settings = {"normalized": False, "lufs_target": -14.0}
        
        self.app.refresh_tree()
        self.app.progress_var.set(0)
        self.app.progress_bar.pack_forget()
        self.app.update_norm_led()
        
        self.app.lbl_folder.config(text=f"Project: {project_name}")
        self.app.btn_add.config(state=tk.NORMAL)
        
        if self.app.vinyl_animator:
            self.app.vinyl_animator.update_artwork(None)
            self.app.vinyl_animator.set_speed(0.0)

        self.project.save_metadata(project_dir)
        self.project.save_app_memory()
        
        messagebox.showinfo("Project Created", f"Workspace '{project_name}' is ready.\n\nClick '+ Add Track(s)' to begin importing audio.", parent=self.root)

    def load_saved_set(self, preset_folder=None):
        initial_ws = self.get_workspace_dir()
        folder = preset_folder if preset_folder else filedialog.askdirectory(title="Select Saved Set", initialdir=initial_ws)
        if not folder: return
        
        meta_path = os.path.join(folder, "metadata", "tracks.json")

        if not os.path.exists(meta_path):
            if messagebox.askyesno("Project Not Found", "This folder does not contain project metadata.\n\nWould you like to scan it for audio files and create a new project here?", parent=self.root):
                self._import_folder_as_new_project(folder)
            return

        try:
            with open(meta_path, "r") as f:
                raw_data = json.load(f)
        except Exception:
            if messagebox.askyesno("Project Corrupted", "Project metadata is unreadable.\n\nWould you like to attempt to rebuild the project from the audio files in this folder?", parent=self.root):
                self._import_folder_as_new_project(folder)
            return

        self.audio.unload()
        self.project.current_folder = folder
        self.project.is_saved_set = True
        self.app.btn_add.config(state=tk.NORMAL) 
        self.project.save_app_memory() 
        self.app.progress_bar.pack_forget()
        
        settings_path = os.path.join(folder, "metadata", "settings.json")
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r") as f: self.project.settings = json.load(f)
            except Exception:
                self.project.settings = {"normalized": False, "lufs_target": -14.0}
        else:
            self.project.settings = raw_data.get("settings", {"normalized": False, "lufs_target": -14.0}) if isinstance(raw_data, dict) else {"normalized": False, "lufs_target": -14.0}

        self.app.update_norm_led() 
        track_list = raw_data.get("tracks", raw_data) if isinstance(raw_data, dict) else raw_data
        self.project.tracks = []

        for t in track_list:
            fname = t.get("current_file", t.get("filename", ""))
            if fname and os.path.exists(os.path.join(folder, fname)):
                if t.get("duration", 0) == 0:
                    try:
                        import librosa
                        t['duration'] = float(librosa.get_duration(path=os.path.join(folder, fname)))
                    except Exception: t['duration'] = 0.0
                
                self.project.tracks.append({
                    "filename": fname, "original_name": t.get("original_name", self.project.clean_name(fname)),
                    "artist": t.get("artist", ""), "album": t.get("album", ""), "song": t.get("song", ""),
                    "thumb_filename": t.get("thumb_filename", ""),
                    "bpm": int(t.get("bpm", 0)), "tone": str(t.get("tone", "?")),
                    "duration": float(t.get("duration", 0.0)), "volume": float(t.get("volume", 100.0)),
                    "lufs": float(t.get("lufs", -14.0)), "size_mb": float(t.get("size_mb", 0.0)),
                    "is_normalized": t.get("is_normalized", self.project.settings.get("normalized", False)),
                    "dsp_state": t.get("dsp_state", {
                        'master_bypass': True,
                        'chain_order': ['eq', 'dyn'],
                        'eq_bypass': True, 'eq_low': 0.0, 'eq_mid': 0.0, 'eq_high': 0.0,
                        'dyn_bypass': True, 'dyn_threshold': 0.0, 'dyn_ratio': 1.0, 'dyn_attack': 5.0, 'dyn_release': 100.0, 'dyn_makeup': 0.0,
                        'limiter_bypass': True, 'limiter_softclip': False, 'limiter_input': 0.0, 'limiter_output': 0.0,
                        'vsts': {}
                    })
                })
                
        self.app.refresh_tree()
        self.app.lbl_folder.config(text=f"Loaded: {os.path.basename(folder)}")
        
        if self.app.vinyl_animator:
            self.app.vinyl_animator.update_artwork(None)
            self.app.vinyl_animator.set_speed(0.0)

        self.app.select_first_track()

    def _import_folder_as_new_project(self, folder):
        # Find audio files
        audio_files = []
        valid_exts = ['.mp3', '.wav', '.flac']
        for f in sorted(os.listdir(folder)):
            if any(f.lower().endswith(ext) for ext in valid_exts) and not f.startswith('.'):
                audio_files.append(f)
        
        if not audio_files:
            messagebox.showinfo("No Audio Found", "No compatible audio files (.mp3, .wav, .flac) were found in this folder.", parent=self.root)
            return

        # Check for existing numerical order to decide on final sorting
        import re
        numbered_files = []
        has_unnumbered = False
        for f_name in audio_files:
            match = re.match(r'^(\d+)\s*[-_.]*\s*(.*)', f_name)
            if match:
                numbered_files.append({'filename': f_name, 'num': int(match.group(1))})
            else:
                has_unnumbered = True
                break
        
        use_existing_order = False
        if not has_unnumbered and len(numbered_files) == len(audio_files):
            numbered_files.sort(key=lambda x: x['num'])
            files_to_process = [f['filename'] for f in numbered_files]
            use_existing_order = True
        else:
            files_to_process = audio_files

        # Set up project state for a new project in this folder
        self.audio.unload()
        self.project.current_folder = folder
        self.project.is_saved_set = True
        self.project.tracks = []
        self.project.settings = {"normalized": False, "lufs_target": -14.0}
        
        self.app.refresh_tree()
        self.app.progress_var.set(0)
        self.app.progress_bar.pack_forget()
        self.app.update_norm_led()
        
        self.app.lbl_folder.config(text=f"Project: {os.path.basename(folder)}")
        self.app.btn_add.config(state=tk.NORMAL)
        
        if self.app.vinyl_animator:
            self.app.vinyl_animator.update_artwork(None)
            self.app.vinyl_animator.set_speed(0.0)

        self.project.save_metadata(folder)
        self.project.save_app_memory()
        
        self.add_tracks(files=[os.path.join(folder, f) for f in files_to_process], sort_by_bpm_at_end=not use_existing_order, select_first_at_end=True)

    def add_tracks(self, files=None, sort_by_bpm_at_end=False, select_first_at_end=False):
        if not self.project.is_saved_set: return
        if not files:
            files = filedialog.askopenfilenames(title="Select Tracks to Import", filetypes=[("Audio Files", "*.mp3 *.wav *.flac")], parent=self.root)
        if not files: return
        
        normalize_new = False
        if self.project.settings.get("normalized", False):
            lufs = self.project.settings.get("lufs_target", -14.0)
            normalize_new = messagebox.askyesno("Project Normalized", f"This project was previously exported with audio normalization ({lufs} LUFS).\n\nNormalize these {len(files)} new track(s)?", parent=self.root)

        self.app.btn_add.config(state=tk.DISABLED)
        self.app.lbl_folder.config(text=f"Importing {len(files)} tracks...")
        self.app.progress_bar.pack(fill=tk.X, pady=5)
        self.app.progress_var.set(0)
        should_sort = self.project.is_bpm_sorted() or sort_by_bpm_at_end
        threading.Thread(target=self.add_tracks_worker, args=(list(files), normalize_new, should_sort, select_first_at_end), daemon=True).start()

    def add_tracks_worker(self, filepaths, normalize_new, was_sorted, select_first_at_end=False):
        total, lufs_target = len(filepaths), self.project.settings.get("lufs_target", -14.0)
        for i, filepath in enumerate(filepaths):
            path_obj, ext = Path(filepath), Path(filepath).suffix.lower()
            original_name = self.project.clean_name(path_obj.name) 
            new_filename = f"{len(self.project.tracks) + 1:02d} - {original_name}"
            if not new_filename.endswith('.mp3'): new_filename += '.mp3'
            dest_path = os.path.join(self.project.current_folder, new_filename)
            try:
                if ext in ['.wav', '.flac'] or normalize_new: self.audio.convert_to_mp3(path_obj, dest_path, normalize=normalize_new, lufs=lufs_target, volume=1.0)
                else: shutil.copy2(filepath, dest_path)
                bpm, tone, duration, lufs_approx, size_mb = self.audio.analyze_track(dest_path)
                
                artist, album, song = self.project.get_track_metadata(dest_path)
                if not song: song = original_name
                
                thumb_filename = ""
                art_bytes = self.project.get_track_artwork_bytes(dest_path)
                if art_bytes:
                    try:
                        from PIL import Image, ImageOps
                        import io
                        img = Image.open(io.BytesIO(art_bytes)).convert("RGBA")
                        img = ImageOps.fit(img, (36, 36), Image.Resampling.LANCZOS)
                        thumb_name = f".thumb_{i}_{int(time.time())}.png"
                        thumb_dest = os.path.join(self.project.current_folder, thumb_name)
                        img.save(thumb_dest)
                        thumb_filename = thumb_name
                    except Exception as e:
                        print(f"Thumb error: {e}")
                        
                self.project.tracks.append({
                    'filename': new_filename, 'original_name': original_name,
                    'artist': artist, 'album': album, 'song': song, 'thumb_filename': thumb_filename,
                    'bpm': bpm, 'tone': tone, 'duration': duration, 
                    'volume': 100.0, 'lufs': lufs_approx, 'size_mb': size_mb, 'is_normalized': normalize_new,
                    'dsp_state': {
                        'master_bypass': True,
                        'chain_order': ['eq', 'dyn'],
                        'eq_bypass': True, 'eq_low': 0.0, 'eq_mid': 0.0, 'eq_high': 0.0,
                        'dyn_bypass': True, 'dyn_threshold': 0.0, 'dyn_ratio': 1.0, 'dyn_attack': 5.0, 'dyn_release': 100.0, 'dyn_makeup': 0.0,
                        'limiter_bypass': True, 'limiter_softclip': False, 'limiter_input': 0.0, 'limiter_output': 0.0,
                        'vsts': {}
                    }
                })
            except Exception as e: print(f"Error adding track: {e}")
            self.root.after(0, self.app.progress_var.set, ((i + 1) / total) * 100)

        def on_done():
            if was_sorted:
                self.project.resort_by_bpm()
            self.app.refresh_tree()
            self.app.lbl_folder.config(text=f"Loaded: {os.path.basename(self.project.current_folder)}")
            self.app.btn_add.config(state=tk.NORMAL)
            self.app.progress_bar.pack_forget()
            self.project.needs_save = True
            if select_first_at_end:
                self.app.select_first_track()

        self.root.after(0, on_done)
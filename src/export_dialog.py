import os
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from constants import BG_MAIN, BG_LIST, FG_TEXT, HIGHLIGHT, BTN_NORMAL, BTN_HOVER
from ui_components import create_btn
from AudioCore import get_ffmpeg_path

class ExportDialog:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        
    def open_export_window(self):
        if not self.app.project.tracks:
            messagebox.showwarning("Warning", "Load some tracks before rendering.")
            return

        win = tk.Toplevel(self.root)
        win.title("Export / Render Audio")
        win.geometry("540x600")
        win.configure(bg=BG_MAIN)
        win.grab_set()

        tk.Label(win, text="Audio Render Settings", bg=BG_MAIN, fg=HIGHLIGHT, font=("Helvetica", 12, "bold")).pack(pady=15)

        self.render_mode = tk.StringVar(value="inplace" if self.app.project.is_saved_set else "new")
        def update_mode():
            if self.render_mode.get() == "new": self.btn_browse.config(state=tk.NORMAL, fg=FG_TEXT)
            else: self.btn_browse.config(state=tk.DISABLED, fg="#555555")

        tk.Radiobutton(win, text="Export to New Folder (Safe Copy):", variable=self.render_mode, value="new", 
                       bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_LIST, activebackground=BG_MAIN, activeforeground=FG_TEXT, command=update_mode).pack(anchor="w", padx=40)
        
        path_frame = tk.Frame(win, bg=BG_MAIN)
        path_frame.pack(fill=tk.X, padx=60, pady=(0, 10))
        default_export = ""
        if self.app.project.current_folder:
            folder_name = os.path.basename(self.app.project.current_folder)
            default_export = os.path.join(os.path.dirname(self.app.project.current_folder), f"{folder_name}_Export")
        self.export_path_var = tk.StringVar(value=default_export)
        tk.Label(path_frame, textvariable=self.export_path_var, bg=BG_MAIN, fg="#aaaaaa", font=("Helvetica", 9)).pack(side=tk.LEFT)
        self.btn_browse = create_btn(path_frame, "📂 Browse", lambda: self.browse_export_dir(win), BTN_NORMAL)
        self.btn_browse.pack(side=tk.LEFT, padx=10)

        self.rb_inplace = tk.Radiobutton(win, text="Render In-Place (Overwrites files, saves final order)", variable=self.render_mode, value="inplace",
                                    bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_LIST, activebackground=BG_MAIN, activeforeground=FG_TEXT, command=update_mode)
        self.rb_inplace.pack(anchor="w", padx=40, pady=(5, 10))
        if not self.app.project.is_saved_set: self.rb_inplace.config(state=tk.DISABLED)
        update_mode()

        tk.Label(win, text="Processing:", bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 9, "bold")).pack(anchor="w", padx=40, pady=(10, 5))
        is_already_norm = self.app.project.settings.get("normalized", False)
        self.norm_var = tk.BooleanVar(value=not is_already_norm)
        tk.Checkbutton(win, text=" Normalize Audio (Equalize Gains to -14 LUFS)", variable=self.norm_var, bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_LIST, activebackground=BG_MAIN, activeforeground=FG_TEXT, command=self.update_export_warnings).pack(anchor="w", padx=40)

        self.bake_dsp_var = tk.BooleanVar(value=True)
        tk.Checkbutton(win, text=" Bake-in DSP (Effects & Volume)", variable=self.bake_dsp_var, bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_LIST, activebackground=BG_MAIN, activeforeground=FG_TEXT, command=self.update_export_warnings).pack(anchor="w", padx=40)

        self.continuous_var = tk.BooleanVar(value=False)
        def update_continuous_mode():
            if self.continuous_var.get():
                self.render_mode.set("new")
                self.rb_inplace.config(state=tk.DISABLED)
            elif self.app.project.is_saved_set:
                self.rb_inplace.config(state=tk.NORMAL)
            update_mode()
        tk.Checkbutton(win, text=" Export as Single Continuous MP3 (one combined file)", variable=self.continuous_var, bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_LIST, activebackground=BG_MAIN, activeforeground=FG_TEXT, command=update_continuous_mode).pack(anchor="w", padx=40)

        self.lbl_warning = tk.Label(win, text="⚠️ Warning: Normalizing while baking DSP/volume may cause\nunexpected loudness, as the normalizer fights the manual offsets.", fg="#e2a84a", bg=BG_MAIN, justify=tk.LEFT)
        self.update_export_warnings()

        tk.Label(win, text="MP3 Compression Level:", bg=BG_MAIN, fg=FG_TEXT).pack(anchor="w", padx=40, pady=(10, 0))
        self.comp_var = tk.StringVar(value="None (Highest Quality)")
        options = ["None (Highest Quality)", "Light (~190 kbps)", "Medium (~165 kbps)", "Heavy (~115 kbps)", "Extreme / Demo (~85 kbps)"]
        dropdown = ttk.Combobox(win, textvariable=self.comp_var, values=options, state="readonly", width=35)
        dropdown.pack(fill=tk.X, padx=40, pady=5)

        self.render_progress_var = tk.DoubleVar()
        ttk.Progressbar(win, variable=self.render_progress_var, maximum=100).pack(fill=tk.X, padx=40, pady=15)
        self.lbl_render_status = tk.Label(win, text="Ready.", bg=BG_MAIN, fg="#aaaaaa")
        self.lbl_render_status.pack()
        self.btn_start_render = create_btn(win, "Start Render", lambda: self.start_render_thread(win), "#2e7d32", bold=True)
        self.btn_start_render.pack(pady=10)

    def update_export_warnings(self):
        if self.norm_var.get() and self.bake_dsp_var.get():
            self.lbl_warning.pack(anchor="w", padx=40, pady=(0, 5))
        else:
            self.lbl_warning.pack_forget()

    def browse_export_dir(self, window):
        if str(self.btn_browse.cget("state")) == tk.DISABLED: return
        d = filedialog.askdirectory(parent=window, title="Select Destination Folder")
        if d:
            folder_name = os.path.basename(self.app.project.current_folder) or "DJ_Set"
            self.export_path_var.set(os.path.join(d, f"{folder_name}_Export"))

    def start_render_thread(self, window):
        continuous = self.continuous_var.get()
        in_place = (self.render_mode.get() == "inplace") and not continuous
        if in_place: export_dir = self.app.project.current_folder
        else:
            export_dir = self.export_path_var.get()
            if not export_dir:
                messagebox.showwarning("Warning", "Please select a valid export directory.", parent=window)
                return
            os.makedirs(export_dir, exist_ok=True)
        self.btn_start_render.config(state=tk.DISABLED)
        if continuous:
            threading.Thread(target=self.render_continuous_worker, args=(window, export_dir), daemon=True).start()
        else:
            threading.Thread(target=self.render_worker, args=(window, export_dir, in_place), daemon=True).start()

    def render_continuous_worker(self, window, export_dir):
        try:
            tracks = [t for t in self.app.project.tracks if not t.get('inactive', False)]
            if not tracks:
                self.app.root.after(0, messagebox.showwarning, "Nothing to Render", "No active tracks to render. (All tracks are marked Removed.)", parent=window)
                self.app.root.after(0, self.btn_start_render.config, {"state": tk.NORMAL})
                return

            comp_map = {"None (Highest Quality)": "0", "Light (~190 kbps)": "2", "Medium (~165 kbps)": "4", "Heavy (~115 kbps)": "6", "Extreme / Demo (~85 kbps)": "8"}
            quality_flag = comp_map.get(self.comp_var.get(), "0")
            normalize = self.norm_var.get()
            bake_dsp = self.bake_dsp_var.get()
            lufs_target = -14.0

            try: self.app.audio.unload()
            except Exception: pass

            temp_paths = []
            n = len(tracks)
            for i, track in enumerate(tracks):
                source_path = os.path.join(self.app.project.current_folder, track['filename'])
                temp_name = f".tmp_continuous_{i:03d}.mp3"
                temp_path = os.path.join(export_dir, temp_name)

                volume = (track.get('volume', 100.0) / 100.0) if bake_dsp else 1.0
                track_dsp = track.get('dsp_state', {
                    'master_bypass': True,
                    'chain_order': ['eq', 'dyn'],
                    'eq_bypass': True, 'eq_low': 0.0, 'eq_mid': 0.0, 'eq_high': 0.0,
                    'dyn_bypass': True, 'dyn_threshold': 0.0, 'dyn_ratio': 1.0, 'dyn_attack': 5.0, 'dyn_release': 100.0, 'dyn_makeup': 0.0,
                    'limiter_bypass': True, 'limiter_softclip': False, 'limiter_input': 0.0, 'limiter_output': 0.0,
                    'vsts': {}
                })
                if not bake_dsp:
                    track_dsp = track_dsp.copy()
                    track_dsp['master_bypass'] = True
                self.app.audio.dsp_state = track_dsp

                is_dsp_active = not track_dsp.get('master_bypass', False) and (
                    not track_dsp.get('eq_bypass', False) or
                    not track_dsp.get('dyn_bypass', False) or
                    not track_dsp.get('limiter_bypass', False) or
                    any(not v.get('bypass', True) for v in track_dsp.get('vsts', {}).values())
                )
                needs_render = normalize or quality_flag != "0" or volume != 1.0 or is_dsp_active or not source_path.lower().endswith('.mp3')

                self.app.root.after(0, self.lbl_render_status.config, {"text": f"Rendering ({i+1}/{n}): {track.get('song') or track.get('original_name', track['filename'])}"})
                try:
                    if not needs_render:
                        shutil.copy2(source_path, temp_path)
                    else:
                        self.app.audio.render_export_track(source_path, temp_path, normalize, quality_flag, lufs_target, volume)
                    temp_paths.append(temp_path)
                except Exception as e:
                    print(f"Continuous render error on {track.get('filename')}: {e}")

                self.app.root.after(0, self.render_progress_var.set, ((i + 1) / n) * 95.0)

            if not temp_paths:
                raise RuntimeError("No tracks were rendered successfully.")

            self.app.root.after(0, self.lbl_render_status.config, {"text": "Concatenating into single MP3..."})
            folder_basename = os.path.basename(self.app.project.current_folder) or "DJ_Set"
            final_name = f"{folder_basename}_Continuous.mp3"
            final_path = os.path.join(export_dir, final_name)
            list_path = os.path.join(export_dir, ".concat_list.txt")
            try:
                with open(list_path, 'w') as fh:
                    for p in temp_paths:
                        escaped = p.replace("'", "'\\''")
                        fh.write(f"file '{escaped}'\n")

                cmd = [get_ffmpeg_path(), '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c', 'copy', final_path]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            finally:
                for p in temp_paths:
                    try: os.remove(p)
                    except Exception: pass
                try: os.remove(list_path)
                except Exception: pass

            self.app.root.after(0, self.render_progress_var.set, 100.0)
            self.app.root.after(0, self.lbl_render_status.config, {"text": "Done."})
            self.app.root.after(0, self.finish_continuous_render, window, final_path)

        except Exception as crash_error:
            print(f"CONTINUOUS RENDER CRASH: {crash_error}")
            self.app.root.after(0, messagebox.showerror, "Render Failed", f"Continuous render failed:\n\n{crash_error}", parent=window)
            self.app.root.after(0, self.btn_start_render.config, {"state": tk.NORMAL})

    def finish_continuous_render(self, window, final_path):
        window.destroy()
        messagebox.showinfo("Export Complete", f"Continuous mix written to:\n\n{final_path}")

    def render_worker(self, window, export_dir, in_place):
        try:
            total = len(self.app.project.tracks)
            comp_map = {"None (Highest Quality)": "0", "Light (~190 kbps)": "2", "Medium (~165 kbps)": "4", "Heavy (~115 kbps)": "6", "Extreme / Demo (~85 kbps)": "8"}
            quality_flag = comp_map.get(self.comp_var.get(), "0") 
            normalize = self.norm_var.get()
            bake_dsp = self.bake_dsp_var.get()
            lufs_target = -14.0

            if not hasattr(self.app.project, 'settings') or not isinstance(self.app.project.settings, dict): self.app.project.settings = {}
            if normalize:
                self.app.project.settings["normalized"] = True
                self.app.project.settings["lufs_target"] = lufs_target
            self.app.root.after(0, self.app.update_norm_led)

            try: self.app.audio.unload()
            except Exception: pass

            original_paths = []
            temp_file_mappings = []

            if in_place:
                for track in self.app.project.tracks:
                    original_paths.append(os.path.join(export_dir, track['filename']))

            for i, track in enumerate(self.app.project.tracks):
                original_name = track.get('original_name', 'track')
                new_filename = f"{i + 1:02d} - {original_name}"
                if not new_filename.lower().endswith('.mp3'): new_filename += '.mp3'
                
                source_path = os.path.join(self.app.project.current_folder, track['filename'])
                volume = (track.get('volume', 100.0) / 100.0) if bake_dsp else 1.0
                track_dsp = track.get('dsp_state', {
                    'master_bypass': True,
                    'chain_order': ['eq', 'dyn'],
                    'eq_bypass': True, 'eq_low': 0.0, 'eq_mid': 0.0, 'eq_high': 0.0,
                    'dyn_bypass': True, 'dyn_threshold': 0.0, 'dyn_ratio': 1.0, 'dyn_attack': 5.0, 'dyn_release': 100.0, 'dyn_makeup': 0.0,
                    'limiter_bypass': True, 'limiter_softclip': False, 'limiter_input': 0.0, 'limiter_output': 0.0,
                    'vsts': {}
                })
                
                if not bake_dsp:
                    track_dsp = track_dsp.copy()
                    track_dsp['master_bypass'] = True
                    
                self.app.audio.dsp_state = track_dsp
                is_dsp_active = not track_dsp.get('master_bypass', False) and (
                    not track_dsp.get('eq_bypass', False) or 
                    not track_dsp.get('dyn_bypass', False) or 
                    not track_dsp.get('limiter_bypass', False) or 
                    any(not v.get('bypass', True) for v in track_dsp.get('vsts', {}).values())
                )
                
                if in_place:
                    dest_path = os.path.join(export_dir, f"temp_render_{i}_{new_filename}")
                    final_path = os.path.join(export_dir, new_filename)
                else:
                    dest_path = os.path.join(export_dir, new_filename)
                    final_path = dest_path

                self.app.root.after(0, self.lbl_render_status.config, {"text": f"Rendering ({i+1}/{total}): {new_filename}..."})
                needs_render = normalize or quality_flag != "0" or volume != 1.0 or is_dsp_active or not source_path.lower().endswith('.mp3')

                try:
                    if not needs_render:
                        if in_place:
                            shutil.copy2(source_path, dest_path)
                        else:
                            if source_path != final_path:
                                shutil.copy2(source_path, final_path)
                    else:
                        self.app.audio.render_export_track(source_path, dest_path, normalize, quality_flag, lufs_target, volume)
                        
                    if in_place:
                        temp_file_mappings.append((dest_path, final_path))

                    track['filename'] = new_filename
                    if not in_place:
                        thumb_filename = track.get('thumb_filename', '')
                        if thumb_filename:
                            src_thumb = os.path.join(self.app.project.current_folder, thumb_filename)
                            dst_thumb = os.path.join(export_dir, thumb_filename)
                            if os.path.exists(src_thumb) and src_thumb != dst_thumb:
                                try: shutil.copy2(src_thumb, dst_thumb)
                                except Exception: pass
                    if normalize:
                        track['is_normalized'] = True
                        track['lufs'] = lufs_target
                    
                    if bake_dsp:
                        if track.get('volume', 100.0) != 100.0:
                            track['volume'] = 100.0
                        if is_dsp_active:
                            track['dsp_state']['eq_bypass'] = True
                            track['dsp_state']['eq_low'] = 0.0
                            track['dsp_state']['eq_mid'] = 0.0
                            track['dsp_state']['eq_high'] = 0.0
                            
                            track['dsp_state']['dyn_bypass'] = True
                            track['dsp_state']['dyn_threshold'] = 0.0
                            track['dsp_state']['dyn_ratio'] = 1.0
                            track['dsp_state']['dyn_attack'] = 5.0
                            track['dsp_state']['dyn_release'] = 100.0
                            track['dsp_state']['dyn_makeup'] = 0.0
                            
                            track['dsp_state']['limiter_bypass'] = True
                            track['dsp_state']['limiter_softclip'] = False
                            track['dsp_state']['limiter_input'] = 0.0
                            track['dsp_state']['limiter_output'] = 0.0
                            
                            if 'vsts' in track['dsp_state']:
                                for vst in track['dsp_state']['vsts'].values():
                                    vst['bypass'] = True
                                    
                            track['dsp_state']['master_bypass'] = True

                    if not in_place and os.path.exists(final_path):
                        track['size_mb'] = float(os.path.getsize(final_path) / (1024 * 1024))
                        
                except Exception as e: 
                    print(f"Render Error on {track.get('filename')}: {e}")
                    if in_place and os.path.exists(dest_path):
                        try: os.remove(dest_path)
                        except: pass
                        
                self.app.root.after(0, self.render_progress_var.set, ((i + 1) / total) * 100)

            if in_place:
                self.app.root.after(0, self.lbl_render_status.config, {"text": "Finalizing files..."})
                
                expected_final_paths = {fp for _, fp in temp_file_mappings}
                
                for temp_path, final_path in temp_file_mappings:
                    if os.path.exists(temp_path):
                        try: os.replace(temp_path, final_path)
                        except Exception as e: print(f"Replace error: {e}")
                        
                active_filenames = {t.get('filename') for t in self.app.project.tracks}
                for old_path in set(original_paths):
                    old_filename = os.path.basename(old_path)
                    if old_filename not in active_filenames and old_path not in expected_final_paths:
                        if os.path.exists(old_path):
                            try: os.remove(old_path)
                            except Exception as e: print(f"Cleanup error: {e}")
                        
                for track in self.app.project.tracks:
                    final_path = os.path.join(export_dir, track['filename'])
                    if os.path.exists(final_path):
                        track['size_mb'] = float(os.path.getsize(final_path) / (1024 * 1024))

            if not in_place: self.app.project.current_folder = export_dir
            self.app.project.is_saved_set = True
            self.app.project.needs_save = True 
            self.app.project.save_app_memory()
            self.app.project.save_metadata(export_dir)
            self.app.root.after(0, self.finish_render, window, export_dir)

        except Exception as crash_error:
            print(f"CRITICAL RENDER CRASH: {crash_error}")
            self.app.root.after(0, messagebox.showerror, "Critical Error", f"The background render engine crashed:\n\n{crash_error}", parent=window)
            self.app.root.after(0, window.destroy)

    def finish_render(self, window, export_dir):
        window.destroy()
        self.app.project_actions.load_saved_set(preset_folder=export_dir)
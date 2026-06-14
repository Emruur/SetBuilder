import os
import sys
import copy
import threading
import time
import datetime
import random
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from difflib import SequenceMatcher
import numpy as np

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    _HAS_DND = False

from AudioCore import AudioEngine
from ProjectManager import ProjectState
from PIL import Image, ImageTk, ImageOps, ImageEnhance

from constants import BG_MAIN, BG_LIST, FG_TEXT, BTN_NORMAL, BTN_HOVER, HIGHLIGHT, BORDER, VINYL_SIZE, CENTER_HOLE, DENOISER_DOWNLOAD_URL, DENOISER_INSTALL_DIR
from ui_components import create_btn, create_group_frame, Knob, Timeline
from export_dialog import ExportDialog
from vinyl_animator import VinylAnimator
from project_actions import ProjectActions

def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running in a PyInstaller bundle
        base_path = sys._MEIPASS
    else:
        # Running in a normal Python environment
        # Go up from src/ to the project root where assets/ lives
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


class DJAppUI:
    def __init__(self, root):
        self.root = root
        self.root.title("DJ Set Builder Pro")
        self.root.geometry("1180x800") 
        self.root.configure(bg=BG_MAIN)

        self.audio = AudioEngine()
        self.project = ProjectState()
        self.project_actions = ProjectActions(self)
        self.vinyl_animator = None

        self.song_length = 0
        self.seek_offset = 0
        self.is_dragging_slider = False
        self.update_job = None
        self._undo_stack = []
        self._redo_stack = []

        # Unified background-task queue
        self._tasks = {}       # task_id -> {'label': str}
        self._task_order = []  # insertion order for display priority
        self._spinner_idx = 0
        self._spinner_job = None
        self._denoising_indices = set()  # track indices currently queued or processing
        self.denoise_mix_var = tk.DoubleVar(value=100.0)
        self._saved_denoise_state = False  # denoise state saved before master bypass

        self._drop_leave_timer = None

        self._drag_src_idx = None
        self._drag_y_start = 0
        self._drag_active = False
        self._drop_indicator = None
        self._drop_intended_idx = None
        
        self.is_keyboard_scrubbing = False
        self.scrub_speed = 1.0
        self.scrub_timer = None
        self.keys_down = {'m': False, 'v': False}

        self.setup_ui()
        self.bind_shortcuts()
        self._setup_file_drop()
        self.root.after(100, self.auto_load_last_session)
        
        threading.Thread(target=self.autosave_daemon, daemon=True).start()

    def autosave_daemon(self):
        while True:
            time.sleep(1.0)
            if self.project.is_saved_set and getattr(self.project, 'needs_save', False):
                try:
                    self.project.save_metadata(self.project.current_folder)
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Auto-saved metadata.", flush=True)
                except Exception:
                    pass

    # ── Task queue ──────────────────────────────────────────────────────────

    def _enqueue_task(self, task_id, label):
        self._tasks[task_id] = {'label': label}
        self._task_order.append(task_id)
        if self._spinner_job is None:
            self._tick_spinner()

    def _update_task_label(self, task_id, label):
        if task_id in self._tasks:
            self._tasks[task_id]['label'] = label

    def _finish_task(self, task_id):
        self._tasks.pop(task_id, None)
        try:
            self._task_order.remove(task_id)
        except ValueError:
            pass
        if not self._tasks:
            if self._spinner_job:
                self.root.after_cancel(self._spinner_job)
                self._spinner_job = None
            self._spinner_lbl.config(text="")

    def _tick_spinner(self):
        frames = ['⣾', '⣽', '⣻', '⢿', '⡿', '⣟', '⣯', '⣷']
        f = frames[self._spinner_idx % len(frames)]

        if self._task_order and self._tasks:
            primary = self._tasks.get(self._task_order[0])
            if primary:
                waiting = len(self._tasks) - 1
                text = f"{f}  {primary['label']}"
                if waiting > 0:
                    text += f"  ·  {waiting} waiting"
                self._spinner_lbl.config(text=text)

        # Animate rack spinner when selected track is being denoised
        if self._denoising_indices and hasattr(self, '_dr_spinner_lbl'):
            if self.get_selected_idx() in self._denoising_indices:
                try:
                    self._dr_spinner_lbl.config(text=f"{f}  Processing…")
                except tk.TclError:
                    pass

        self._spinner_idx += 1
        self._spinner_job = self.root.after(80, self._tick_spinner)

    def create_group_frame(self, parent, padx=5, pady=5):
        return create_group_frame(parent, padx, pady)

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TProgressbar", background=HIGHLIGHT, troughcolor=BG_LIST)
        style.configure("TScale", background=BG_MAIN, troughcolor=BG_LIST)
        style.configure("Vertical.TScale", background=BG_MAIN, troughcolor=BG_LIST)
        
        style.configure("TCombobox", fieldbackground=BTN_NORMAL, background=BTN_NORMAL, foreground=FG_TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", BTN_NORMAL)], selectbackground=[("readonly", HIGHLIGHT)], foreground=[("readonly", FG_TEXT)])
        
        # Dark Theme Scrollbar Styling
        style.configure("Vertical.TScrollbar", gripcount=0,
                        background=BTN_NORMAL, troughcolor=BG_LIST,
                        bordercolor=BORDER, arrowcolor=FG_TEXT,
                        lightcolor=BTN_NORMAL, darkcolor=BTN_NORMAL)
        style.map("Vertical.TScrollbar", background=[('active', BTN_HOVER)])

        self.root.option_add('*TCombobox*Listbox.background', BG_LIST)
        self.root.option_add('*TCombobox*Listbox.foreground', FG_TEXT)
        self.root.option_add('*TCombobox*Listbox.selectBackground', HIGHLIGHT)
        self.root.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')

        style.configure("Treeview", background=BG_LIST, foreground=FG_TEXT, fieldbackground=BG_LIST, borderwidth=0, rowheight=42, font=("Courier", 11))
        style.map('Treeview', background=[('selected', HIGHLIGHT)], foreground=[('selected', '#ffffff')])
        style.configure("Treeview.Heading", background=BTN_NORMAL, foreground=FG_TEXT, font=("Helvetica", 9, "bold"), borderwidth=1)

        top_frame = tk.Frame(self.root, bg=BG_MAIN)
        top_frame.pack(pady=(10, 2), fill=tk.X, padx=10) 
        
        proj_group = self.create_group_frame(top_frame)
        self.btn_analyze = self.create_btn(proj_group, "New Project", self.project_actions.create_new_project, "#2c4a6b")
        self.btn_analyze.pack(side=tk.LEFT, padx=2, pady=2)
        self.btn_load_set = self.create_btn(proj_group, "Load Set", self.project_actions.load_saved_set, "#5c3a6b")
        self.btn_load_set.pack(side=tk.LEFT, padx=2, pady=2)
        self.btn_add = self.create_btn(proj_group, "+ Add Track(s)", self.project_actions.add_tracks, "#2e7d32")
        self.btn_add.config(state=tk.DISABLED)
        self.btn_add.pack(side=tk.LEFT, padx=2, pady=2)

        self.lbl_folder = tk.Label(top_frame, text="No project loaded", bg=BG_MAIN, fg=FG_TEXT)
        self.lbl_folder.pack(side=tk.LEFT, padx=10)

        self._spinner_lbl = tk.Label(top_frame, text="", bg=BG_MAIN, fg=HIGHLIGHT, font=("Courier", 10, "bold"))
        self._spinner_lbl.pack(side=tk.RIGHT, padx=10)

        self.progress_frame = tk.Frame(self.root, bg=BG_MAIN)
        self.progress_frame.pack(fill=tk.X, padx=10, pady=0)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.progress_frame, variable=self.progress_var, maximum=100)

        mid_frame = tk.Frame(self.root, bg=BG_MAIN)
        mid_frame.pack(expand=True, fill=tk.BOTH, padx=10, pady=(2, 0))

        # --- LEFT PANEL ---
        left_control_frame = tk.Frame(mid_frame, bg=BG_MAIN)
        left_control_frame.pack(side=tk.LEFT, padx=(0, 10), fill=tk.Y)
        
        self.led_norm = tk.Label(left_control_frame, text="○ RAW", fg="#555555", bg=BG_MAIN, font=("Helvetica", 10, "bold"))
        self.led_norm.pack(pady=(5, 0))

        vinyl_wrapper = tk.Frame(left_control_frame, bg=BG_MAIN)
        vinyl_wrapper.pack(pady=(0, 15), fill=tk.X)
        
        self.vinyl_canvas = tk.Canvas(vinyl_wrapper, width=VINYL_SIZE+20, height=VINYL_SIZE+20, bg=BG_MAIN, highlightthickness=0)
        self.vinyl_canvas.pack(side=tk.LEFT)
        self.vinyl_animator = VinylAnimator(self.root, self.vinyl_canvas, self.project)
        
        volume_frame = tk.Frame(vinyl_wrapper, bg=BG_MAIN)
        volume_frame.pack(side=tk.LEFT, padx=(5, 5), fill=tk.Y, pady=10)
        
        tk.Label(volume_frame, text="Vol", bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 9, "bold")).pack()
        self.lbl_volume = tk.Label(volume_frame, text="100%", width=5, bg=BG_MAIN, fg=HIGHLIGHT, font=("Courier", 9, "bold"))
        self.lbl_volume.pack(pady=(0, 2))
        
        fader_wrapper = tk.Frame(volume_frame, bg=BG_MAIN)
        fader_wrapper.pack(expand=True, fill=tk.Y)
        
        self.volume_var = tk.DoubleVar(value=100.0)
        self.slider_volume = ttk.Scale(fader_wrapper, from_=100.0, to=0.0, orient=tk.VERTICAL, 
                                     variable=self.volume_var, style="Vertical.TScale", command=self.on_volume_slide, length=VINYL_SIZE-60)
        self.slider_volume.pack(side=tk.LEFT, padx=(0, 5))
        self.slider_volume.bind("<ButtonRelease-1>", lambda e: setattr(self.project, 'needs_save', True))
        
        self.vu_canvas = tk.Canvas(fader_wrapper, width=8, height=VINYL_SIZE-60, bg=BG_LIST, highlightthickness=0)
        self.vu_canvas.pack(side=tk.LEFT, padx=(0, 5))
        
        self.lbl_lufs = tk.Label(volume_frame, text="-- LUFS", width=10, bg=BG_MAIN, fg="#aaaaaa", font=("Courier", 8))
        self.lbl_lufs.pack(pady=(2, 0))

        # --- EFFECT RACK (SCROLLABLE) ---
        dsp_container = tk.Frame(left_control_frame, bg=BG_MAIN)
        dsp_container.pack(fill=tk.BOTH, expand=True)

        self.left_canvas = tk.Canvas(dsp_container, bg=BG_MAIN, highlightthickness=0, width=280)
        self.left_scrollbar = ttk.Scrollbar(dsp_container, orient="vertical", command=self.left_canvas.yview)
        left_inner = tk.Frame(self.left_canvas, bg=BG_MAIN)
        
        self.left_canvas.create_window((0, 0), window=left_inner, anchor="nw", width=280)
        self.left_canvas.configure(yscrollcommand=self.left_scrollbar.set)
        
        self._scroll_timer = None
        def _do_update_scrollregion():
            self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))
            req_h = left_inner.winfo_reqheight()
            canv_h = self.left_canvas.winfo_height()
            if req_h > canv_h and canv_h > 1:
                if not self.left_scrollbar.winfo_manager():
                    self.left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            else:
                if self.left_scrollbar.winfo_manager():
                    self.left_scrollbar.pack_forget()

        def _update_scrollregion(event=None):
            if self._scroll_timer:
                self.root.after_cancel(self._scroll_timer)
            self._scroll_timer = self.root.after(20, _do_update_scrollregion) # Debounce UI lag

        left_inner.bind("<Configure>", _update_scrollregion)
        self.left_canvas.bind("<Configure>", _update_scrollregion)
        self.left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Enable cross-platform mouse wheel scrolling over the effect rack.
        # Throttle to one repaint per ~16ms (≈60fps) so trackpad bursts don't
        # queue hundreds of expensive child-widget repositions.
        _scroll_acc   = [0.0]
        _scroll_pend  = [False]
        self._last_scroll_t = [0.0]   # shared with animation timer

        def _flush_scroll():
            _scroll_pend[0] = False
            delta = _scroll_acc[0];  _scroll_acc[0] = 0.0
            if delta == 0.0: return
            req_h = left_inner.winfo_reqheight()
            canv_h = self.left_canvas.winfo_height()
            if req_h <= canv_h: return
            self._last_scroll_t[0] = time.monotonic()
            top, _ = self.left_canvas.yview()
            self.left_canvas.yview_moveto(
                max(0.0, min(1.0, top - delta / (req_h * 2.5))))

        def _on_mousewheel(event):
            # Only act when the cursor is actually over the DSP rack canvas.
            # Position-check avoids interfering with the setlist Treeview.
            try:
                mx, my = self.root.winfo_pointerx(), self.root.winfo_pointery()
                lx, ly = self.left_canvas.winfo_rootx(), self.left_canvas.winfo_rooty()
                if not (lx <= mx <= lx + self.left_canvas.winfo_width() and
                        ly <= my <= ly + self.left_canvas.winfo_height()):
                    return
            except tk.TclError:
                return
            req_h = left_inner.winfo_reqheight()
            if req_h <= self.left_canvas.winfo_height(): return
            if hasattr(event, 'num') and event.num in (4, 5):
                _scroll_acc[0] += -120 if event.num == 4 else 120
            else:
                _scroll_acc[0] += event.delta
            if not _scroll_pend[0]:
                _scroll_pend[0] = True
                self.left_canvas.after(16, _flush_scroll)

        # Recursively bind to every widget inside the rack so instance bindings
        # fire before any class-level binding that might swallow the event.
        def _bind_rack_scroll(w):
            try:
                w.bind("<MouseWheel>", _on_mousewheel, add="+")
                w.bind("<Button-4>",   _on_mousewheel, add="+")
                w.bind("<Button-5>",   _on_mousewheel, add="+")
            except tk.TclError:
                pass
            for child in w.winfo_children():
                _bind_rack_scroll(child)

        self._bind_rack_scroll = _bind_rack_scroll  # expose so VST add can call it
        self.root.after(200, lambda: _bind_rack_scroll(dsp_container))

        self.dsp_vars = {}
        self.rack_modules = {}
        self.dsp_vars['chain_order'] = ['eq', 'dyn']
        self.dsp_vars['vsts'] = {}
        rack_outer_frame = tk.Frame(left_inner, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        rack_outer_frame.pack(pady=(15, 5), fill=tk.X, padx=5)

        rack_header = tk.Frame(rack_outer_frame, bg=BG_LIST)
        rack_header.pack(fill=tk.X)
        tk.Label(rack_header, text="EFFECT RACK", bg=BG_LIST, fg=HIGHLIGHT, font=("Helvetica", 10, "bold")).pack(side=tk.LEFT, padx=5, pady=5)
        
        self.dsp_vars['master_bypass'] = tk.BooleanVar(value=True)
        
        master_btn_canvas = tk.Canvas(rack_header, width=16, height=16, bg=BG_LIST, highlightthickness=0, cursor="hand2")
        master_btn_canvas.pack(side=tk.RIGHT, padx=5, pady=5)
        tk.Label(rack_header, text="Power", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 8)).pack(side=tk.RIGHT)
        
        def toggle_master(*args):
            new_bypass = not self.dsp_vars['master_bypass'].get()
            self._master_user_toggled = True
            try:
                self.dsp_vars['master_bypass'].set(new_bypass)
                self.on_dsp_change()
            finally:
                self._master_user_toggled = False
            self._on_master_bypass_toggle(new_bypass)

        def draw_master(*args):
            master_btn_canvas.delete("all")
            is_active = not self.dsp_vars['master_bypass'].get()
            if is_active:
                master_btn_canvas.create_oval(2, 2, 14, 14, fill="#ffd700", outline="#ffd700")
            else:
                master_btn_canvas.create_oval(2, 2, 14, 14, fill=BG_LIST, outline="#888888", width=1.5)

        master_btn_canvas.bind("<Button-1>", toggle_master)
        master_btn_canvas.bind("<Map>", draw_master)
        self.dsp_vars['master_bypass'].trace_add("write", draw_master)
        draw_master()

        # DENOISE MODULE (top of rack, same width as other modules)
        self._build_denoise_rack(rack_outer_frame)

        # FIXED LIMITER MODULE (Always Top)
        limiter_frame = tk.Frame(rack_outer_frame, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        limiter_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        
        lim_header = tk.Frame(limiter_frame, bg=BG_LIST)
        lim_header.pack(fill=tk.X)
        
        self.dsp_vars['limiter_bypass'] = tk.BooleanVar(value=True)
        self.dsp_vars['limiter_softclip'] = tk.BooleanVar(value=False)
        self.dsp_vars['limiter_input'] = tk.DoubleVar(value=0.0)
        self.dsp_vars['limiter_output'] = tk.DoubleVar(value=0.0)
        
        tk.Label(lim_header, text="LIMITER", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 9, "bold")).pack(side=tk.LEFT, padx=5, pady=2)
        
        lim_btn_canvas = tk.Canvas(lim_header, width=16, height=16, bg=BG_LIST, highlightthickness=0, cursor="hand2")
        lim_btn_canvas.pack(side=tk.RIGHT, padx=5, pady=2)
        tk.Label(lim_header, text="Bypass", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 8)).pack(side=tk.RIGHT)
        
        sc_btn_canvas = tk.Canvas(lim_header, width=16, height=16, bg=BG_LIST, highlightthickness=0, cursor="hand2")
        sc_btn_canvas.pack(side=tk.RIGHT, padx=5, pady=2)
        lbl_sc = tk.Label(lim_header, text="Soft Clip", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 8))
        lbl_sc.pack(side=tk.RIGHT)
        
        lim_body = tk.Frame(limiter_frame, bg=BG_MAIN)
        lim_body.pack(fill=tk.X, pady=2)
        
        lim_ui_elements = []
        for name, var, from_, to_ in [("Input", self.dsp_vars['limiter_input'], -24.0, 24.0), ("Output", self.dsp_vars['limiter_output'], -24.0, 24.0)]:
            col = tk.Frame(lim_body, bg=BG_MAIN)
            col.pack(side=tk.LEFT, expand=True)
            name_lbl = tk.Label(col, text=name, bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 7))
            name_lbl.pack()
            
            knob = Knob(col, var, from_, to_, command=self.on_dsp_change, size=26)
            knob.pack(pady=0)
            val_lbl = tk.Label(col, text="0.0", bg=BG_MAIN, fg="#aaaaaa", font=("Helvetica", 7))
            val_lbl.pack()
            
            def update_lim_lbl(v_lbl=val_lbl, v_var=var, *args):
                v_lbl.config(text=f"{v_var.get():.1f}")
                
            var.trace_add('write', lambda *args, l=val_lbl, v=var: update_lim_lbl(l, v, *args))
            val_lbl.config(text=f"{var.get():.1f}")
            lim_ui_elements.append((name_lbl, knob, val_lbl))

        def update_lim_visuals(*args):
            master_bypassed = self.dsp_vars['master_bypass'].get()
            is_bypassed = self.dsp_vars['limiter_bypass'].get() or master_bypassed
            color = "#555555" if is_bypassed else FG_TEXT
            lbl_sc.config(fg=color)
            for name_lbl, knob, val_lbl in lim_ui_elements:
                knob.set_disabled(is_bypassed)
                name_lbl.config(fg=color)
                val_lbl.config(fg=color)
        
        def toggle_lim_bypass(event):
            self._user_toggling_bypass = True
            try:
                self.dsp_vars['limiter_bypass'].set(not self.dsp_vars['limiter_bypass'].get())
                self.on_dsp_change()
            finally:
                self._user_toggling_bypass = False
            
        def draw_lim_btn(*args):
            lim_btn_canvas.delete("all")
            master_bypassed = self.dsp_vars['master_bypass'].get()
            is_active = not self.dsp_vars['limiter_bypass'].get() and not master_bypassed
            if is_active: lim_btn_canvas.create_oval(2, 2, 14, 14, fill="#ffd700", outline="#ffd700")
            else: lim_btn_canvas.create_oval(2, 2, 14, 14, fill=BG_LIST, outline="#888888", width=1.5)
                
        def toggle_sc_bypass(event):
            self.dsp_vars['limiter_softclip'].set(not self.dsp_vars['limiter_softclip'].get())
            self.on_dsp_change()

        def draw_sc_btn(*args):
            sc_btn_canvas.delete("all")
            master_bypassed = self.dsp_vars['master_bypass'].get()
            lim_bypassed = self.dsp_vars['limiter_bypass'].get()
            is_active = self.dsp_vars['limiter_softclip'].get() and not master_bypassed and not lim_bypassed
            if is_active: sc_btn_canvas.create_oval(2, 2, 14, 14, fill="#ffd700", outline="#ffd700")
            else: sc_btn_canvas.create_oval(2, 2, 14, 14, fill=BG_LIST, outline="#888888", width=1.5)

        lim_btn_canvas.bind("<Button-1>", toggle_lim_bypass)
        lim_btn_canvas.bind("<Map>", draw_lim_btn)
        sc_btn_canvas.bind("<Button-1>", toggle_sc_bypass)
        sc_btn_canvas.bind("<Map>", draw_sc_btn)
        
        self.dsp_vars['limiter_bypass'].trace_add("write", update_lim_visuals)
        self.dsp_vars['master_bypass'].trace_add("write", update_lim_visuals)
        
        self.dsp_vars['limiter_bypass'].trace_add("write", draw_lim_btn)
        self.dsp_vars['master_bypass'].trace_add("write", draw_lim_btn)
        
        self.dsp_vars['limiter_softclip'].trace_add("write", draw_sc_btn)
        self.dsp_vars['limiter_bypass'].trace_add("write", draw_sc_btn)
        self.dsp_vars['master_bypass'].trace_add("write", draw_sc_btn)
        
        update_lim_visuals()
        draw_lim_btn()
        draw_sc_btn()

        self.rack_body = tk.Frame(rack_outer_frame, bg=BG_MAIN)
        self.rack_body.pack(fill=tk.X, pady=5)

        # 1. EQ Module (Graph)
        self.dsp_vars['eq_bypass'] = tk.BooleanVar(value=True)
        self.dsp_vars['eq_low'] = tk.DoubleVar(value=0.0)
        self.dsp_vars['eq_mid'] = tk.DoubleVar(value=0.0)
        self.dsp_vars['eq_high'] = tk.DoubleVar(value=0.0)
        self.dsp_vars['eq_low_freq'] = tk.DoubleVar(value=250.0)
        self.dsp_vars['eq_mid_freq'] = tk.DoubleVar(value=1000.0)
        self.dsp_vars['eq_high_freq'] = tk.DoubleVar(value=4000.0)
        self.dsp_vars['eq_low_q'] = tk.DoubleVar(value=0.707)
        self.dsp_vars['eq_mid_q'] = tk.DoubleVar(value=1.0)
        self.dsp_vars['eq_high_q'] = tk.DoubleVar(value=0.707)
        self.rack_modules['eq'] = self.create_graph_eq_module(self.rack_body)

        # 2. Dynamics Module
        self.dsp_vars['dyn_bypass'] = tk.BooleanVar(value=True)
        self.dsp_vars['dyn_threshold'] = tk.DoubleVar(value=0.0)
        self.dsp_vars['dyn_ratio'] = tk.DoubleVar(value=1.0)
        self.dsp_vars['dyn_attack'] = tk.DoubleVar(value=5.0)
        self.dsp_vars['dyn_release'] = tk.DoubleVar(value=100.0)
        self.dsp_vars['dyn_makeup'] = tk.DoubleVar(value=0.0)
        self.rack_modules['dyn'] = self.create_plugin_module(self.rack_body, "COMPRESSOR", 'dyn', self.dsp_vars['dyn_bypass'], [
            ("Thresh", self.dsp_vars['dyn_threshold'], -60.0, 0.0),
            ("Ratio", self.dsp_vars['dyn_ratio'], 1.0, 20.0),
            ("Attack", self.dsp_vars['dyn_attack'], 1.0, 500.0),
            ("Release", self.dsp_vars['dyn_release'], 10.0, 2000.0),
            ("MakeUp", self.dsp_vars['dyn_makeup'], 0.0, 24.0)
        ])

        rack_footer = tk.Frame(rack_outer_frame, bg=BG_MAIN)
        rack_footer.pack(fill=tk.X, pady=(0, 5), padx=5)
        
        add_btn = tk.Label(rack_footer, text="＋", bg=BTN_NORMAL, fg=FG_TEXT, font=("Helvetica", 18, "bold"), cursor="hand2", pady=2)
        add_btn.pack(fill=tk.X)
        add_btn.bind("<Enter>", lambda e: add_btn.config(bg=BTN_HOVER))
        add_btn.bind("<Leave>", lambda e: add_btn.config(bg=BTN_NORMAL))
        add_btn.bind("<Button-1>", lambda e: self.add_vst_plugin())

        self.render_rack_order()
        self.on_dsp_change()
        
        # --- CENTER PANEL ---
        list_frame = tk.Frame(mid_frame, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        list_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        self.list_frame = list_frame
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        columns = ("num", "bpm", "tone", "song", "artist", "album", "size", "norm")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="tree headings", yscrollcommand=scrollbar.set, selectmode="browse", style="Treeview")
        
        self.tree.heading("#0", text="Art")
        self.tree.column("#0", width=60, anchor=tk.CENTER, stretch=tk.NO)
        
        self.tree.heading("num", text="#")
        self.tree.column("num", width=30, anchor=tk.CENTER, stretch=tk.NO)

        self.tree.heading("bpm", text="BPM")
        self.tree.column("bpm", width=50, anchor=tk.CENTER, stretch=tk.NO)
        self.tree.heading("tone", text="Key")
        self.tree.column("tone", width=50, anchor=tk.CENTER, stretch=tk.NO)
        self.tree.heading("song", text="Song")
        self.tree.column("song", width=160, anchor=tk.W, stretch=tk.YES)
        self.tree.heading("artist", text="Artist")
        self.tree.column("artist", width=120, anchor=tk.W, stretch=tk.YES)
        self.tree.heading("album", text="Album")
        self.tree.column("album", width=120, anchor=tk.W, stretch=tk.YES)
        self.tree.heading("size", text="Size")
        self.tree.column("size", width=50, anchor=tk.E, stretch=tk.NO)
        self.tree.heading("norm", text="Norm")
        self.tree.column("norm", width=40, anchor=tk.CENTER, stretch=tk.NO)

        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        scrollbar.config(command=self.tree.yview)

        self.tree.tag_configure("inactive", foreground="#454545", font=("Courier", 11, "italic"))

        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)

        def prevent_art_resize(event):
            if self.tree.identify_region(event.x, event.y) == "separator" and self.tree.identify_column(event.x) == "#0":
                return "break"
        
        self.tree.bind('<Button-1>', prevent_art_resize, add='+')
        self.tree.bind('<B1-Motion>', prevent_art_resize, add='+')
        self.tree.bind('<Double-1>', self.on_double_click)

        self.tree.bind('<ButtonPress-1>', self._on_drag_press, add='+')
        self.tree.bind('<B1-Motion>', self._on_drag_motion, add='+')
        self.tree.bind('<ButtonRelease-1>', self._on_drag_release, add='+')
        self.root.bind('<Escape>', self._cancel_drag, add='+')


        # --- RIGHT PANEL ---
        right_control_frame = tk.Frame(mid_frame, bg=BG_MAIN)
        right_control_frame.pack(side=tk.LEFT, padx=(10, 0), fill=tk.Y)
        right_inner = tk.Frame(right_control_frame, bg=BG_MAIN)
        right_inner.pack(expand=True)

        search_btn = tk.Label(right_inner, text="⌕", bg=BTN_NORMAL, fg=FG_TEXT,
                              font=("Helvetica", 18), cursor="hand2",
                              padx=10, pady=4,
                              highlightbackground=BORDER, highlightthickness=1)
        search_btn.pack(pady=(4, 8), padx=5, fill=tk.X)
        search_btn.bind("<Enter>",    lambda e: search_btn.config(bg=BTN_HOVER))
        search_btn.bind("<Leave>",    lambda e: search_btn.config(bg=BTN_NORMAL))
        search_btn.bind("<Button-1>", lambda e: self.open_search_popup())

        math_group = tk.Frame(right_inner, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        math_group.pack(pady=(0, 10), padx=5, fill=tk.X)
        tk.Label(math_group, text="BPM", bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 9, "bold")).pack(pady=(5,2))
        self.bpm_var = tk.StringVar()
        self.bpm_entry = tk.Entry(math_group, textvariable=self.bpm_var, width=6,
                                   bg=BG_LIST, fg=HIGHLIGHT, insertbackground=HIGHLIGHT,
                                   font=("Courier", 12, "bold"), bd=0,
                                   highlightthickness=1, highlightbackground=BORDER,
                                   highlightcolor=HIGHLIGHT, justify='center')
        self.bpm_entry.pack(pady=(0, 4), ipady=3)
        self.bpm_entry.bind("<Return>", self._on_bpm_edit)
        self.bpm_entry.bind("<FocusOut>", self._on_bpm_edit)
        self.create_btn(math_group, "x2", lambda: self.modify_bpm(2.0), BTN_NORMAL, 4).pack(pady=2)
        self.create_btn(math_group, "÷2", lambda: self.modify_bpm(0.5), BTN_NORMAL, 4).pack(pady=2)
        self.create_btn(math_group, "↻ Sort", self.resort_project, "#a86a11", 6).pack(pady=(10, 5), padx=5)

        history_group = tk.Frame(right_inner, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        history_group.pack(pady=(0, 10), padx=5, fill=tk.X)
        tk.Label(history_group, text="History", bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 9, "bold")).pack(pady=(5, 2))
        self.btn_undo = self.create_btn(history_group, "↩ Undo", self.undo, BTN_NORMAL, 6)
        self.btn_undo.pack(pady=2)
        self.btn_undo.config(state=tk.DISABLED)
        self.btn_redo = self.create_btn(history_group, "↪ Redo", self.redo, BTN_NORMAL, 6)
        self.btn_redo.pack(pady=(2, 5))
        self.btn_redo.config(state=tk.DISABLED)

        move_group = tk.Frame(right_inner, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        move_group.pack(pady=5, padx=5, fill=tk.X)
        tk.Label(move_group, text="Move", bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 9, "bold")).pack(pady=(5,2))
        self.create_btn(move_group, "▲▲", lambda: self.shift_track(-5), "#444444", 4).pack(pady=2)
        self.create_btn(move_group, "▲", lambda: self.shift_track(-1), BTN_NORMAL, 4).pack(pady=2)
        self.create_btn(move_group, "▼", lambda: self.shift_track(1), BTN_NORMAL, 4).pack(pady=2)
        self.create_btn(move_group, "▼▼", lambda: self.shift_track(5), "#444444", 4).pack(pady=(2, 5))

        out_group = tk.Frame(right_inner, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        out_group.pack(pady=(10, 5), padx=5, fill=tk.X)
        tk.Label(out_group, text="Set", bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 9, "bold")).pack(pady=(5, 2))
        self.btn_sort_out = self.create_btn(out_group, "✕ Remove", self.toggle_inactive, BTN_NORMAL, 8)
        self.btn_sort_out.pack(pady=(2, 5))

        # --- BOTTOM TIMELINE & AUDIO ---
        timeline_frame = tk.Frame(self.root, bg=BG_MAIN)
        timeline_frame.pack(fill=tk.X, padx=20, pady=(15, 10)) 
        
        self.lbl_current_time = tk.Label(timeline_frame, text="00:00", bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 10))
        self.lbl_current_time.pack(side=tk.LEFT)
        
        self.lbl_total_time = tk.Label(timeline_frame, text="00:00", bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 10))
        self.lbl_total_time.pack(side=tk.RIGHT)
        
        self.timeline_var = tk.DoubleVar()
        self.timeline = Timeline(timeline_frame, variable=self.timeline_var, max_val=100.0, command=self.on_timeline_interaction)
        self.timeline.pack(fill=tk.X, expand=True, padx=10)

        audio_frame = tk.Frame(self.root, bg=BG_MAIN)
        audio_frame.pack(fill=tk.X, padx=20, pady=(0, 15))
        info_frame = tk.Frame(audio_frame, bg=BG_MAIN)
        info_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        total_box = tk.Frame(info_frame, bg=BG_LIST, highlightbackground=BORDER, highlightthickness=1)
        total_box.pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(total_box, text="TOTAL SET", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 7, "bold")).pack(padx=8, pady=(3, 0))
        self.lbl_set_total = tk.Label(total_box, text="--:--", bg=BG_LIST, fg=HIGHLIGHT, font=("Courier", 11, "bold"))
        self.lbl_set_total.pack(padx=8, pady=(0, 3))

        upto_box = tk.Frame(info_frame, bg=BG_LIST, highlightbackground=BORDER, highlightthickness=1)
        upto_box.pack(side=tk.LEFT)
        tk.Label(upto_box, text="UP TO HERE", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 7, "bold")).pack(padx=8, pady=(3, 0))
        self.lbl_set_upto = tk.Label(upto_box, text="--:--", bg=BG_LIST, fg=HIGHLIGHT, font=("Courier", 11, "bold"))
        self.lbl_set_upto.pack(padx=8, pady=(0, 3))
        
        transport_group = self.create_group_frame(audio_frame, padx=0, pady=0)
        transport_group.pack(side=tk.LEFT)
        self.btn_prev = self.create_btn(transport_group, "⏮", self.play_prev, BTN_NORMAL, 4, bold=True)
        self.btn_prev.pack(side=tk.LEFT, padx=2, pady=2)
        self.btn_play_pause = self.create_btn(transport_group, "▶ Play", self.toggle_play_pause, BTN_NORMAL, 10, bold=True)
        self.btn_play_pause.pack(side=tk.LEFT, padx=2, pady=2)
        self.btn_next = self.create_btn(transport_group, "⏭", self.play_next, BTN_NORMAL, 4, bold=True)
        self.btn_next.pack(side=tk.LEFT, padx=2, pady=2)
        
        right_panel = tk.Frame(audio_frame, bg=BG_MAIN)
        right_panel.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        self.btn_render = self.create_btn(right_panel, "🎧 Export / Render Audio", lambda: ExportDialog(self).open_export_window(), bg_color="#a83232", bold=True)
        self.btn_render.pack(side=tk.RIGHT)

    def add_vst_plugin(self):
        if not self.project.tracks:
            messagebox.showinfo("Wait", "Load a project and select a track before adding plugins.")
            return

        win = tk.Toplevel(self.root)
        win.title("Plugin Browser")
        win.geometry("400x500")
        win.configure(bg=BG_MAIN)
        win.grab_set()
        
        search_frame = tk.Frame(win, bg=BG_MAIN)
        search_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=search_var, bg=BG_LIST, fg=FG_TEXT, insertbackground=FG_TEXT, font=("Helvetica", 11), bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=HIGHLIGHT)
        search_entry.pack(fill=tk.X, ipady=4)
        search_entry.focus_set()

        list_frame = tk.Frame(win, bg=BG_MAIN)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        plugin_listbox = tk.Listbox(list_frame, bg=BG_LIST, fg=FG_TEXT, selectbackground=HIGHLIGHT, yscrollcommand=scrollbar.set, font=("Helvetica", 10), bd=0, highlightthickness=1, highlightbackground=BORDER)
        plugin_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=plugin_listbox.yview)

        status_lbl = tk.Label(win, text="Scanning system for plugins...", bg=BG_MAIN, fg="#aaaaaa", font=("Helvetica", 9))
        status_lbl.pack(pady=5)

        all_plugins = []
        
        def update_list(*args):
            q = search_var.get().lower()
            plugin_listbox.delete(0, tk.END)
            for p in all_plugins:
                if q in p['name'].lower() or q in p['ext'].lower():
                    plugin_listbox.insert(tk.END, f"{p['name']} ({p['ext']})")

        search_var.trace_add("write", update_list)

        def scan_thread():
            paths = []
            if sys.platform == "darwin":
                paths = ["/Library/Audio/Plug-Ins/VST", "/Library/Audio/Plug-Ins/VST3", "/Library/Audio/Plug-Ins/Components",
                         os.path.expanduser("~/Library/Audio/Plug-Ins/VST"), os.path.expanduser("~/Library/Audio/Plug-Ins/VST3"), os.path.expanduser("~/Library/Audio/Plug-Ins/Components")]
            elif sys.platform == "win32":
                paths = [r"C:\Program Files\Common Files\VST3", r"C:\Program Files\Common Files\VST2", r"C:\Program Files\VSTPlugins", r"C:\Program Files\Steinberg\VSTPlugins"]
            else:
                paths = [os.path.expanduser("~/.vst"), os.path.expanduser("~/.vst3"), "/usr/lib/vst", "/usr/lib/vst3", "/usr/local/lib/vst", "/usr/local/lib/vst3"]

            valid_exts = {".vst", ".vst3", ".component", ".dll", ".so"}
            found = []
            seen = set()
            for d in paths:
                if os.path.exists(d):
                    for root, dirs, files in os.walk(d):
                        # Prevent traversing inside plugin bundles (macOS) by removing matched dirs
                        for d_name in list(dirs):
                            ext = os.path.splitext(d_name)[1].lower()
                            if ext in valid_exts:
                                path = os.path.join(root, d_name)
                                if path not in seen:
                                    seen.add(path)
                                    found.append({"name": os.path.splitext(d_name)[0], "path": path, "ext": ext})
                                dirs.remove(d_name) 
                        for f_name in files:
                            ext = os.path.splitext(f_name)[1].lower()
                            if ext in valid_exts:
                                path = os.path.join(root, f_name)
                                if path not in seen:
                                    seen.add(path)
                                    found.append({"name": os.path.splitext(f_name)[0], "path": path, "ext": ext})
            found.sort(key=lambda x: x["name"].lower())
            
            def on_done():
                nonlocal all_plugins
                all_plugins = found
                status_lbl.config(text=f"Found {len(all_plugins)} plugins.")
                self.root.after(2000, status_lbl.pack_forget)
                update_list()
            
            self.root.after(0, on_done)

        threading.Thread(target=scan_thread, daemon=True).start()

        def on_select(e=None):
            sel = plugin_listbox.curselection()
            if not sel: return
            sel_text = plugin_listbox.get(sel[0])
            match = next((p for p in all_plugins if f"{p['name']} ({p['ext']})" == sel_text), None)
            if match:
                win.destroy()
                self._add_vst_to_rack(match['path'], match['name'])

        plugin_listbox.bind("<Double-Button-1>", on_select)
        plugin_listbox.bind("<Return>", on_select)

        btn_add = self.create_btn(win, "Add Selected", on_select, "#2e7d32", bold=True)
        btn_add.pack(pady=5)
        
        lbl_browse = tk.Label(win, text="Browse manually...", bg=BG_MAIN, fg=HIGHLIGHT, cursor="hand2", font=("Helvetica", 9, "underline"))
        lbl_browse.pack(pady=(0, 10))
        
        def browse_manual(e):
            path = filedialog.askopenfilename(parent=win, title="Select VST Plugin", filetypes=[("Audio Plugins", "*.vst3 *.vst *.component *.dll *.so"), ("All Files", "*.*")])
            if path:
                win.destroy()
                self._add_vst_to_rack(path, os.path.splitext(os.path.basename(path))[0])
                
        lbl_browse.bind("<Button-1>", browse_manual)

    def _add_vst_to_rack(self, path, name):
        mod_id = f"vst_{int(time.time())}_{random.randint(0, 1000)}"
        
        self.dsp_vars['vsts'][mod_id] = {
            'path': path, 'name': name, 'bypass': tk.BooleanVar(value=False)
        }
        self.dsp_vars['chain_order'].append(mod_id)
        
        self.rack_modules[mod_id] = self.create_vst_module(self.rack_body, name, mod_id, self.dsp_vars['vsts'][mod_id]['bypass'])
        self.render_rack_order()
        self.on_dsp_change()
        if hasattr(self, '_bind_rack_scroll'):
            self.root.after(50, lambda: self._bind_rack_scroll(self.rack_body))

    def move_module(self, module_id, direction):
        order = self.dsp_vars.get('chain_order', ['eq', 'dyn'])
        if module_id not in order: return
        idx = order.index(module_id)
        new_idx = idx + direction
        if 0 <= new_idx < len(order):
            order[idx], order[new_idx] = order[new_idx], order[idx]
            self.dsp_vars['chain_order'] = order
            self.render_rack_order()
            self.on_dsp_change()

    def render_rack_order(self):
        # Unpack all modules cleanly first
        for module_frame in self.rack_modules.values():
            module_frame.pack_forget()
            
        # Reversed because signal moves bottom (first packed/processed) to top (last packed)
        for mod_id in reversed(self.dsp_vars.get('chain_order', ['eq', 'dyn'])):
            if mod_id in self.rack_modules:
                self.rack_modules[mod_id].pack(pady=5, padx=5, fill=tk.X)
                
        # Force Tkinter to immediately flush the geometry and redraw queues!
        # This completely eliminates text/arrow ghosting during re-orders.
        self.root.update_idletasks()

    def create_vst_module(self, parent, title, module_id, bypass_var):
        f = tk.Frame(parent, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        header = tk.Frame(f, bg=BG_LIST)
        header.pack(fill=tk.X)
        
        order_frame = tk.Frame(header, bg=BG_LIST)
        order_frame.pack(side=tk.LEFT, padx=(5, 0))
        btn_up = tk.Label(order_frame, text="▲", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 7), cursor="hand2")
        btn_up.pack(side=tk.TOP)
        btn_down = tk.Label(order_frame, text="▼", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 7), cursor="hand2")
        btn_down.pack(side=tk.TOP)
        btn_up.bind("<Button-1>", lambda e: self.move_module(module_id, 1))
        btn_down.bind("<Button-1>", lambda e: self.move_module(module_id, -1))

        display_title = title if len(title) < 18 else title[:15] + "..."
        title_lbl = tk.Label(header, text=display_title, bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 9, "bold"))
        title_lbl.pack(side=tk.LEFT, padx=5, pady=6)
        
        del_btn = tk.Label(header, text="✕", bg=BG_LIST, fg="#cc4444", font=("Helvetica", 10, "bold"), cursor="hand2")
        del_btn.pack(side=tk.RIGHT, padx=5)

        ui_btn = tk.Label(header, text="⚙", bg=BG_LIST, fg=HIGHLIGHT, font=("Helvetica", 12, "bold"), cursor="hand2")
        ui_btn.pack(side=tk.RIGHT, padx=5)

        def open_ui(e):
            if module_id in self.audio.vst_instances:
                try:
                    # VST UIs must be called from the main GUI thread
                    self.audio.vst_instances[module_id].show_editor()
                except Exception as ex:
                    messagebox.showinfo("Not Supported", f"This VST does not support a custom UI, or there was an error:\n{ex}")

        ui_btn.bind("<Button-1>", open_ui)

        btn_canvas = tk.Canvas(header, width=16, height=16, bg=BG_LIST, highlightthickness=0, cursor="hand2")
        btn_canvas.pack(side=tk.RIGHT, padx=8, pady=6)
        
        def delete_vst(e):
            if module_id in self.dsp_vars.get('chain_order', []):
                self.dsp_vars['chain_order'].remove(module_id)
            if module_id in self.dsp_vars.get('vsts', {}):
                del self.dsp_vars['vsts'][module_id]
            if module_id in self.rack_modules:
                self.rack_modules[module_id].destroy()
                del self.rack_modules[module_id]
            self.render_rack_order()
            self.on_dsp_change()
            
        del_btn.bind("<Button-1>", delete_vst)
        
        def toggle_bypass(e):
            self._user_toggling_bypass = True
            try:
                bypass_var.set(not bypass_var.get())
                self.on_dsp_change()
            finally:
                self._user_toggling_bypass = False

        draw_btn_cb = None
        def draw_btn(*args):
            try:
                if not btn_canvas.winfo_exists():
                    if draw_btn_cb is not None:
                        self.dsp_vars['master_bypass'].trace_remove("write", draw_btn_cb)
                    return
                btn_canvas.delete("all")
                is_active = not bypass_var.get() and not self.dsp_vars['master_bypass'].get()
                if is_active: btn_canvas.create_oval(2, 2, 14, 14, fill="#ffd700", outline="#ffd700")
                else: btn_canvas.create_oval(2, 2, 14, 14, fill=BG_LIST, outline="#888888", width=1.5)
                color = "#555555" if not is_active else FG_TEXT
                title_lbl.config(fg=color)
            except tk.TclError:
                pass

        btn_canvas.bind("<Button-1>", toggle_bypass)
        btn_canvas.bind("<Map>", draw_btn)
        bypass_var.trace_add("write", draw_btn)
        draw_btn_cb = self.dsp_vars['master_bypass'].trace_add("write", draw_btn)
        draw_btn()
        return f

    def create_graph_eq_module(self, parent):
        SR = 44100.0
        GAIN_MAX = 15.0
        HR = 5
        FLIM = {'low': (20, 2000), 'mid': (100, 8000), 'high': (1000, 20000)}

        f = tk.Frame(parent, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)

        # ── Header (order ▲▼ | EQ | [LOW ⊙freq ⊙Q] | Power ●) ─────────────
        header = tk.Frame(f, bg=BG_LIST)
        header.pack(fill=tk.X)
        order_frame = tk.Frame(header, bg=BG_LIST)
        order_frame.pack(side=tk.LEFT, padx=(5, 0))
        for arrow, d in [("▲", 1), ("▼", -1)]:
            lbl = tk.Label(order_frame, text=arrow, bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 7), cursor="hand2")
            lbl.pack(side=tk.TOP)
            lbl.bind("<Button-1>", lambda e, _d=d: self.move_module('eq', _d))
        title_lbl = tk.Label(header, text="EQ", bg=BG_LIST, fg="#555555", font=("Helvetica", 9, "bold"))
        title_lbl.pack(side=tk.LEFT, padx=(5, 0))
        bypass_c = tk.Canvas(header, width=16, height=16, bg=BG_LIST, highlightthickness=0, cursor="hand2")
        bypass_c.pack(side=tk.RIGHT, padx=(0, 8), pady=4)
        tk.Label(header, text="Power", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 8)).pack(side=tk.RIGHT)

        # Inline band info — lives in the header, hidden until a handle is grabbed
        _freq_proxy = tk.DoubleVar(value=250.0)
        _q_proxy    = tk.DoubleVar(value=0.707)
        _active     = {'band': None}

        info_inline = tk.Frame(header, bg=BG_LIST)

        freq_knob = Knob(info_inline, _freq_proxy, 20, 2000, size=26, pill=True)
        freq_knob.pack(side=tk.LEFT, padx=(6, 2))
        freq_lbl = tk.Label(info_inline, text="250Hz", bg=BG_LIST, fg="#888888",
                             font=("Courier", 7), width=5, anchor='w')
        freq_lbl.pack(side=tk.LEFT, padx=(0, 4))

        q_knob = Knob(info_inline, _q_proxy, 0.1, 4.0, size=26, pill=True)
        q_knob.pack(side=tk.LEFT, padx=(0, 2))
        q_lbl = tk.Label(info_inline, text="Q0.71", bg=BG_LIST, fg="#888888",
                          font=("Courier", 7), width=5, anchor='w')
        q_lbl.pack(side=tk.LEFT)

        def _fmt_freq(f):
            return f"{f:.0f}Hz" if f < 1000 else f"{f/1000:.1f}k"

        def _on_proxy_change(*_):
            if not _active['band']: return
            band = _active['band']
            nf = round(_freq_proxy.get(), 1)
            nq = round(_q_proxy.get(), 3)
            self.dsp_vars[f'eq_{band}_freq'].set(nf)
            self.dsp_vars[f'eq_{band}_q'].set(nq)
            freq_lbl.config(text=_fmt_freq(nf))
            q_lbl.config(text=f"Q{nq:.2f}")
            self.on_dsp_change()
            draw()

        _freq_proxy.trace_add('write', _on_proxy_change)
        _q_proxy.trace_add('write', _on_proxy_change)

        def _set_active(band):
            _active['band'] = band
            if band is None:
                info_inline.pack_forget()
                return
            fmin, fmax = FLIM[band]
            freq_knob.from_ = fmin
            freq_knob.to_   = fmax
            fv = self.dsp_vars[f'eq_{band}_freq'].get()
            qv = self.dsp_vars[f'eq_{band}_q'].get()
            _freq_proxy.set(fv)
            _q_proxy.set(qv)
            freq_lbl.config(text=_fmt_freq(fv))
            q_lbl.config(text=f"Q{qv:.2f}")
            freq_knob.draw()
            if not info_inline.winfo_manager():
                info_inline.pack(side=tk.LEFT, padx=(4, 0))

        # ── EQ Graph canvas ──────────────────────────────────────────────────
        cv = tk.Canvas(f, bg=BG_MAIN, height=58, highlightthickness=0, cursor="crosshair")
        cv.pack(fill=tk.X, padx=4, pady=(3, 4))

        drag = {'band': None, 'y0': 0, 'g0': 0.0, 'gvar': None, 'fvar': None, 'fmin': 20, 'fmax': 20000}

        def freq_to_x(freq, w):
            return (np.log10(max(freq, 1)) - np.log10(20.0)) / (np.log10(20000.0) - np.log10(20.0)) * w

        def x_to_freq(x, w, fmin, fmax):
            lo, hi = np.log10(20.0), np.log10(20000.0)
            return float(np.clip(10 ** (lo + x / w * (hi - lo)), fmin, fmax))

        def gain_to_y(g, h):
            return h / 2.0 - g / GAIN_MAX * (h / 2.0 - 6)

        def biquad_db(freqs, b0, b1, b2, a0, a1, a2):
            w = 2 * np.pi * freqs / SR
            e = np.exp(1j * w)
            H = (b0 + b1/e + b2/e**2) / (a0 + a1/e + a2/e**2)
            return 20 * np.log10(np.maximum(np.abs(H), 1e-10))

        def low_shelf(freqs, g, fc, q):
            if abs(g) < 0.01: return np.zeros(len(freqs))
            A = 10**(g/40); w0 = 2*np.pi*fc/SR
            cw, sqA = np.cos(w0), np.sqrt(A)
            al = np.sin(w0) / (2 * max(q, 0.01))
            return biquad_db(freqs,
                A*((A+1)-(A-1)*cw+2*sqA*al), 2*A*((A-1)-(A+1)*cw), A*((A+1)-(A-1)*cw-2*sqA*al),
                (A+1)+(A-1)*cw+2*sqA*al, -2*((A-1)+(A+1)*cw), (A+1)+(A-1)*cw-2*sqA*al)

        def peak(freqs, g, fc, q):
            if abs(g) < 0.01: return np.zeros(len(freqs))
            A = 10**(g/40); w0 = 2*np.pi*fc/SR
            cw, al = np.cos(w0), np.sin(w0) / (2 * max(q, 0.01))
            return biquad_db(freqs, 1+al*A, -2*cw, 1-al*A, 1+al/A, -2*cw, 1-al/A)

        def high_shelf(freqs, g, fc, q):
            if abs(g) < 0.01: return np.zeros(len(freqs))
            A = 10**(g/40); w0 = 2*np.pi*fc/SR
            cw, sqA = np.cos(w0), np.sqrt(A)
            al = np.sin(w0) / (2 * max(q, 0.01))
            return biquad_db(freqs,
                A*((A+1)+(A-1)*cw+2*sqA*al), -2*A*((A-1)+(A+1)*cw), A*((A+1)+(A-1)*cw-2*sqA*al),
                (A+1)-(A-1)*cw+2*sqA*al, 2*((A-1)-(A+1)*cw), (A+1)-(A-1)*cw-2*sqA*al)

        N_BANDS = 28

        def draw(*_):
            cv.delete("all")
            w, h = cv.winfo_width(), cv.winfo_height()
            if w < 20 or h < 20: return
            bypassed = self.dsp_vars['eq_bypass'].get() or self.dsp_vars['master_bypass'].get()

            # ── Spectrum (background layer) ──────────────────────────────────
            buf = self.audio.spectrum_buffer.copy()
            if np.abs(buf).max() > 1e-6:
                win_fn = np.hanning(len(buf))
                mag = np.abs(np.fft.rfft(buf * win_fn))
                fft_freqs = np.fft.rfftfreq(len(buf), 1.0 / SR)
                edges = np.logspace(np.log10(20), np.log10(20000), N_BANDS + 1)
                for i in range(N_BANDS):
                    mask = (fft_freqs >= edges[i]) & (fft_freqs < edges[i+1])
                    if not mask.any(): continue
                    db = 20 * np.log10(max(mag[mask].mean() / len(buf), 1e-10)) + 72
                    bar_h = max(0, min(h, db / 72 * h * 0.85))
                    x0 = freq_to_x(edges[i],   w) + 1
                    x1 = freq_to_x(edges[i+1], w) - 1
                    if x1 > x0 and bar_h > 0:
                        cv.create_rectangle(x0, h - bar_h, x1, h, fill="#1a2e1a", outline="")

            y0 = gain_to_y(0, h)
            cv.create_line(0, y0, w, y0, fill="#333333")
            for gf, lbl in [(100, "100"), (1000, "1k"), (10000, "10k")]:
                gx = freq_to_x(gf, w)
                cv.create_line(gx, 0, gx, h, fill="#252525")
                cv.create_text(gx+2, h-1, text=lbl, fill="#404040", font=("Helvetica", 6), anchor='sw')

            freqs = np.logspace(np.log10(20), np.log10(20000), w)
            total = np.clip(
                low_shelf(freqs,  self.dsp_vars['eq_low'].get(),  self.dsp_vars['eq_low_freq'].get(),  self.dsp_vars['eq_low_q'].get()) +
                peak(freqs,       self.dsp_vars['eq_mid'].get(),  self.dsp_vars['eq_mid_freq'].get(),  self.dsp_vars['eq_mid_q'].get()) +
                high_shelf(freqs, self.dsp_vars['eq_high'].get(), self.dsp_vars['eq_high_freq'].get(), self.dsp_vars['eq_high_q'].get()),
                -GAIN_MAX*1.5, GAIN_MAX*1.5)
            pts = [c for i in range(w) for c in (i, gain_to_y(total[i], h))]
            if len(pts) >= 4:
                cv.create_line(*pts, fill="#444444" if bypassed else HIGHLIGHT, width=1.5, smooth=True)

            hcol = "#444444" if bypassed else HIGHLIGHT
            for band, gvar, fvar in [
                ('low',  self.dsp_vars['eq_low'],  self.dsp_vars['eq_low_freq']),
                ('mid',  self.dsp_vars['eq_mid'],  self.dsp_vars['eq_mid_freq']),
                ('high', self.dsp_vars['eq_high'], self.dsp_vars['eq_high_freq']),
            ]:
                hx = freq_to_x(fvar.get(), w)
                hy = gain_to_y(gvar.get(), h)
                active_band = band == _active['band']
                if bypassed:
                    fill = "#444444"
                elif active_band:
                    fill = "#ffffff"
                else:
                    fill = HIGHLIGHT
                cv.create_oval(hx-HR, hy-HR, hx+HR, hy+HR, fill=fill, outline=BG_MAIN, width=1.5)

        def on_press(e):
            w, h = cv.winfo_width(), cv.winfo_height()
            bands = [
                ('low',  self.dsp_vars['eq_low'],  self.dsp_vars['eq_low_freq'],  *FLIM['low']),
                ('mid',  self.dsp_vars['eq_mid'],  self.dsp_vars['eq_mid_freq'],  *FLIM['mid']),
                ('high', self.dsp_vars['eq_high'], self.dsp_vars['eq_high_freq'], *FLIM['high']),
            ]
            best, closest = None, 18
            for band, gvar, fvar, fmin, fmax in bands:
                hx = freq_to_x(fvar.get(), w)
                hy = gain_to_y(gvar.get(), h)
                d = ((e.x-hx)**2 + (e.y-hy)**2)**0.5
                if d < closest:
                    closest, best = d, (band, gvar, fvar, fmin, fmax)
            if best:
                drag['band'] = best[0]
                drag['gvar'], drag['fvar'] = best[1], best[2]
                drag['fmin'], drag['fmax'] = best[3], best[4]
                drag['y0'], drag['g0'] = e.y, best[1].get()
                _set_active(best[0])

        def on_drag(e):
            if not drag['band']: return
            w, h = cv.winfo_width(), cv.winfo_height()
            dy = e.y - drag['y0']
            new_g = max(-GAIN_MAX, min(GAIN_MAX, drag['g0'] - dy * GAIN_MAX / (h/2 - 6)))
            drag['gvar'].set(round(new_g, 1))
            new_f = x_to_freq(e.x, w, drag['fmin'], drag['fmax'])
            drag['fvar'].set(round(new_f, 1))
            # Sync proxy so info row stays live
            _active['band'] = None  # suppress feedback loop
            _freq_proxy.set(round(new_f, 1))
            freq_lbl.config(text=_fmt_freq(new_f))
            _active['band'] = drag['band']
            self.on_dsp_change()
            draw()

        def on_release(e):
            drag['band'] = None

        cv.bind('<ButtonPress-1>', on_press)
        cv.bind('<B1-Motion>', on_drag)
        cv.bind('<ButtonRelease-1>', on_release)
        cv.bind('<Configure>', lambda e: cv.after(10, draw))

        for v in [self.dsp_vars['eq_low'], self.dsp_vars['eq_mid'], self.dsp_vars['eq_high'],
                  self.dsp_vars['eq_low_freq'], self.dsp_vars['eq_mid_freq'], self.dsp_vars['eq_high_freq'],
                  self.dsp_vars['eq_low_q'], self.dsp_vars['eq_mid_q'], self.dsp_vars['eq_high_q'],
                  self.dsp_vars['eq_bypass'], self.dsp_vars['master_bypass']]:
            v.trace_add('write', draw)

        # ── Spectrum animation timer — pauses during scroll to avoid stutter ─
        def _animate():
            try:
                if not cv.winfo_exists():
                    return
                if time.monotonic() - self._last_scroll_t[0] > 0.2:
                    draw()
                cv.after(150, _animate)
            except tk.TclError:
                pass

        cv.after(400, _animate)

        # ── Bypass button ────────────────────────────────────────────────────
        def toggle_bypass(e):
            self._user_toggling_bypass = True
            try:
                self.dsp_vars['eq_bypass'].set(not self.dsp_vars['eq_bypass'].get())
                self.on_dsp_change()
            finally:
                self._user_toggling_bypass = False

        def draw_btn(*_):
            try:
                if not bypass_c.winfo_exists(): return
                bypass_c.delete("all")
                active = not self.dsp_vars['eq_bypass'].get() and not self.dsp_vars['master_bypass'].get()
                bypass_c.create_oval(2,2,14,14, fill="#ffd700" if active else BG_LIST,
                                     outline="#ffd700" if active else "#888888", width=1.5)
                title_lbl.config(fg=FG_TEXT if active else "#555555")
            except tk.TclError: pass

        bypass_c.bind("<Button-1>", toggle_bypass)
        bypass_c.bind("<Map>", draw_btn)
        self.dsp_vars['eq_bypass'].trace_add("write", draw_btn)
        self.dsp_vars['master_bypass'].trace_add("write", draw_btn)
        draw_btn()
        f.after(80, draw)
        f.after(100, lambda: _set_active('low'))
        return f

    def create_plugin_module(self, parent, title, module_id, bypass_var, parameters):
        f = tk.Frame(parent, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        header = tk.Frame(f, bg=BG_LIST)
        header.pack(fill=tk.X)
        
        order_frame = tk.Frame(header, bg=BG_LIST)
        order_frame.pack(side=tk.LEFT, padx=(5, 0))
        btn_up = tk.Label(order_frame, text="▲", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 7), cursor="hand2")
        btn_up.pack(side=tk.TOP)
        btn_down = tk.Label(order_frame, text="▼", bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 7), cursor="hand2")
        btn_down.pack(side=tk.TOP)
        
        btn_up.bind("<Button-1>", lambda e: self.move_module(module_id, 1))
        btn_down.bind("<Button-1>", lambda e: self.move_module(module_id, -1))

        title_lbl = tk.Label(header, text=title, bg=BG_LIST, fg=FG_TEXT, font=("Helvetica", 9, "bold"))
        title_lbl.pack(side=tk.LEFT, padx=5)
        
        body = tk.Frame(f, bg=BG_MAIN)
        body.pack(fill=tk.X, pady=5)
        
        ui_elements = []
        
        for name, var, from_, to_ in parameters:
            col = tk.Frame(body, bg=BG_MAIN)
            col.pack(side=tk.LEFT, expand=True)
            name_lbl = tk.Label(col, text=name, bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 8))
            name_lbl.pack()
            
            knob = Knob(col, var, from_, to_, command=self.on_dsp_change)
            knob.pack(pady=2)
            val_lbl = tk.Label(col, text="0.0", bg=BG_MAIN, fg="#aaaaaa", font=("Helvetica", 8))
            val_lbl.pack()
            
            def update_lbl(v_lbl=val_lbl, v_var=var, *args):
                v_lbl.config(text=f"{v_var.get():.1f}")
                
            var.trace_add('write', lambda *args, l=val_lbl, v=var: update_lbl(l, v, *args))
            val_lbl.config(text=f"{var.get():.1f}")
            ui_elements.append((name_lbl, knob, val_lbl))

        def update_visuals(*args):
            try:
                if not f.winfo_exists(): return
                master_bypassed = self.dsp_vars['master_bypass'].get()
                is_bypassed = bypass_var.get() or master_bypassed
                color = "#555555" if is_bypassed else FG_TEXT
                title_lbl.config(fg=color)
                for name_lbl, knob, val_lbl in ui_elements:
                    knob.set_disabled(is_bypassed)
                    name_lbl.config(fg=color)
                    val_lbl.config(fg=color)
            except tk.TclError:
                pass

        bypass_var.trace_add('write', update_visuals)
        
        btn_canvas = tk.Canvas(header, width=16, height=16, bg=BG_LIST, highlightthickness=0, cursor="hand2")
        btn_canvas.pack(side=tk.RIGHT, padx=8, pady=4)
        
        def toggle_bypass(event):
            self._user_toggling_bypass = True
            try:
                bypass_var.set(not bypass_var.get())
                self.on_dsp_change()
            finally:
                self._user_toggling_bypass = False

        def draw_btn(*args):
            try:
                if not btn_canvas.winfo_exists(): return
                btn_canvas.delete("all")
                master_bypassed = self.dsp_vars['master_bypass'].get()
                is_active = not bypass_var.get() and not master_bypassed
                if is_active:
                    btn_canvas.create_oval(2, 2, 14, 14, fill="#ffd700", outline="#ffd700")
                else:
                    btn_canvas.create_oval(2, 2, 14, 14, fill=BG_LIST, outline="#888888", width=1.5)
            except tk.TclError:
                pass
                
        btn_canvas.bind("<Button-1>", toggle_bypass)
        btn_canvas.bind("<Map>", draw_btn)
        bypass_var.trace_add("write", draw_btn)
        self.dsp_vars['master_bypass'].trace_add("write", update_visuals)
        self.dsp_vars['master_bypass'].trace_add("write", draw_btn)

        update_visuals() # Set initial visual state
        draw_btn()
        return f

    def on_dsp_change(self, *args):
        if getattr(self, '_loading_dsp', False): return

        # Flag to prevent re-triggering on_dsp_change when programmatically setting bypass
        if hasattr(self, '_setting_bypass_programmatically') and self._setting_bypass_programmatically:
            return
        self._setting_bypass_programmatically = True

        try:
            # Only auto-unbypass modules on parameter value changes, not on explicit bypass toggles
            if not getattr(self, '_user_toggling_bypass', False):
                # EQ
                if (self.dsp_vars['eq_low'].get() != 0.0 or
                    self.dsp_vars['eq_mid'].get() != 0.0 or
                    self.dsp_vars['eq_high'].get() != 0.0) and self.dsp_vars['eq_bypass'].get():
                    self.dsp_vars['eq_bypass'].set(False)

                # Dynamics
                if (self.dsp_vars['dyn_threshold'].get() != 0.0 or
                    self.dsp_vars['dyn_ratio'].get() != 1.0 or
                    self.dsp_vars['dyn_attack'].get() != 5.0 or
                    self.dsp_vars['dyn_release'].get() != 100.0 or
                    self.dsp_vars['dyn_makeup'].get() != 0.0) and self.dsp_vars['dyn_bypass'].get():
                    self.dsp_vars['dyn_bypass'].set(False)

                # Limiter
                if (self.dsp_vars['limiter_input'].get() != 0.0 or
                    self.dsp_vars['limiter_output'].get() != 0.0 or
                    self.dsp_vars['limiter_softclip'].get()) and self.dsp_vars['limiter_bypass'].get():
                    self.dsp_vars['limiter_bypass'].set(False)

            # If any module is active, ensure master bypass is off
            # Check all module bypasses, including VSTs
            any_module_active = (
                not self.dsp_vars['eq_bypass'].get() or
                not self.dsp_vars['dyn_bypass'].get() or
                not self.dsp_vars['limiter_bypass'].get() or
                any(not v['bypass'].get() for v in self.dsp_vars['vsts'].values())
            )

            if any_module_active and self.dsp_vars['master_bypass'].get() and not getattr(self, '_master_user_toggled', False):
                self.dsp_vars['master_bypass'].set(False)

        finally:
            self._setting_bypass_programmatically = False

        vsts_state = {}
        for k, v in self.dsp_vars.get('vsts', {}).items():
            vsts_state[k] = {
                'path': v['path'],
                'name': v['name'],
                'bypass': v['bypass'].get()
            }

        new_state = {
            'master_bypass': self.dsp_vars['master_bypass'].get(),
            'chain_order': self.dsp_vars['chain_order'].copy(),
            'eq_bypass': self.dsp_vars['eq_bypass'].get(),
            'eq_low': self.dsp_vars['eq_low'].get(), 'eq_mid': self.dsp_vars['eq_mid'].get(), 'eq_high': self.dsp_vars['eq_high'].get(),
            'eq_low_freq': self.dsp_vars['eq_low_freq'].get(), 'eq_mid_freq': self.dsp_vars['eq_mid_freq'].get(), 'eq_high_freq': self.dsp_vars['eq_high_freq'].get(),
            'eq_low_q': self.dsp_vars['eq_low_q'].get(), 'eq_mid_q': self.dsp_vars['eq_mid_q'].get(), 'eq_high_q': self.dsp_vars['eq_high_q'].get(),
            'dyn_bypass': self.dsp_vars['dyn_bypass'].get(),
            'dyn_threshold': self.dsp_vars['dyn_threshold'].get(), 'dyn_ratio': self.dsp_vars['dyn_ratio'].get(), 'dyn_attack': self.dsp_vars['dyn_attack'].get(), 'dyn_release': self.dsp_vars['dyn_release'].get(), 'dyn_makeup': self.dsp_vars['dyn_makeup'].get(),
            'limiter_bypass': self.dsp_vars['limiter_bypass'].get(), 'limiter_softclip': self.dsp_vars['limiter_softclip'].get(),
            'limiter_input': self.dsp_vars['limiter_input'].get(), 'limiter_output': self.dsp_vars['limiter_output'].get(),
            'vsts': vsts_state
        }
        
        # Instantly updates the live audio thread!
        self.audio.dsp_state = new_state 
        
        idx = self.get_active_idx()
        if idx is not None:
            self.project.tracks[idx]['dsp_state'] = new_state
            self.project.needs_save = True
    # --- UI SUPPORT METHODS ---
    def draw_vu_meter(self):
        self.vu_canvas.delete("all")
        h = VINYL_SIZE - 60
        w = 8
        if not self.audio.is_paused and self.audio.current_track and not self.is_keyboard_scrubbing:
            rms = getattr(self.audio, 'current_rms', 0.0)
            lufs = getattr(self.audio, 'current_lufs', -70.0)
            
            level = min(1.0, rms * 2.0)
            fill_height = h * level
            if level > 0.85: color = "#ff4444" 
            elif level > 0.6: color = "#ffaa00" 
            else: color = "#44ff44" 
            self.vu_canvas.create_rectangle(0, h - fill_height, w, h, fill=color, outline="")
            self.lbl_lufs.config(text=f"{lufs:>5.1f} LUFS")
        else:
            self.lbl_lufs.config(text="-- LUFS")

    def create_btn(self, parent, text, command, bg_color, width=None, bold=False):
        return create_btn(parent, text, command, bg_color, width, bold)

    def get_selected_idx(self):
        if not hasattr(self, 'tree'): return None
        selected = self.tree.selection()
        if not selected: return None
        return self.tree.index(selected[0])

    def get_active_idx(self):
        if self.audio.current_track and not self.audio.is_paused:
            for i, t in enumerate(self.project.tracks):
                if t['filename'] == self.audio.current_track:
                    return i
        return self.get_selected_idx()

    def select_first_track(self):
        """Selects and focuses the first track in the treeview if it exists."""
        if self.project.tracks and self.tree.get_children():
            first_item = self.tree.get_children()[0]
            self.tree.selection_set(first_item)
            self.tree.focus(first_item)
            self.tree.see(first_item)
            self.tree.focus_set()
            self.tree.event_generate("<<TreeviewSelect>>")

    def select_track_by_index(self, idx):
        if 0 <= idx < len(self.tree.get_children()):
            item = self.tree.get_children()[idx]
            self.tree.selection_set(item)
            self.tree.focus(item)
            self.tree.see(item)
            self.tree.focus_set()
            self.tree.event_generate("<<TreeviewSelect>>")

    def auto_load_last_session(self):
        last_folder = self.project.load_app_memory()
        if last_folder: self.project_actions.load_saved_set(preset_folder=last_folder)

    def update_norm_led(self):
        if self.project.settings.get("normalized", False):
            self.led_norm.config(text="● NORM", fg="#00ff00") 
        else:
            self.led_norm.config(text="○ RAW", fg="#555555") 

    def sync_ui_state(self):
        idx = self.get_active_idx()
        if idx is not None:
            track = self.project.tracks[idx]
            vol_pct = track.get('volume', 100.0)
            self.slider_volume.configure(command="")
            self.volume_var.set(vol_pct)
            self.lbl_volume.config(text=f"{vol_pct}%")
            self.slider_volume.configure(command=self.on_volume_slide)
            if self.bpm_entry != self.root.focus_get():
                self.bpm_var.set(str(track.get('bpm', '')))
            
            dsp = track.get('dsp_state', {
                'master_bypass': True,
                'chain_order': ['eq', 'dyn'],
                'eq_bypass': True, 'eq_low': 0.0, 'eq_mid': 0.0, 'eq_high': 0.0,
                'dyn_bypass': True, 'dyn_threshold': 0.0, 'dyn_ratio': 1.0, 'dyn_attack': 5.0, 'dyn_release': 100.0, 'dyn_makeup': 0.0,
                'limiter_bypass': True, 'limiter_softclip': False, 'limiter_input': 0.0, 'limiter_output': 0.0,
                'vsts': {}
            })
            
            current_order = self.dsp_vars.get('chain_order', ['eq', 'dyn'])
            new_order = dsp.get('chain_order', ['eq', 'dyn'])
            
            # Clean up old VSTs in UI
            track_vsts = dsp.get('vsts', {})
            for mod_id in list(self.rack_modules.keys()):
                if mod_id.startswith('vst_') and mod_id not in track_vsts:
                    self.rack_modules[mod_id].destroy()
                    del self.rack_modules[mod_id]
                    if mod_id in self.dsp_vars.get('vsts', {}):
                        del self.dsp_vars['vsts'][mod_id]

            if 'vsts' not in self.dsp_vars:
                self.dsp_vars['vsts'] = {}
                
            for mod_id, v_info in track_vsts.items():
                if mod_id not in self.dsp_vars['vsts']:
                    self.dsp_vars['vsts'][mod_id] = {
                        'path': v_info['path'],
                        'name': v_info['name'],
                        'bypass': tk.BooleanVar(value=v_info.get('bypass', False))
                    }
                else:
                    self.dsp_vars['vsts'][mod_id]['bypass'].set(v_info.get('bypass', False))
                    
                if mod_id not in self.rack_modules:
                    self.rack_modules[mod_id] = self.create_vst_module(self.rack_body, v_info['name'], mod_id, self.dsp_vars['vsts'][mod_id]['bypass'])

            new_order = [m for m in new_order if m in self.rack_modules or m in track_vsts]
            order_changed = current_order != new_order

            self._loading_dsp = True
            for k, v in dsp.items():
                if k == 'chain_order':
                    self.dsp_vars['chain_order'] = new_order.copy()
                elif k == 'vsts': pass
                elif k in self.dsp_vars:
                    self.dsp_vars[k].set(v)
            self._loading_dsp = False
            
            if order_changed:
                self.render_rack_order()
                
            self.on_dsp_change()
            
            self.vinyl_animator.update_artwork(track['filename'])
        else:
            self.vinyl_animator.update_artwork(None)
        self._refresh_sort_btn()
        self.update_set_length_labels()
        self._update_denoise_rack(self.project.tracks[idx] if idx is not None else None)

    # ── Denoise ─────────────────────────────────────────────────────────────

    # ── Master bypass ↔ denoise sync ────────────────────────────────────────

    def _on_master_bypass_toggle(self, bypassing):
        """Called after the effect-rack master power button is toggled."""
        idx = self.get_selected_idx()
        if idx is None:
            return
        track = self.project.tracks[idx]
        pos = self.audio.get_pos() / 1000.0 if self.audio.current_track == track['filename'] else 0.0
        was_playing = self.audio.current_track == track['filename'] and not self.audio.is_paused

        if bypassing:
            # Save denoise state and turn it off alongside the rack
            self._saved_denoise_state = track.get('use_denoised', False)
            if track.get('use_denoised'):
                track['use_denoised'] = False
                self._update_denoise_rack(track)
                self.project.needs_save = True
                if self.audio.current_track == track['filename']:
                    play_path = os.path.join(self.project.current_folder, self._get_playback_filename(track))
                    self.audio.play_from(play_path, pos, volume=track.get('volume', 100.0) / 100.0)
                    if not was_playing:
                        self.audio.toggle_pause()
        else:
            # Restore denoise state saved before bypass
            if self._saved_denoise_state and track.get('denoised_filename'):
                track['use_denoised'] = True
                self._update_denoise_rack(track)
                self.project.needs_save = True
                if self.audio.current_track == track['filename']:
                    play_path = os.path.join(self.project.current_folder, self._get_playback_filename(track))
                    noise_p, d_mix = self._get_noise_play_args(track)
                    self.audio.play_from(play_path, pos, volume=track.get('volume', 100.0) / 100.0,
                                         noise_path=noise_p, denoise_mix=d_mix)
                    if not was_playing:
                        self.audio.toggle_pause()
            self._saved_denoise_state = False

    def _activate_rack_if_needed(self):
        """Turn the effect rack on visually when denoise activates (cosmetic link)."""
        if self.dsp_vars['master_bypass'].get():
            self.dsp_vars['master_bypass'].set(False)
            self.on_dsp_change()

    # ── Denoise helpers ─────────────────────────────────────────────────────

    def _get_playback_filename(self, track):
        if track.get('use_denoised') and track.get('denoised_filename'):
            p = os.path.join(self.project.current_folder, track['denoised_filename'])
            if os.path.exists(p):
                return track['denoised_filename']
        return track['filename']

    def _get_noise_play_args(self, track):
        """Returns (noise_abs_path_or_None, denoise_mix) for play_from."""
        if track.get('use_denoised') and track.get('noise_filename'):
            p = os.path.join(self.project.current_folder, track['noise_filename'])
            if os.path.exists(p):
                return p, track.get('denoise_mix', 1.0)
        return None, 1.0

    # ── Denoise rack UI ─────────────────────────────────────────────────────

    def _build_denoise_rack(self, parent):
        # Single-row module — title | knob NN | Power ●
        rack = tk.Frame(parent, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        rack.pack(fill=tk.X, padx=5, pady=(0, 5))

        self._dr_title = tk.Label(rack, text="DENOISE", bg=BG_MAIN, fg="#555555",
                                   font=("Helvetica", 9, "bold"))
        self._dr_title.pack(side=tk.LEFT, padx=(5, 4), pady=5)

        # Power LED (right side)
        self._dr_led = tk.Canvas(rack, width=16, height=16, bg=BG_MAIN,
                                  highlightthickness=0, cursor="hand2")
        self._dr_led.pack(side=tk.RIGHT, padx=(0, 8), pady=5)
        tk.Label(rack, text="Power", bg=BG_MAIN, fg=FG_TEXT,
                 font=("Helvetica", 8)).pack(side=tk.RIGHT, padx=(0, 2))
        self._dr_led.bind("<Button-1>", lambda e: self.toggle_denoise())
        self._draw_denoise_led(False)

        # Knob + value (centre, shown when not processing)
        self._dr_knob_frame = tk.Frame(rack, bg=BG_MAIN)
        self._dr_knob = Knob(self._dr_knob_frame, self.denoise_mix_var, 0.0, 100.0,
                              command=self._on_denoise_mix_change, size=22)
        self._dr_knob.pack(side=tk.LEFT, padx=(0, 2))
        self._dr_knob.set_disabled(True)
        self._dr_val_lbl = tk.Label(self._dr_knob_frame, text="100", bg=BG_MAIN,
                                     fg="#555555", font=("Helvetica", 8), width=3)
        self._dr_val_lbl.pack(side=tk.LEFT)
        self._dr_knob_frame.pack(side=tk.LEFT, padx=4)
        self.denoise_mix_var.trace_add('write', lambda *_: self._dr_val_lbl.config(
            text=f"{self.denoise_mix_var.get():.0f}"))

        # Spinner (centre, shown during processing)
        self._dr_spinner_frame = tk.Frame(rack, bg=BG_MAIN)
        self._dr_spinner_lbl = tk.Label(self._dr_spinner_frame, text="⣾  Processing…",
                                         bg=BG_MAIN, fg="#888888", font=("Helvetica", 8))
        self._dr_spinner_lbl.pack(side=tk.LEFT)

    def _draw_denoise_led(self, active):
        self._dr_led.delete("all")
        if active:
            self._dr_led.create_oval(2, 2, 14, 14, fill="#ffd700", outline="#ffd700")
        else:
            self._dr_led.create_oval(2, 2, 14, 14, fill=BG_MAIN, outline="#888888", width=1.5)

    def _update_denoise_rack(self, track=None):
        if not hasattr(self, '_dr_title'):
            return

        # Determine track index
        track_idx = None
        if track is not None:
            try:
                track_idx = next(i for i, t in enumerate(self.project.tracks) if t is track)
            except StopIteration:
                pass

        processing = (track_idx is not None and track_idx in self._denoising_indices)

        if processing:
            self._dr_knob_frame.pack_forget()
            self._dr_spinner_frame.pack(side=tk.LEFT, padx=4)
            self._dr_title.config(fg=FG_TEXT)
            self._draw_denoise_led(False)
            return

        # Not processing — show knob
        self._dr_spinner_frame.pack_forget()
        self._dr_knob_frame.pack(side=tk.LEFT, padx=4)

        if track is None or not track.get('denoised_filename'):
            self._dr_title.config(fg="#555555")
            self._dr_knob.set_disabled(True)
            self._dr_val_lbl.config(fg="#555555")
            self._draw_denoise_led(False)
        elif track.get('use_denoised', False):
            self._dr_title.config(fg=FG_TEXT)
            self._dr_knob.set_disabled(False)
            self._dr_val_lbl.config(fg="#aaaaaa")
            self._draw_denoise_led(True)
            self.denoise_mix_var.set(round(track.get('denoise_mix', 1.0) * 100.0, 1))
        else:
            self._dr_title.config(fg="#555555")
            self._dr_knob.set_disabled(True)
            self._dr_val_lbl.config(fg="#555555")
            self._draw_denoise_led(False)
            self.denoise_mix_var.set(round(track.get('denoise_mix', 1.0) * 100.0, 1))

    def _on_denoise_mix_change(self, *_):
        idx = self.get_selected_idx()
        if idx is None:
            return
        track = self.project.tracks[idx]
        if not track.get('use_denoised'):
            return
        mix = self.denoise_mix_var.get() / 100.0
        track['denoise_mix'] = mix
        self.audio.denoise_mix = mix
        self.project.needs_save = True

    def toggle_denoise(self):
        idx = self.get_selected_idx()
        if idx is None:
            return
        if idx in self._denoising_indices:
            return  # processing in progress

        track = self.project.tracks[idx]

        if not track.get('denoised_filename'):
            self._start_denoising(idx)
            return

        was_playing = (self.audio.current_track == track['filename'] and not self.audio.is_paused)
        pos = self.audio.get_pos() / 1000.0 if self.audio.current_track == track['filename'] else 0.0

        track['use_denoised'] = not track.get('use_denoised', False)
        self._update_denoise_rack(track)
        self.project.needs_save = True
        if track['use_denoised']:
            self._activate_rack_if_needed()

        if self.audio.current_track == track['filename']:
            play_path = os.path.join(self.project.current_folder, self._get_playback_filename(track))
            noise_p, d_mix = self._get_noise_play_args(track)
            vol = track.get('volume', 100.0) / 100.0
            self.audio.play_from(play_path, pos, volume=vol, noise_path=noise_p, denoise_mix=d_mix)
            if not was_playing:
                self.audio.toggle_pause()

    def _download_and_install_denoiser(self, on_done, on_error):
        """Download SetBuilderDenoiser.zip, extract to ~/Applications, call on_done() or on_error(msg)."""
        import urllib.request
        import zipfile

        install_dir = os.path.expanduser(DENOISER_INSTALL_DIR)
        os.makedirs(install_dir, exist_ok=True)
        zip_path = os.path.join(install_dir, '_SetBuilderDenoiser_download.zip')

        dl_task  = 'denoiser_download'
        ext_task = 'denoiser_extract'

        self._enqueue_task(dl_task, 'Downloading denoiser 0%')

        def worker():
            try:
                # --- Download with progress ---
                with urllib.request.urlopen(DENOISER_DOWNLOAD_URL) as resp:
                    total = int(resp.headers.get('Content-Length', 0))
                    done  = 0
                    chunk = 1024 * 256
                    with open(zip_path, 'wb') as f:
                        while True:
                            buf = resp.read(chunk)
                            if not buf:
                                break
                            f.write(buf)
                            done += len(buf)
                            if total:
                                pct = done / total * 100
                                self.root.after(0, self._update_task_label, dl_task,
                                                f'Downloading denoiser {pct:.0f}%')
                self.root.after(0, self._finish_task, dl_task)
                self.root.after(0, self._enqueue_task, ext_task, 'Installing denoiser…')

                # --- Extract ---
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    zf.extractall(install_dir)
                os.remove(zip_path)

                self.root.after(0, self._finish_task, ext_task)
                self.root.after(0, on_done)
            except Exception as e:
                self.root.after(0, self._finish_task, dl_task)
                self.root.after(0, self._finish_task, ext_task)
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
                self.root.after(0, on_error, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _start_denoising(self, idx):
        companion, is_script = AudioEngine.find_denoiser_companion()
        if companion is None:
            # Ask user if they want to download the companion
            size_mb = 800
            ok = messagebox.askyesno(
                "Denoiser Module Required",
                f"The denoiser module is not installed.\n\n"
                f"Download and install it now? (~{size_mb} MB)\n\n"
                f"It will be saved to {DENOISER_INSTALL_DIR} and denoising will start automatically."
            )
            if not ok:
                return

            def on_installed():
                # Re-run now that the companion is in place
                self._start_denoising(idx)

            def on_install_error(msg):
                messagebox.showerror("Download Failed", f"Could not download denoiser:\n\n{msg}")

            self._download_and_install_denoiser(on_installed, on_install_error)
            return

        track = self.project.tracks[idx]
        src = os.path.join(self.project.current_folder, track['filename'])
        dest_rel   = f"denoised/{track['filename']}"
        noise_rel  = f"denoised/{os.path.splitext(track['filename'])[0]}_noise.mp3"
        dest       = os.path.join(self.project.current_folder, dest_rel)
        noise_dest = os.path.join(self.project.current_folder, noise_rel)

        self._denoising_indices.add(idx)
        self._update_denoise_rack(track)

        model_path    = os.path.join(AudioEngine.denoise_model_dir(), AudioEngine.DENOISE_MODEL)
        need_download = not os.path.exists(model_path)
        dl_task_id    = f"dl_model_{idx}"
        inf_task_id   = f"denoise_{idx}_{id(track)}"

        if need_download:
            self._enqueue_task(dl_task_id, "Downloading model 0%")
        else:
            self._enqueue_task(inf_task_id, "Denoising 0% of track")

        def on_download(pct):
            self.root.after(0, self._update_task_label, dl_task_id,
                            f"Downloading model {pct:.0f}%")

        def on_download_done():
            self.root.after(0, self._finish_task, dl_task_id)
            self.root.after(0, self._enqueue_task, inf_task_id, "Denoising 0% of track")

        def on_inference(pct):
            self.root.after(0, self._update_task_label, inf_task_id,
                            f"Denoised {pct:.0f}% of track")

        def worker():
            try:
                AudioEngine.run_denoiser_companion(
                    companion, is_script,
                    src, dest, noise_dest_path=noise_dest,
                    download_cb=on_download if need_download else None,
                    download_done_cb=on_download_done if need_download else None,
                    inference_cb=on_inference,
                )
                self.root.after(0, self._on_denoising_done, idx, dest_rel, noise_rel, inf_task_id)
            except Exception as e:
                if need_download:
                    self.root.after(0, self._finish_task, dl_task_id)
                self.root.after(0, self._on_denoising_error, idx, str(e), inf_task_id)

        threading.Thread(target=worker, daemon=True).start()

    def _on_denoising_done(self, idx, dest_rel, noise_rel, task_id):
        self._denoising_indices.discard(idx)
        self._finish_task(task_id)
        if idx < len(self.project.tracks):
            track = self.project.tracks[idx]
            track['denoised_filename'] = dest_rel
            track['noise_filename'] = noise_rel
            track['use_denoised'] = True
            track['denoise_mix'] = 1.0
            self._update_denoise_rack(track)
            self._activate_rack_if_needed()
            self.project.needs_save = True

    def _on_denoising_error(self, idx, err, task_id):
        self._denoising_indices.discard(idx)
        self._finish_task(task_id)
        sel = self.get_selected_idx()
        self._update_denoise_rack(self.project.tracks[sel] if sel is not None else None)
        messagebox.showerror("Denoise Failed", f"Denoise error:\n\n{err}")

    def _refresh_sort_btn(self):
        if not hasattr(self, 'btn_sort_out'): return
        sel = self.get_selected_idx()
        if sel is not None and self.project.tracks[sel].get('inactive', False):
            self.btn_sort_out.config(text="↺ Restore")
        else:
            self.btn_sort_out.config(text="✕ Remove")

    def on_double_click(self, event):
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)

        if not row_id: return

        # Allow editing Song (#4), Artist (#5), and Album (#6)
        if col_id in ("#4", "#5", "#6"):
            col_map = {"#4": "song", "#5": "artist", "#6": "album"}
            data_key = col_map[col_id]

            bbox = self.tree.bbox(row_id, col_id)
            if not bbox: return
            x, y, width, height = bbox

            val = self.tree.set(row_id, col_id)
            
            entry = tk.Entry(self.tree, font=("Courier", 11), bg=BG_LIST, fg=FG_TEXT, selectbackground=HIGHLIGHT, insertbackground=FG_TEXT, highlightthickness=1, highlightcolor=HIGHLIGHT, highlightbackground=HIGHLIGHT)
            entry.place(x=x, y=y, width=width, height=height)
            entry.insert(0, val)
            entry.select_range(0, tk.END)
            entry.focus_set()

            def save_edit(e=None):
                if not entry.winfo_exists(): return
                new_val = entry.get()
                self.tree.set(row_id, col_id, new_val)
                idx = self.tree.index(row_id)
                self.project.tracks[idx][data_key] = new_val
                self.project.needs_save = True
                entry.destroy()

            def cancel_edit(e=None):
                if entry.winfo_exists(): entry.destroy()

            entry.bind("<Return>", save_edit)
            entry.bind("<FocusOut>", save_edit)
            entry.bind("<Escape>", cancel_edit)
        else:
            self.play_audio() # Standard fast-play on double clicking non-editable areas

    def on_tree_select(self, event):
        if hasattr(self, '_sync_timer') and self._sync_timer:
            self.root.after_cancel(self._sync_timer)
        self._sync_timer = self.root.after(50, self.sync_ui_state)

    def on_volume_slide(self, val):
        idx = self.get_active_idx()
        if idx is not None:
            vol_pct = round(float(val), 1)
            self.project.tracks[idx]['volume'] = vol_pct
            self.lbl_volume.config(text=f"{vol_pct}%")
            
            linear_vol = vol_pct / 100.0
            track = self.project.tracks[idx]
            if self.audio.current_track == track['filename']:
                self.audio.set_realtime_volume(linear_vol)
            self.project.needs_save = True 

    def bind_shortcuts(self):
        self.root.bind("<KeyPress>", self.on_key_press)
        self.root.bind("<KeyRelease>", self.on_key_release)
        self.root.bind("<FocusIn>", self._on_app_focus_in)
        for key in ["<Up>", "<Down>", "<Left>", "<Right>"]:
            self.root.bind(key, self.on_arrow_override)
            self.tree.bind(key, self.on_arrow_override)
        self.root.bind("<space>", self.on_space_override)
        self.tree.bind("<space>", self.on_space_override)
        self.root.bind("<Command-z>", lambda e: self.undo())
        self.root.bind("<Command-Z>", lambda e: self.redo())
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-y>", lambda e: self.redo())
        self.root.bind("<Command-f>", lambda e: self.open_search_popup())
        self.root.bind("<Control-f>", lambda e: self.open_search_popup())

    # --- RESTORED: Transport logic ---
    def on_arrow_override(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Combobox)): return
        self.handle_arrows(event.keysym.lower())
        return "break"

    def on_space_override(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Combobox)): return
        self.toggle_play_pause()
        return "break"

    def _on_app_focus_in(self, event):
        if event.widget != self.root: return
        canvas = getattr(self, '_drop_canvas', None)
        if canvas and canvas.winfo_ismapped():
            self._hide_drop_overlay()

    def on_key_press(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Combobox)): return
        key = event.keysym.lower()
        if key == 'm': self.keys_down['m'] = True
        elif key == 'v': self.keys_down['v'] = True

    def on_key_release(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Combobox)): return
        key = event.keysym.lower()
        if key == 'm': 
            self.keys_down['m'] = False
            if self.is_keyboard_scrubbing: self.commit_keyboard_scrub()
        elif key == 'v': self.keys_down['v'] = False
        if key in ['up', 'down', 'left', 'right']: self.scrub_speed = 1.0 

    def handle_arrows(self, direction):
        if not self.project.tracks: return
        if self.keys_down['m']:
            if direction == 'left': self.play_prev()
            elif direction == 'right': self.play_next()
            elif direction in ['up', 'down']: self.handle_keyboard_scrub(direction)
        elif self.keys_down['v']:
            offset = -1 if direction == 'up' else 1 if direction == 'down' else -5 if direction == 'left' else 5
            self.shift_track(offset)
        else:
            offset = -1 if direction == 'up' else 1 if direction == 'down' else -5 if direction == 'left' else 5
            self.shift_cursor(offset)

    def handle_keyboard_scrub(self, direction):
        if not self.audio.current_track: return
        if not self.is_keyboard_scrubbing:
            self.is_keyboard_scrubbing = True
            self.scrub_speed = 1.0
            if not self.audio.is_paused: self.audio.toggle_pause() 

        self.scrub_speed = min(self.scrub_speed * 1.15, 12.0)
        offset = self.scrub_speed if direction == 'up' else -self.scrub_speed
        current_time = self.timeline_var.get()
        target_time = max(0, min(self.song_length, current_time + offset))
        
        self.timeline_var.set(target_time)
        self.lbl_current_time.config(text=self.format_time(target_time))

        if self.scrub_timer: self.root.after_cancel(self.scrub_timer)
        self.scrub_timer = self.root.after(250, self.commit_keyboard_scrub)

    def commit_keyboard_scrub(self):
        self.is_keyboard_scrubbing = False
        self.scrub_speed = 1.0
        target = self.timeline_var.get()
        
        idx = None
        for i, t in enumerate(self.project.tracks):
            if t['filename'] == self.audio.current_track:
                idx = i
                break
                
        if idx is not None:
            track = self.project.tracks[idx]
            vol = track.get('volume', 100.0) / 100.0
            noise_p, d_mix = self._get_noise_play_args(track)
            self.audio.play_from(os.path.join(self.project.current_folder, self._get_playback_filename(track)), target, volume=vol, noise_path=noise_p, denoise_mix=d_mix)
            self.seek_offset = target
            self.btn_play_pause.config(text="⏸ Pause")
            self.sync_ui_state()

    def shift_cursor(self, offset):
        idx = self.get_selected_idx()
        if idx is None:
            if self.project.tracks:
                first = self.tree.get_children()[0]
                self.tree.selection_set(first)
                self.tree.focus(first)
                self.tree.event_generate("<<TreeviewSelect>>")
            return
            
        new_idx = max(0, min(len(self.project.tracks) - 1, idx + offset))
        item = self.tree.get_children()[new_idx]
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.tree.see(item)
        self.tree.event_generate("<<TreeviewSelect>>") 

    def shift_track(self, offset):
        idx = self.get_selected_idx()
        if idx is not None:
            self.push_undo()
            new_idx = self.project.shift_track(idx, offset)
            if idx != new_idx:
                self.refresh_tree()
                item = self.tree.get_children()[new_idx]
                self.tree.selection_set(item)
                self.tree.focus(item)
                self.tree.see(item)
                self.tree.event_generate("<<TreeviewSelect>>")
                self.project.needs_save = True

    def toggle_inactive(self):
        idx = self.get_selected_idx()
        if idx is None: return
        self.push_undo()
        new_idx = self.project.toggle_inactive(idx)
        self.refresh_tree()
        self.select_track_by_index(new_idx)
        self.project.needs_save = True

    def _on_drag_press(self, event):
        if self.tree.identify_region(event.x, event.y) in ("heading", "separator"):
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            self._drag_src_idx = None
            return
        self._drag_src_idx = self.tree.index(row_id)
        self._drag_y_start = event.y_root
        self._drag_active = False
        self._drop_intended_idx = None

    def _on_drag_motion(self, event):
        if self._drag_src_idx is None: return
        if not self._drag_active:
            if abs(event.y_root - self._drag_y_start) < 6:
                return
            self._drag_active = True
            try: self.tree.config(cursor="hand2")
            except tk.TclError: pass
            if self._drop_indicator is None:
                self._drop_indicator = tk.Frame(self.list_frame, bg=HIGHLIGHT, height=2)

        n = len(self.project.tracks)
        if n == 0:
            return

        over_id = self.tree.identify_row(event.y)
        if over_id:
            over_idx = self.tree.index(over_id)
            bbox = self.tree.bbox(over_id)
            if bbox:
                x, y, w, h = bbox
                if event.y < y + h / 2:
                    intended = over_idx
                    indicator_y = y
                else:
                    intended = over_idx + 1
                    indicator_y = y + h
            else:
                intended = over_idx
                indicator_y = 0
        else:
            # Below all rows — drop at end
            intended = n
            last_id = self.tree.get_children()[-1] if self.tree.get_children() else None
            bbox = self.tree.bbox(last_id) if last_id else None
            if bbox:
                indicator_y = bbox[1] + bbox[3]
            else:
                indicator_y = 0

        self._drop_intended_idx = intended
        self._drop_indicator.place(x=0, y=max(0, indicator_y - 1), relwidth=1.0)
        self._drop_indicator.lift()

    def _on_drag_release(self, event):
        if not self._drag_active or self._drag_src_idx is None or self._drop_intended_idx is None:
            self._reset_drag()
            return

        src = self._drag_src_idx
        intended = self._drop_intended_idx
        n = len(self.project.tracks)
        intended = max(0, min(n, intended))
        new_idx = intended - 1 if src < intended else intended
        new_idx = max(0, min(n - 1, new_idx))

        if new_idx != src:
            self.push_undo()
            offset = new_idx - src
            actual_new = self.project.shift_track(src, offset)
            self.refresh_tree()
            self.select_track_by_index(actual_new)
            self.project.needs_save = True

        self._reset_drag()

    def _cancel_drag(self, event=None):
        if self._drag_active:
            self._reset_drag()

    def _reset_drag(self):
        self._drag_src_idx = None
        self._drag_active = False
        self._drop_intended_idx = None
        if self._drop_indicator is not None:
            try: self._drop_indicator.place_forget()
            except tk.TclError: pass
        try: self.tree.config(cursor="")
        except tk.TclError: pass

    def _setup_file_drop(self):
        if not _HAS_DND or not hasattr(self.root, 'drop_target_register'):
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.on_file_drop)
            self.root.dnd_bind('<<DropEnter>>', self._on_drop_enter)
            self.root.dnd_bind('<<DropLeave>>', self._on_drop_leave)
            self.root.dnd_bind('<<DropPosition>>', self._on_drop_position)
        except Exception as e:
            print(f"DnD setup failed: {e}")

    def _on_drop_enter(self, event):
        self._show_drop_overlay()
        self._reset_drag_heartbeat()

    def _on_drop_position(self, event):
        self._reset_drag_heartbeat()
        return 'copy'

    def _on_drop_leave(self, event):
        self._cancel_drag_heartbeat()
        self._hide_drop_overlay()

    def _reset_drag_heartbeat(self):
        self._cancel_drag_heartbeat()
        self._drag_heartbeat = self.root.after(600, self._hide_drop_overlay)

    def _cancel_drag_heartbeat(self):
        job = getattr(self, '_drag_heartbeat', None)
        if job:
            self.root.after_cancel(job)
            self._drag_heartbeat = None

    def _show_drop_overlay(self):
        if not hasattr(self, '_drop_canvas') or self._drop_canvas is None:
            W, H, R = 260, 220, 22
            C = "#111111"
            self._drop_canvas = tk.Canvas(
                self.root, bg=BG_MAIN, highlightthickness=0, width=W, height=H)
            # Rounded rectangle: two fill rects + four corner arcs
            self._drop_canvas.create_rectangle(R, 0, W-R, H, fill=C, outline=C)
            self._drop_canvas.create_rectangle(0, R, W, H-R, fill=C, outline=C)
            for ax, ay, start in [(0,0,90),(W-2*R,0,0),(W-2*R,H-2*R,270),(0,H-2*R,180)]:
                self._drop_canvas.create_arc(ax, ay, ax+2*R, ay+2*R,
                                             start=start, extent=90, fill=C, outline=C)
            self._drop_canvas.create_text(W//2, H//2, text="+", fill=HIGHLIGHT,
                                          font=("Helvetica", 110, "bold"))
        try:
            if not self._drop_canvas.winfo_ismapped():
                self._drop_canvas.place(relx=0.5, rely=0.5, anchor='center')
                self._drop_canvas.lift()
        except tk.TclError:
            pass

    def _hide_drop_overlay(self):
        self._cancel_drag_heartbeat()
        if hasattr(self, '_drop_canvas') and self._drop_canvas is not None:
            try:
                self._drop_canvas.destroy()
            except tk.TclError:
                pass
            self._drop_canvas = None
        # Schedule repaint for next tick — forces macOS to redraw even if unfocused
        self.root.after(0, self.root.update_idletasks)

    def on_file_drop(self, event):
        self._hide_drop_overlay()
        if not self.project.is_saved_set:
            messagebox.showinfo("No Project", "Create or load a project first, then drop tracks onto the window.")
            return
        try:
            raw = self.root.tk.splitlist(event.data)
        except Exception:
            raw = event.data.split() if isinstance(event.data, str) else []

        valid_exts = ('.mp3', '.wav', '.flac', '.wma')
        files = []
        for p in raw:
            path = p.strip().strip('{}')
            if os.path.isdir(path):
                for root, _, names in os.walk(path):
                    for name in sorted(names):
                        if name.lower().endswith(valid_exts) and not name.startswith('.'):
                            files.append(os.path.join(root, name))
            elif os.path.isfile(path) and path.lower().endswith(valid_exts):
                files.append(path)

        if not files:
            messagebox.showinfo("No Audio", "No supported audio files (.mp3, .wav, .flac, .wma) found in the drop.")
            return

        self.project_actions.add_tracks(files=files)


    # --- RESTORED: format_time, on_slider_press, update_timeline_ui ---
    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def update_timeline_ui(self):
        self.draw_vu_meter()
        if not self.audio.is_paused and not self.is_dragging_slider and not self.is_keyboard_scrubbing:
            current_pos = self.audio.get_pos()
            if current_pos != -1:
                real_time = current_pos / 1000.0
                if real_time >= self.song_length - 0.5:
                    self.play_next()
                else:
                    self.timeline_var.set(real_time)
                    self.lbl_current_time.config(text=self.format_time(real_time))
        
        self.update_job = self.root.after(100, self.update_timeline_ui)

    def on_timeline_interaction(self, val, dragging):
        self.is_dragging_slider = dragging
        self.lbl_current_time.config(text=self.format_time(val))
        
        if not dragging:
            if not self.audio.current_track: return
            idx = None
            for i, t in enumerate(self.project.tracks):
                if t['filename'] == self.audio.current_track:
                    idx = i
                    break
                    
            if idx is not None:
                track = self.project.tracks[idx]
                vol = track.get('volume', 100.0) / 100.0
                noise_p, d_mix = self._get_noise_play_args(track)
                self.audio.play_from(os.path.join(self.project.current_folder, self._get_playback_filename(track)), val, volume=vol, noise_path=noise_p, denoise_mix=d_mix)
                self.seek_offset = val
                self.btn_play_pause.config(text="⏸ Pause")
                self.sync_ui_state()


    def modify_bpm(self, multiplier):
        idx = self.get_selected_idx()
        if idx is not None:
            self.push_undo()
            self.project.modify_bpm(idx, multiplier)
            self.refresh_tree()
            item = self.tree.get_children()[idx]
            self.tree.selection_set(item)
            self.project.needs_save = True

    def _on_bpm_edit(self, event=None):
        idx = self.get_selected_idx()
        if idx is None:
            return
        try:
            new_bpm = int(self.bpm_var.get().strip())
            if new_bpm <= 0:
                raise ValueError
        except ValueError:
            self.bpm_var.set(str(self.project.tracks[idx].get('bpm', '')))
            return
        if new_bpm == self.project.tracks[idx].get('bpm'):
            return
        self.push_undo()
        self.project.tracks[idx]['bpm'] = new_bpm
        children = self.tree.get_children()
        if idx < len(children):
            cur_vals = list(self.tree.item(children[idx], 'values'))
            cur_vals[1] = str(new_bpm)
            self.tree.item(children[idx], values=cur_vals)
        self.project.needs_save = True

    def resort_project(self):
        if not self.project.tracks: return
        self.push_undo()
        self.project.resort_by_bpm()
        self.refresh_tree()
        self.project.needs_save = True

    # ── Search ──────────────────────────────────────────────────────────────

    # Fold Turkish (and common Latin diacritics) to base ASCII so that
    # "satel" matches "Şatellites", "ı" matches "i", etc.
    _FOLD = str.maketrans(
        'şŞıİöÖğĞüÜçÇâÂîÎûÛàáäãåÀÁÄÃÅèéêëÈÉÊËìíîïÌÍÎÏòóôõøÒÓÔÕØùúûÙÚÛýÿÝ',
        'sSiIoOgGuUcCaAiIuUaaaaaAAAAAeeeeEEEEiiiiIIIIoooooOOOOOuuuUUUyyY'
    )

    @staticmethod
    def _fold(text):
        return text.lower().translate(DJAppUI._FOLD)

    def _search_score(self, track, query):
        q = self._fold(query.strip())
        if not q:
            return 0
        corpus = self._fold(' '.join(filter(None, [
            track.get('song', ''), track.get('artist', ''), track.get('album', ''),
            track.get('original_name', ''), str(track.get('bpm', '')), track.get('tone', ''),
        ])))
        tokens = q.split()
        score = 0
        if q in corpus:
            score += 100                        # full query exact hit
        for token in tokens:
            if not token:
                continue
            if token in corpus:
                score += 50                     # token substring hit
            for word in corpus.split():
                if token in word:
                    score += 25                 # word contains token
                elif word in token:
                    score += 15
                else:
                    r = SequenceMatcher(None, token, word).ratio()
                    if r > 0.7:
                        score += r * 30         # fuzzy word similarity
        return score

    def open_search_popup(self):
        if not self.project.tracks:
            return

        win = tk.Toplevel(self.root)
        win.title("Search")
        win.geometry("460x280")
        win.configure(bg=BG_MAIN)
        win.resizable(False, False)
        win.transient(self.root)

        search_var = tk.StringVar()
        entry = tk.Entry(win, textvariable=search_var, bg=BG_LIST, fg=FG_TEXT,
                         insertbackground=FG_TEXT, font=("Helvetica", 13), bd=0,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=HIGHLIGHT)
        entry.pack(fill=tk.X, padx=14, pady=(14, 6), ipady=7)
        entry.focus_set()

        list_frame = tk.Frame(win, bg=BG_MAIN)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))
        sb = ttk.Scrollbar(list_frame, orient="vertical")
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        results_box = tk.Listbox(list_frame, bg=BG_LIST, fg=FG_TEXT,
                                  selectbackground=HIGHLIGHT, selectforeground='#ffffff',
                                  font=("Courier", 11), bd=0, highlightthickness=0,
                                  activestyle='none', yscrollcommand=sb.set)
        results_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=results_box.yview)

        result_indices = []
        _timer = [None]

        def do_search():
            q = search_var.get().strip()
            results_box.delete(0, tk.END)
            result_indices.clear()
            if not q:
                return
            scored = sorted(
                ((self._search_score(t, q), i, t) for i, t in enumerate(self.project.tracks)),
                key=lambda x: x[0], reverse=True
            )
            for score, idx, track in scored[:5]:
                if score <= 0:
                    break
                song = track.get('song') or track.get('original_name', track['filename'])
                artist = track.get('artist', '')
                line = f"#{idx+1:02d}  {track.get('bpm','?')} BPM  {song}"
                if artist:
                    line += f"  — {artist}"
                results_box.insert(tk.END, f"  {line}")
                result_indices.append(idx)
            if result_indices:
                results_box.selection_set(0)

        def on_type(*_):
            if _timer[0]:
                win.after_cancel(_timer[0])
            _timer[0] = win.after(180, do_search)

        def select_result(e=None):
            sel = results_box.curselection()
            if not sel or not result_indices:
                return
            idx = result_indices[sel[0]]
            win.destroy()
            self.select_track_by_index(idx)
            self.tree.focus_set()

        search_var.trace_add('write', on_type)
        results_box.bind('<Double-Button-1>', select_result)
        results_box.bind('<Return>', select_result)
        entry.bind('<Return>', select_result)
        entry.bind('<Down>', lambda e: (results_box.focus_set(),
                                        results_box.selection_clear(0, tk.END),
                                        results_box.selection_set(0)) and None)
        win.bind('<Escape>', lambda e: win.destroy())

    def push_undo(self):
        self._undo_stack.append(copy.deepcopy(self.project.tracks))
        self._redo_stack.clear()
        self._update_history_buttons()

    def undo(self):
        if not self._undo_stack: return
        self._redo_stack.append(copy.deepcopy(self.project.tracks))
        self.project.tracks = self._undo_stack.pop()
        self.project.needs_save = True
        self.refresh_tree()
        self._update_history_buttons()

    def redo(self):
        if not self._redo_stack: return
        self._undo_stack.append(copy.deepcopy(self.project.tracks))
        self.project.tracks = self._redo_stack.pop()
        self.project.needs_save = True
        self.refresh_tree()
        self._update_history_buttons()

    def _update_history_buttons(self):
        self.btn_undo.config(state=tk.NORMAL if self._undo_stack else tk.DISABLED)
        self.btn_redo.config(state=tk.NORMAL if self._redo_stack else tk.DISABLED)

    def update_set_length_labels(self):
        if not self.project.tracks:
            self.lbl_set_total.config(text="--:--")
            self.lbl_set_upto.config(text="--:--")
            return
        total = sum(t.get('duration', 0) for t in self.project.tracks)
        self.lbl_set_total.config(text=self.format_time(total))
        idx = self.get_active_idx()
        if idx is not None:
            upto = sum(t.get('duration', 0) for t in self.project.tracks[:idx + 1])
            self.lbl_set_upto.config(text=self.format_time(upto))
        else:
            self.lbl_set_upto.config(text="--:--")

    def _ensure_default_thumb(self):
        if hasattr(self, 'default_thumb') and self.default_thumb is not None:
            return
        default_art_path = get_resource_path(os.path.join("assets", "default.png"))
        def_pil = None
        if os.path.exists(default_art_path):
            try:
                def_pil = Image.open(default_art_path).convert("RGBA")
                def_pil = ImageOps.fit(def_pil, (36, 36), Image.Resampling.LANCZOS)
            except Exception:
                def_pil = None
        if def_pil is None:
            def_pil = Image.new("RGBA", (36, 36), (40, 40, 40, 255))
        self.default_thumb = ImageTk.PhotoImage(def_pil)
        self.default_thumb_dim = ImageTk.PhotoImage(ImageEnhance.Brightness(def_pil).enhance(0.25))

    def _append_track_to_tree(self, track):
        """Insert one track row without rebuilding the whole tree."""
        self._ensure_default_thumb()
        if not hasattr(self, 'tree_images'):
            self.tree_images = {}

        # Identity lookup so duplicate-content dicts don't collide
        idx = next((i for i, t in enumerate(self.project.tracks) if t is track),
                   len(self.project.tracks) - 1)

        is_inactive = track.get('inactive', False)
        song = track.get('song', track.get('original_name', track['filename'])) or track['filename']

        thumb_img = self.default_thumb_dim if is_inactive else self.default_thumb
        thumb_filename = track.get('thumb_filename', '')
        if thumb_filename:
            thumb_path = os.path.join(self.project.current_folder, thumb_filename)
            if os.path.exists(thumb_path):
                try:
                    img = Image.open(thumb_path).convert("RGBA")
                    img = ImageOps.fit(img, (36, 36), Image.Resampling.LANCZOS)
                    if is_inactive:
                        img = ImageEnhance.Brightness(img).enhance(0.25)
                    photo = ImageTk.PhotoImage(img)
                    self.tree_images[idx] = photo
                    thumb_img = photo
                except Exception:
                    pass

        # Insert before the first inactive row so active tracks stay on top
        insert_pos = tk.END
        if not is_inactive:
            for child in reversed(self.tree.get_children()):
                if 'inactive' in self.tree.item(child, 'tags'):
                    insert_pos = self.tree.index(child)
                else:
                    break

        self.tree.insert("", insert_pos, text="", image=thumb_img or "", values=(
            f"{idx + 1}", f"{track['bpm']}", f"{track['tone']}", song,
            track.get('artist', ''), track.get('album', ''),
            f"{track.get('size_mb', 0.0):.1f}",
            "●" if track.get('is_normalized', False) else "○"
        ), tags=("inactive",) if is_inactive else ())
        self.update_set_length_labels()

    def refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.tree_images = {}
        self.default_thumb = None  # Force refresh_tree to always regen thumbnails
        self._ensure_default_thumb()

        for i, track in enumerate(self.project.tracks):
            is_inactive = track.get('inactive', False)
            norm_dot = "●" if track.get('is_normalized', False) else "○"
            size_str = f"{track.get('size_mb', 0.0):.1f}"

            artist = track.get('artist', '')
            album = track.get('album', '')
            song = track.get('song', track.get('original_name', track['filename']))
            if not song: song = track.get('original_name', track['filename'])

            thumb_img = self.default_thumb_dim if is_inactive else self.default_thumb
            thumb_filename = track.get('thumb_filename', '')
            if thumb_filename:
                thumb_path = os.path.join(self.project.current_folder, thumb_filename)
                if os.path.exists(thumb_path):
                    try:
                        img = Image.open(thumb_path).convert("RGBA")
                        img = ImageOps.fit(img, (36, 36), Image.Resampling.LANCZOS)
                        if is_inactive:
                            img = ImageEnhance.Brightness(img).enhance(0.25)
                        photo = ImageTk.PhotoImage(img)
                        self.tree_images[i] = photo
                        thumb_img = photo
                    except Exception:
                        pass

            self.tree.insert("", tk.END, text="", image=thumb_img if thumb_img else "", values=(
                f"{i + 1}",
                f"{track['bpm']}",
                f"{track['tone']}",
                song,
                artist,
                album,
                size_str,
                norm_dot
            ), tags=("inactive",) if is_inactive else ())
        self.update_set_length_labels()
        self._refresh_sort_btn()

    def toggle_play_pause(self):
        try:
            if not self.audio.current_track:
                self.play_audio()
                return
            idx = self.get_selected_idx()
            if idx is None: return
            track = self.project.tracks[idx]

            if self.audio.current_track != track['filename']:
                self.play_audio()
                return

            if self.audio.is_paused:
                self.audio.toggle_pause()
                self.btn_play_pause.config(text="⏸ Pause")
                bpm = track.get('bpm', 120)
                self.vinyl_animator.set_speed(max(1.5, bpm / 35.0))
            else:
                self.audio.toggle_pause()
                self.btn_play_pause.config(text="▶ Play")
                self.vinyl_animator.set_speed(0.0)
                
            self.sync_ui_state()
        except IndexError: pass

    def play_audio(self):
        try:
            idx = self.get_selected_idx()
            if idx is None: return
            track = self.project.tracks[idx]
            self.song_length = track['duration']
            self.timeline.set_max(self.song_length)
            self.seek_offset = 0
            self.timeline_var.set(0)
            self.lbl_total_time.config(text=self.format_time(self.song_length))
            self.lbl_current_time.config(text="00:00")

            self.vinyl_animator.update_artwork(track['filename'])
            bpm = track.get('bpm', 120)
            self.vinyl_animator.set_speed(max(1.5, bpm / 35.0))

            self.audio.current_track = track['filename']
            vol = track.get('volume', 100.0) / 100.0
            noise_p, d_mix = self._get_noise_play_args(track)
            self.audio.play(os.path.join(self.project.current_folder, self._get_playback_filename(track)), volume=vol, noise_path=noise_p, denoise_mix=d_mix)
            self.btn_play_pause.config(text="⏸ Pause")
            
            if self.update_job: self.root.after_cancel(self.update_job)
            self.update_timeline_ui()
            self.sync_ui_state()
        except IndexError: pass
        
    def play_next(self):
        idx = self.get_selected_idx()
        if idx is not None and idx < len(self.project.tracks) - 1:
            next_item = self.tree.get_children()[idx + 1]
            self.tree.selection_set(next_item)
            self.tree.focus(next_item)
            self.tree.see(next_item)
            self.tree.event_generate("<<TreeviewSelect>>")
            self.play_audio()
        else:
            self.audio.stop()
            self.btn_play_pause.config(text="▶ Play")
            self.timeline_var.set(0)
            self.lbl_current_time.config(text="00:00")
            self.lbl_total_time.config(text="00:00")
            self.vinyl_animator.set_speed(0.0)
            self.sync_ui_state()

    def play_prev(self):
        current_time = self.timeline_var.get()
        if current_time > 5.0:
            idx = None
            for i, t in enumerate(self.project.tracks):
                if t['filename'] == self.audio.current_track:
                    idx = i
                    break
            if idx is not None:
                track = self.project.tracks[idx]
                vol = track.get('volume', 100.0) / 100.0
                noise_p, d_mix = self._get_noise_play_args(track)
                self.audio.play_from(os.path.join(self.project.current_folder, self._get_playback_filename(track)), 0, volume=vol, noise_path=noise_p, denoise_mix=d_mix)
                self.seek_offset = 0
                self.timeline_var.set(0)
                self.btn_play_pause.config(text="⏸ Pause")
                self.sync_ui_state()
        else:
            idx = self.get_selected_idx()
            if idx is not None and idx > 0:
                prev_item = self.tree.get_children()[idx - 1]
                self.tree.selection_set(prev_item)
                self.tree.focus(prev_item)
                self.tree.see(prev_item)
                self.tree.event_generate("<<TreeviewSelect>>")
                self.play_audio()
            elif idx == 0:
                track = self.project.tracks[idx]
                vol = track.get('volume', 100.0) / 100.0
                noise_p, d_mix = self._get_noise_play_args(track)
                self.audio.play_from(os.path.join(self.project.current_folder, self._get_playback_filename(track)), 0, volume=vol, noise_path=noise_p, denoise_mix=d_mix)
                self.seek_offset = 0
                self.timeline_var.set(0)
                self.sync_ui_state()

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support() # Fix joblib/multiprocessing spawning issues in PyInstaller
    root = None
    if _HAS_DND:
        try:
            root = TkinterDnD.Tk()
        except Exception as e:
            print(f"[warn] tkdnd unavailable ({e}); drag-and-drop disabled.")
            _HAS_DND = False
            # TkinterDnD.Tk()'s super().__init__ already created a Tk interpreter before
            # _require() failed; tkinter._default_root may still point at it. Tear it down
            # so the fresh tk.Tk() below is the only root.
            try:
                if tk._default_root is not None:
                    tk._default_root.destroy()
            except Exception:
                pass
            tk._default_root = None
    if root is None:
        root = tk.Tk()
    app = DJAppUI(root)
    root.mainloop()
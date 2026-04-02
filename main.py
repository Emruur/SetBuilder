import os
import sys
import threading
import time
import datetime
import random
import tkinter as tk
from tkinter import messagebox, ttk, filedialog

from AudioCore import AudioEngine
from ProjectManager import ProjectState
from PIL import Image, ImageTk, ImageOps

from constants import BG_MAIN, BG_LIST, FG_TEXT, BTN_NORMAL, BTN_HOVER, HIGHLIGHT, BORDER, VINYL_SIZE, CENTER_HOLE
from ui_components import create_btn, create_group_frame, Knob, Timeline
from export_dialog import ExportDialog
from vinyl_animator import VinylAnimator
from project_actions import ProjectActions

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
        
        self.is_keyboard_scrubbing = False
        self.scrub_speed = 1.0
        self.scrub_timer = None
        self.keys_down = {'m': False, 'v': False}

        self.setup_ui()
        self.bind_shortcuts()
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
        
        # Enable cross-platform mouse wheel scrolling over the effect rack
        def _on_mousewheel(event):
            req_h = left_inner.winfo_reqheight()
            canv_h = self.left_canvas.winfo_height()
            if req_h > canv_h: # Only scroll if content is taller than canvas
                if hasattr(event, 'num') and event.num == 4: self.left_canvas.yview_scroll(-1, "units")
                elif hasattr(event, 'num') and event.num == 5: self.left_canvas.yview_scroll(1, "units")
                else: self.left_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        def _bind_mousewheel(event):
            self.left_canvas.bind_all("<MouseWheel>", _on_mousewheel)
            self.left_canvas.bind_all("<Button-4>", _on_mousewheel)
            self.left_canvas.bind_all("<Button-5>", _on_mousewheel)
        def _unbind_mousewheel(event):
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"): self.left_canvas.unbind_all(seq)
        self.left_canvas.bind('<Enter>', _bind_mousewheel)
        self.left_canvas.bind('<Leave>', _unbind_mousewheel)

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
            self.dsp_vars['master_bypass'].set(not self.dsp_vars['master_bypass'].get())
            self.on_dsp_change()

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
            self.dsp_vars['limiter_bypass'].set(not self.dsp_vars['limiter_bypass'].get())
            self.on_dsp_change()
            
        def draw_lim_btn(*args):
            lim_btn_canvas.delete("all")
            master_bypassed = self.dsp_vars['master_bypass'].get()
            is_active = not self.dsp_vars['limiter_bypass'].get() and not master_bypassed
            if is_active: lim_btn_canvas.create_oval(2, 2, 14, 14, fill="#ffd700", outline="#ffd700")
            else: lim_btn_canvas.create_oval(2, 2, 14, 14, fill=BG_LIST, outline="#888888", width=1.5)
                
        def toggle_sc_bypass(event):
            if not self.dsp_vars['limiter_bypass'].get() and not self.dsp_vars['master_bypass'].get():
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

        # 1. EQ Module
        self.dsp_vars['eq_bypass'] = tk.BooleanVar(value=True)
        self.dsp_vars['eq_low'] = tk.DoubleVar(value=0.0)
        self.dsp_vars['eq_mid'] = tk.DoubleVar(value=0.0)
        self.dsp_vars['eq_high'] = tk.DoubleVar(value=0.0)
        self.rack_modules['eq'] = self.create_plugin_module(self.rack_body, "EQ", 'eq', self.dsp_vars['eq_bypass'], [
            ("High", self.dsp_vars['eq_high'], -15.0, 15.0),
            ("Mid", self.dsp_vars['eq_mid'], -15.0, 15.0),
            ("Low", self.dsp_vars['eq_low'], -15.0, 15.0)
        ])

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
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        columns = ("num", "bpm", "tone", "artist", "album", "song", "size", "lufs", "norm")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="tree headings", yscrollcommand=scrollbar.set, selectmode="browse", style="Treeview")
        
        self.tree.heading("#0", text="Art")
        self.tree.column("#0", width=60, anchor=tk.CENTER, stretch=tk.NO)
        
        self.tree.heading("num", text="#")
        self.tree.column("num", width=30, anchor=tk.CENTER, stretch=tk.NO)

        self.tree.heading("bpm", text="BPM")
        self.tree.column("bpm", width=50, anchor=tk.CENTER, stretch=tk.NO)
        self.tree.heading("tone", text="Key")
        self.tree.column("tone", width=50, anchor=tk.CENTER, stretch=tk.NO)
        self.tree.heading("artist", text="Artist")
        self.tree.column("artist", width=120, anchor=tk.W, stretch=tk.YES)
        self.tree.heading("album", text="Album")
        self.tree.column("album", width=120, anchor=tk.W, stretch=tk.YES)
        self.tree.heading("song", text="Song")
        self.tree.column("song", width=160, anchor=tk.W, stretch=tk.YES)
        self.tree.heading("size", text="Size")
        self.tree.column("size", width=50, anchor=tk.E, stretch=tk.NO)
        self.tree.heading("lufs", text="LUFS")
        self.tree.column("lufs", width=50, anchor=tk.E, stretch=tk.NO)
        self.tree.heading("norm", text="Norm")
        self.tree.column("norm", width=40, anchor=tk.CENTER, stretch=tk.NO)

        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        scrollbar.config(command=self.tree.yview)
        
        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)

        def prevent_art_resize(event):
            if self.tree.identify_region(event.x, event.y) == "separator" and self.tree.identify_column(event.x) == "#0":
                return "break"
        
        self.tree.bind('<Button-1>', prevent_art_resize, add='+')
        self.tree.bind('<B1-Motion>', prevent_art_resize, add='+')
        self.tree.bind('<Double-1>', self.on_double_click)


        # --- RIGHT PANEL ---
        right_control_frame = tk.Frame(mid_frame, bg=BG_MAIN)
        right_control_frame.pack(side=tk.LEFT, padx=(10, 0), fill=tk.Y)
        right_inner = tk.Frame(right_control_frame, bg=BG_MAIN)
        right_inner.pack(expand=True)
        
        math_group = tk.Frame(right_inner, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        math_group.pack(pady=(0, 10), padx=5, fill=tk.X)
        tk.Label(math_group, text="BPM", bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 9, "bold")).pack(pady=(5,2))
        self.create_btn(math_group, "x2", lambda: self.modify_bpm(2.0), BTN_NORMAL, 4).pack(pady=2)
        self.create_btn(math_group, "÷2", lambda: self.modify_bpm(0.5), BTN_NORMAL, 4).pack(pady=2)
        self.create_btn(math_group, "↻ Sort", self.resort_project, "#a86a11", 6).pack(pady=(10, 5), padx=5)

        move_group = tk.Frame(right_inner, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1)
        move_group.pack(pady=5, padx=5, fill=tk.X)
        tk.Label(move_group, text="Move", bg=BG_MAIN, fg=FG_TEXT, font=("Helvetica", 9, "bold")).pack(pady=(5,2))
        self.create_btn(move_group, "▲▲", lambda: self.shift_track(-5), "#444444", 4).pack(pady=2)
        self.create_btn(move_group, "▲", lambda: self.shift_track(-1), BTN_NORMAL, 4).pack(pady=2)
        self.create_btn(move_group, "▼", lambda: self.shift_track(1), BTN_NORMAL, 4).pack(pady=2)
        self.create_btn(move_group, "▼▼", lambda: self.shift_track(5), "#444444", 4).pack(pady=(2, 5))

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
        left_spacer = tk.Frame(audio_frame, bg=BG_MAIN)
        left_spacer.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        
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
            bypass_var.set(not bypass_var.get())
            self.on_dsp_change()
            
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
            bypass_var.set(not bypass_var.get())
            self.on_dsp_change()
            
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
            bounce = random.uniform(0.4, 1.0)
            vol_pct = self.volume_var.get()
            normalized_vol = vol_pct / 100.0 
            level = bounce * normalized_vol
            fill_height = h * level
            if level > 0.85: color = "#ff4444" 
            elif level > 0.6: color = "#ffaa00" 
            else: color = "#44ff44" 
            self.vu_canvas.create_rectangle(0, h - fill_height, w, h, fill=color, outline="")

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

    def on_double_click(self, event):
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)

        if not row_id: return

        # Allow editing Artist (#4), Album (#5), and Song (#6)
        if col_id in ("#4", "#5", "#6"):
            col_map = {"#4": "artist", "#5": "album", "#6": "song"}
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
        for key in ["<Up>", "<Down>", "<Left>", "<Right>"]:
            self.root.bind(key, self.on_arrow_override)
            self.tree.bind(key, self.on_arrow_override)
        self.root.bind("<space>", self.on_space_override)
        self.tree.bind("<space>", self.on_space_override)

    # --- RESTORED: Transport logic ---
    def on_arrow_override(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Combobox)): return
        self.handle_arrows(event.keysym.lower())
        return "break"

    def on_space_override(self, event):
        if isinstance(event.widget, (tk.Entry, ttk.Combobox)): return
        self.toggle_play_pause()
        return "break"

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
            self.audio.play_from(os.path.join(self.project.current_folder, self.audio.current_track), target, volume=vol)
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
            new_idx = self.project.shift_track(idx, offset)
            if idx != new_idx:
                self.refresh_tree()
                item = self.tree.get_children()[new_idx]
                self.tree.selection_set(item)
                self.tree.focus(item)
                self.tree.see(item)
                self.tree.event_generate("<<TreeviewSelect>>")
                self.project.needs_save = True 

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
                self.audio.play_from(os.path.join(self.project.current_folder, self.audio.current_track), val, volume=vol)
                self.seek_offset = val
                self.btn_play_pause.config(text="⏸ Pause")
                self.sync_ui_state()


    def modify_bpm(self, multiplier):
        idx = self.get_selected_idx()
        if idx is not None:
            self.project.modify_bpm(idx, multiplier)
            self.refresh_tree()
            item = self.tree.get_children()[idx]
            self.tree.selection_set(item)
            self.project.needs_save = True

    def resort_project(self):
        if not self.project.tracks: return
        if messagebox.askyesno("Re-Sort", "Reorganize list by BPM?"):
            self.project.resort_by_bpm()
            self.refresh_tree()
            self.project.needs_save = True

    def refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        self.tree_images = {}
        
        default_art_path = os.path.join(os.getcwd(), "app_states", "default.png")
        if os.path.exists(default_art_path):
            try:
                def_img = Image.open(default_art_path).convert("RGBA")
                def_img = ImageOps.fit(def_img, (36, 36), Image.Resampling.LANCZOS)
                self.default_thumb = ImageTk.PhotoImage(def_img)
            except Exception:
                self.default_thumb = None
        else:
            def_img = Image.new("RGBA", (36, 36), (40, 40, 40, 255))
            self.default_thumb = ImageTk.PhotoImage(def_img)
            
        for i, track in enumerate(self.project.tracks):
            norm_dot = "●" if track.get('is_normalized', False) else "○"
            size_str = f"{track.get('size_mb', 0.0):.1f}"
            lufs_str = f"{track.get('lufs', -14.0):.1f}"
            
            artist = track.get('artist', '')
            album = track.get('album', '')
            song = track.get('song', track.get('original_name', track['filename']))
            if not song: song = track.get('original_name', track['filename'])
            
            thumb_img = self.default_thumb
            thumb_filename = track.get('thumb_filename', '')
            if thumb_filename:
                thumb_path = os.path.join(self.project.current_folder, thumb_filename)
                if os.path.exists(thumb_path):
                    try:
                        img = Image.open(thumb_path).convert("RGBA")
                        img = ImageOps.fit(img, (36, 36), Image.Resampling.LANCZOS)
                        photo = ImageTk.PhotoImage(img)
                        self.tree_images[i] = photo
                        thumb_img = photo
                    except Exception:
                        pass
                        
            self.tree.insert("", tk.END, text="", image=thumb_img if thumb_img else "", values=(
                f"{i + 1}",
                f"{track['bpm']}", 
                f"{track['tone']}", 
                artist,
                album,
                song,
                size_str, 
                lufs_str, 
                norm_dot
            ))

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
            
            # Look how clean this is now!
            vol = track.get('volume', 100.0) / 100.0
            self.audio.play(os.path.join(self.project.current_folder, track['filename']), volume=vol)
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
                self.audio.play_from(os.path.join(self.project.current_folder, self.audio.current_track), 0, volume=vol)
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
                self.audio.play_from(os.path.join(self.project.current_folder, self.audio.current_track), 0, volume=vol)
                self.seek_offset = 0
                self.timeline_var.set(0)
                self.sync_ui_state()

if __name__ == "__main__":
    root = tk.Tk()
    app = DJAppUI(root)
    root.mainloop()
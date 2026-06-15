import tkinter as tk
from constants import BG_MAIN, BG_LIST, HIGHLIGHT, BORDER, FG_TEXT, BTN_HOVER

def create_btn(parent, text, command, bg_color, width=None, bold=False):
    font_style = ("Helvetica", 10, "bold") if bold else ("Helvetica", 10)
    btn = tk.Label(parent, text=text, bg=bg_color, fg=FG_TEXT, font=font_style, cursor="hand2", padx=8, pady=4)
    if width: btn.config(width=width)
    
    def on_enter(e):
        if str(btn.cget("state")) != tk.DISABLED: btn.config(bg=BTN_HOVER)
    def on_leave(e):
        if str(btn.cget("state")) != tk.DISABLED: btn.config(bg=bg_color)
    def on_click(e):
        if str(btn.cget("state")) != tk.DISABLED:
            btn.config(bg="#ffffff", fg="#000000")
            btn.after(50, lambda: btn.config(bg=BTN_HOVER, fg=FG_TEXT))
            command()
            
    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    btn.bind("<Button-1>", on_click)
    return btn

def create_group_frame(parent, padx=5, pady=5):
    f = tk.Frame(parent, bg=BG_MAIN, highlightbackground=BORDER, highlightthickness=1, bd=0)
    f.pack(side=tk.LEFT, padx=padx, pady=pady)
    return f

class Knob(tk.Canvas):
    def __init__(self, parent, variable, from_, to_, command=None, size=36, pill=False, *args, **kwargs):
        bg = BG_LIST if pill else BG_MAIN
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0, *args, **kwargs)
        self.variable = variable
        self.from_ = from_
        self.to_ = to_
        self.command = command
        self.size = size
        self.disabled = False
        self.pill = pill  # rounded pill bg: dark oval on BG_LIST canvas
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.variable.trace_add('write', self.draw)
        self.bind("<Map>", self.draw)
        self.draw()

    def on_press(self, event):
        self.start_y = event.y
        self.start_val = self.variable.get()

    def on_drag(self, event):
        dy = self.start_y - event.y
        new_val = self.start_val + (dy / 100.0) * (self.to_ - self.from_)
        new_val = max(self.from_, min(self.to_, new_val))
        self.variable.set(new_val)
        if self.command: self.command()

    def set_disabled(self, disabled):
        self.disabled = disabled
        self.draw()

    def draw(self, *args):
        self.delete("all")
        val = self.variable.get()
        pct = (val - self.from_) / (self.to_ - self.from_) if self.to_ != self.from_ else 0
        outline_color = "#555555" if self.disabled else HIGHLIGHT
        m = 4
        s = self.size - m
        if self.pill:
            # Dark rounded pill bg so knob is visible on BG_LIST parent
            self.create_oval(1, 1, self.size - 1, self.size - 1, fill=BG_MAIN, outline="")
            track_color = BORDER
        else:
            track_color = BG_LIST
        self.create_arc(m, m, s, s, start=-45, extent=270, style=tk.ARC, outline=track_color, width=3)
        self.create_arc(m, m, s, s, start=225, extent=-270*pct, style=tk.ARC, outline=outline_color, width=3)

class Timeline(tk.Canvas):
    def __init__(self, parent, variable, max_val=100.0, command=None, *args, **kwargs):
        super().__init__(parent, height=12, bg=BG_LIST, highlightthickness=0, cursor="hand2", *args, **kwargs)
        self.variable = variable
        self.max_val = max_val
        self.command = command
        self.bind("<Button-1>", self.on_click)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Configure>", self.draw)
        self.variable.trace_add('write', self.draw)

    def set_max(self, val):
        self.max_val = val
        self.draw()

    def on_click(self, event):
        if self.max_val <= 0: return
        w = self.winfo_width()
        if w <= 0: return
        x = max(0, min(w, event.x))
        new_val = (x / w) * self.max_val
        self.variable.set(new_val)
        if self.command: self.command(new_val, dragging=True)

    def on_drag(self, event):
        if self.max_val <= 0: return
        w = self.winfo_width()
        if w <= 0: return
        x = max(0, min(w, event.x))
        new_val = (x / w) * self.max_val
        self.variable.set(new_val)
        if self.command: self.command(new_val, dragging=True)
        
    def on_release(self, event):
        if self.max_val <= 0: return
        w = self.winfo_width()
        if w <= 0: return
        x = max(0, min(w, event.x))
        new_val = (x / w) * self.max_val
        self.variable.set(new_val)
        if self.command: self.command(new_val, dragging=False)

    def draw(self, *args):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if self.max_val > 0 and w > 0:
            val = self.variable.get()
            pct = max(0, min(1, val / self.max_val))
            self.create_rectangle(0, 0, w * pct, h, fill=HIGHLIGHT, outline="")
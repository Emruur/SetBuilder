import os
from PIL import Image, ImageTk
from constants import VINYL_SIZE
from vinyl_renderer import generate_vinyl_image

class VinylAnimator:
    def __init__(self, root, canvas, project):
        self.root = root
        self.canvas = canvas
        self.project = project
        
        self.current_rotation_pil_frame = None 
        self.vinyl_tk_image_ref = None         
        self.current_angle = 0.0
        self.current_spin_speed = 0.0 
        self.target_spin_speed = 0.0  
        self.vinyl_draw_job = None
        self.current_art_filename = None
        
        self.generate_vinyl_processed_frame(None)
        self.vinyl_rotation_check_loop()

    def update_artwork(self, filename):
        if filename != self.current_art_filename:
            self.current_art_filename = filename
            self.process_selected_artwork(filename)

    def set_speed(self, speed):
        self.target_spin_speed = speed

    def generate_vinyl_processed_frame(self, artwork_bytes):
        self.current_rotation_pil_frame = generate_vinyl_image(artwork_bytes)
        self.draw_tk_vinyl_frame_to_canvas()

    def process_selected_artwork(self, filename):
        if not self.project.current_folder or not filename: return
        full_path = os.path.join(self.project.current_folder, filename)
        art_bytes = None
        if os.path.exists(full_path):
            try:
                art_bytes = self.project.get_track_artwork_bytes(full_path)
            except Exception:
                art_bytes = None

        # Fallback: if the audio file has no extractable cover but a saved
        # thumb PNG exists for this track, use it so the vinyl never shows
        # the default when the row already has artwork.
        if not art_bytes:
            track = next((t for t in self.project.tracks if t.get('filename') == filename), None)
            thumb = track.get('thumb_filename', '') if track else ''
            if thumb:
                thumb_path = os.path.join(self.project.current_folder, thumb)
                if os.path.exists(thumb_path):
                    try:
                        with open(thumb_path, 'rb') as fh:
                            art_bytes = fh.read()
                    except Exception:
                        art_bytes = None

        self.generate_vinyl_processed_frame(art_bytes)

    def vinyl_rotation_check_loop(self):
        if self.target_spin_speed > 0:
            self.current_spin_speed += (self.target_spin_speed - self.current_spin_speed) * 0.08
        else:
            self.current_spin_speed *= 0.94

        if self.current_spin_speed > 0.05:
            self.current_angle = (self.current_angle + self.current_spin_speed) % 360.0
            self.draw_tk_vinyl_frame_to_canvas()
        else:
            self.current_spin_speed = 0.0

        self.vinyl_draw_job = self.root.after(33, self.vinyl_rotation_check_loop)

    def draw_tk_vinyl_frame_to_canvas(self):
        if not self.current_rotation_pil_frame: return
        rotated_pil = self.current_rotation_pil_frame.rotate(self.current_angle, resample=Image.Resampling.BILINEAR)
        self.vinyl_tk_image_ref = ImageTk.PhotoImage(rotated_pil)
        center_c = (VINYL_SIZE + 20) // 2
        self.canvas.delete("record")
        self.canvas.create_image(center_c, center_c, image=self.vinyl_tk_image_ref, tags="record")
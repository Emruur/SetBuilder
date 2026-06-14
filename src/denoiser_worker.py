#!/usr/bin/env python3
"""SetBuilder Denoiser — companion process for audio denoising.

Writes newline-delimited JSON to stdout:
  {"type": "progress", "stage": "download"|"inference", "pct": 0-100}
  {"type": "download_done"}
  {"type": "done"}
  {"type": "error", "message": "..."}
"""
import sys
import os
import json
import argparse
import subprocess
import tempfile
import shutil


def emit(obj):
    print(json.dumps(obj), flush=True)


DENOISE_MODEL = 'denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt'


def _model_dir():
    if sys.platform == 'darwin':
        return os.path.join(os.path.expanduser('~/Library/Application Support'),
                            'SetBuilder', 'separator_models')
    return os.path.join(os.path.expanduser('~'), '.SetBuilder', 'separator_models')


def _ffmpeg(hint=None):
    exe = 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'
    meipass = getattr(sys, '_MEIPASS', None)
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else None

    candidates = []
    if hint:
        candidates.append(('hint', hint))
    if exe_dir:
        candidates += [
            ('MacOS', os.path.normpath(os.path.join(exe_dir, exe))),
            ('Frameworks', os.path.normpath(os.path.join(exe_dir, '..', 'Frameworks', exe))),
            ('Resources', os.path.normpath(os.path.join(exe_dir, '..', 'Resources', exe))),
        ]
    if meipass:
        candidates.append(('_MEIPASS', os.path.normpath(os.path.join(meipass, exe))))
    candidates += [
        ('homebrew', '/opt/homebrew/bin/ffmpeg'),
        ('local', '/usr/local/bin/ffmpeg'),
    ]

    try:
        debug_path = os.path.expanduser('~/Desktop/denoiser_ffmpeg_debug.txt')
        with open(debug_path, 'w') as f:
            f.write(f"hint: {hint}\nsys.executable: {sys.executable}\n_MEIPASS: {meipass}\nfrozen: {getattr(sys,'frozen',False)}\n\n")
            for label, p in candidates:
                f.write(f"  [{label}] {p} -> {os.path.isfile(p)}\n")
    except Exception:
        pass

    for label, p in candidates:
        if os.path.isfile(p):
            return p
    return exe


def run(source_path, dest_path, noise_dest_path=None, ffmpeg_hint=None):
    from audio_separator.separator import Separator
    import audio_separator.separator.separator as _sep_mod
    import audio_separator.separator.architectures.mdxc_separator as _mdxc_mod

    model_dir = _model_dir()
    os.makedirs(model_dir, exist_ok=True)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    need_download = not os.path.exists(os.path.join(model_dir, DENOISE_MODEL))

    _orig_sep_tqdm = _sep_mod.tqdm
    _orig_mdxc_tqdm = _mdxc_mod.tqdm

    class _DownloadProxy:
        def __init__(self, total=0, **_kw):
            self._total = total
            self._done = 0
        def update(self, n=1):
            self._done += n
            if self._total > 0:
                emit({'type': 'progress', 'stage': 'download',
                      'pct': self._done / self._total * 100.0})
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass

    class _InferenceProxy:
        def __init__(self, iterable=None, **_kw):
            self._items = list(iterable) if iterable is not None else []
            self._n = len(self._items)
        def __iter__(self):
            for i, item in enumerate(self._items):
                yield item
                if self._n > 0:
                    emit({'type': 'progress', 'stage': 'inference',
                          'pct': (i + 1) / self._n * 98.0})
        def __enter__(self): return self
        def __exit__(self, *_): pass

    _sep_mod.tqdm = _DownloadProxy
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
        sep.load_model(DENOISE_MODEL)
        _sep_mod.tqdm = _orig_sep_tqdm

        if need_download:
            emit({'type': 'download_done'})

        output_files = sep.separate(source_path)

        wavs = [f for f in os.listdir(tmp_dir) if f.lower().endswith('.wav')]
        if not wavs:
            raise RuntimeError(f'No output WAV found. Separator returned: {output_files}')
        dry = [f for f in wavs if '(dry)' in f.lower()]
        noise_wavs = [f for f in wavs if f not in dry]
        if not dry:
            raise RuntimeError(f'No dry stem found in: {wavs}')

        ffmpeg = _ffmpeg(ffmpeg_hint)
        cmd = [ffmpeg, '-y',
               '-i', os.path.join(tmp_dir, dry[0]), '-i', str(source_path),
               '-map', '0:a:0', '-map', '1:v:0?', '-map_metadata', '1',
               '-c:v', 'copy', '-codec:a', 'libmp3lame', '-q:a', '0',
               '-id3v2_version', '3', str(dest_path)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if noise_dest_path and noise_wavs:
            os.makedirs(os.path.dirname(noise_dest_path), exist_ok=True)
            cmd_n = [ffmpeg, '-y',
                     '-i', os.path.join(tmp_dir, noise_wavs[0]),
                     '-codec:a', 'libmp3lame', '-q:a', '2', str(noise_dest_path)]
            subprocess.run(cmd_n, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        _sep_mod.tqdm = _orig_sep_tqdm
        _mdxc_mod.tqdm = _orig_mdxc_tqdm
        shutil.rmtree(tmp_dir, ignore_errors=True)

    emit({'type': 'done'})


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='SetBuilder Denoiser companion')
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--noise-output', default=None)
    ap.add_argument('--ffmpeg', default=None)
    args = ap.parse_args()

    try:
        run(args.input, args.output, args.noise_output, ffmpeg_hint=args.ffmpeg)
    except Exception as exc:
        emit({'type': 'error', 'message': str(exc)})
        sys.exit(1)

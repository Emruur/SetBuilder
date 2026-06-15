#!/usr/bin/env python3
"""SetBuilder Denoiser — companion process. ML inference only.

Writes newline-delimited JSON to stdout:
  {"type": "progress", "stage": "download"|"inference", "pct": 0-100}
  {"type": "download_done"}
  {"type": "done", "dry_wav": "...", "noise_wav": "...", "tmp_dir": "..."}
  {"type": "error", "message": "..."}

The base app receives the WAV paths and handles WAV→MP3 conversion via pedalboard.
"""
import sys
import os
import json
import argparse
import tempfile


def emit(obj):
    print(json.dumps(obj), flush=True)


DENOISE_MODEL = 'denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt'


def _model_dir():
    if sys.platform == 'darwin':
        return os.path.join(os.path.expanduser('~/Library/Application Support'),
                            'SetBuilder', 'separator_models')
    return os.path.join(os.path.expanduser('~'), '.SetBuilder', 'separator_models')


def run(source_path):
    from audio_separator.separator import Separator
    import audio_separator.separator.separator as _sep_mod
    import audio_separator.separator.architectures.mdxc_separator as _mdxc_mod

    # audio_separator calls check_ffmpeg_installed() at Separator.__init__ and
    # uses pydub (ffmpeg) for output writing. Both are unnecessary: the base app
    # converts input to WAV before passing here so librosa reads it fine, and
    # use_soundfile=True below tells audio_separator to write via soundfile instead.
    _sep_mod.Separator.check_ffmpeg_installed = lambda self: None

    model_dir = _model_dir()
    os.makedirs(model_dir, exist_ok=True)

    need_download = not os.path.exists(os.path.join(model_dir, DENOISE_MODEL))

    _orig_sep_tqdm  = _sep_mod.tqdm
    _orig_mdxc_tqdm = _mdxc_mod.tqdm

    class _DownloadProxy:
        def __init__(self, total=0, **_kw):
            self._total = total
            self._done  = 0
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

    _sep_mod.tqdm  = _DownloadProxy
    _mdxc_mod.tqdm = _InferenceProxy

    tmp_dir = tempfile.mkdtemp()
    try:
        sep = Separator(
            model_file_dir=model_dir,
            output_dir=tmp_dir,
            output_format='WAV',
            log_level=30,
            use_autocast=True,
            use_soundfile=True,
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

        # Emit paths — base app handles WAV→MP3 conversion via pedalboard
        emit({
            'type':      'done',
            'dry_wav':   os.path.join(tmp_dir, dry[0]),
            'noise_wav': os.path.join(tmp_dir, noise_wavs[0]) if noise_wavs else None,
            'tmp_dir':   tmp_dir,
        })
    except Exception:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    finally:
        _sep_mod.tqdm  = _orig_sep_tqdm
        _mdxc_mod.tqdm = _orig_mdxc_tqdm


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='SetBuilder Denoiser companion')
    ap.add_argument('--input', required=True)
    args = ap.parse_args()

    try:
        run(args.input)
    except Exception as exc:
        emit({'type': 'error', 'message': str(exc)})
        sys.exit(1)

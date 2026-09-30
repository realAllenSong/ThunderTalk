"""Drop-in replacements for the two librosa calls used on the IndexTTS path."""


def _mel_filters(sr, n_fft, n_mels, fmin=0.0, fmax=None):
    """librosa.filters.mel(sr, n_fft, n_mels, fmin, fmax) with its defaults
    (Slaney scale, Slaney area norm), via torchaudio. ThunderTalk: avoids
    librosa, which needs scipy/numba that the app does not bundle."""
    import torchaudio
    fb = torchaudio.functional.melscale_fbanks(
        n_freqs=n_fft // 2 + 1, f_min=float(fmin), f_max=float(fmax if fmax is not None else sr / 2.0),
        n_mels=n_mels, sample_rate=int(sr), norm="slaney", mel_scale="slaney")
    return fb.T.numpy()


def _load_audio(path, sr=None, mono=True):
    """librosa.load(path, sr=None, mono=True) without librosa: native rate, float32."""
    import soundfile as sf
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1) if mono else audio.T
    if sr is not None and sr != rate:
        import torch, torchaudio
        audio = torchaudio.functional.resample(torch.from_numpy(audio), rate, sr).numpy()
        rate = sr
    return audio, rate

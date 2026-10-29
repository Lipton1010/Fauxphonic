"""Phase 0 research only: official wav2wav algorithm vs bounded I/O.

Does not install/enable a model in the desktop product. Test source is synthesized,
not human speech. The numerical reference loop follows upstream test_onnx.py at
f85223bd546b27f39dc0744e0310dcd246f750a4 (MIT, AHN Sung Hwan 2025).
"""
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import onnxruntime as ort
import psutil
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "m": "c7da800810b583f4734d757c6e14d235f3eec81476121b595743e5866b66efa2",
    "s": "f04ece2beed330da367264c54cedded62f65a117fbde5c005d3a88fc796d0ba3",
}


def session(path):
    settings = ort.SessionOptions()
    settings.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    settings.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    settings.intra_op_num_threads = 1
    settings.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), settings, providers=["CPUExecutionProvider"])


def caches(model):
    return {i.name: np.zeros(i.shape, np.float32) for i in model.get_inputs() if i.name.startswith("cache_in_")}


def step(model, state, chunk):
    state["wav_in"] = chunk.reshape(1, -1)
    out = model.run(None, state)
    for j, value in enumerate(out[1:]):
        state[f"cache_in_{j}"] = value
    return out[0][0]


def reference(model, signal, hop):
    # Exact initialization, right padding, cache ordering, and crop from upstream.
    state = caches(model)
    padded = np.pad(np.clip(signal, -1, 1), (0, 1024))
    parts = [step(model, state, padded[i:i+hop]) for i in range(0, len(signal)+1024-hop, hop)]
    result = np.concatenate(parts)[1024-hop:1024-hop+len(signal)]
    return np.clip(result, -1, 1)


def stream_file(model, source_path, output_path, hop, block_size):
    state = caches(model)
    pending = np.empty(0, np.float32)
    produced = 0
    delay = 1024-hop
    peak_rss = 0
    proc = psutil.Process()
    with sf.SoundFile(source_path) as source, sf.SoundFile(output_path, "w", samplerate=48000,
                                                         channels=1, subtype="FLOAT") as output:
        length = len(source)
        def consume(chunk):
            nonlocal produced
            result = step(model, state, np.clip(chunk, -1, 1))
            if not np.isfinite(result).all() or result.shape != (hop,):
                raise ValueError("Invalid model output")
            left = max(0, delay-produced)
            right = min(hop, length+delay-produced)
            if right > left:
                output.write(np.clip(result[left:right], -1, 1))
            produced += hop
        for block in source.blocks(blocksize=block_size, dtype="float32"):
            pending = np.concatenate((pending, block))
            count = len(pending)//hop
            for i in range(count):
                consume(pending[i*hop:(i+1)*hop])
            pending = pending[count*hop:]
            peak_rss = max(peak_rss, proc.memory_info().rss)
        pending = np.pad(pending, (0, 1024))
        for i in range(0, len(pending)-hop+1, hop):
            if produced >= length+delay:
                break
            consume(pending[i:i+hop])
        if output.tell() != length:
            raise AssertionError("Incorrect output length")
    return peak_rss


def main():
    output_dir = ROOT / "evidence" / "local" / "model-probe"
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    t = np.arange(48000*12+137)/48000
    signal = (0.1*np.sin(2*np.pi*173*t)*(0.5+0.5*np.sin(2*np.pi*2*t)) + rng.normal(0, 0.006, len(t))).astype(np.float32)
    signal[0], signal[-1] = 0.5, -0.5
    source = output_dir / "synthetic-noisy.wav"
    sf.write(source, signal, 48000, subtype="FLOAT")
    results = []
    for size, expected in EXPECTED.items():
        path = ROOT / ".cache" / "upstream" / f"fastenhancer_{size}.onnx"
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert sha == expected
        started = time.perf_counter()
        model = session(path)
        load_seconds = time.perf_counter()-started
        hop = model.get_inputs()[0].shape[-1]
        ref_start = time.perf_counter()
        baseline = reference(model, signal, hop)
        reference_seconds = time.perf_counter()-ref_start
        runs = []
        for block_size in (4093, 65536):
            target = output_dir / f"{size}-block-{block_size}.wav"
            start = time.perf_counter()
            rss = stream_file(model, source, target, hop, block_size)
            seconds = time.perf_counter()-start
            actual, _ = sf.read(target, dtype="float32")
            difference = float(np.max(np.abs(actual-baseline)))
            assert difference < 1e-6
            runs.append({"block_size": block_size, "wall_seconds": seconds,
                         "rtf_including_load": (seconds+load_seconds)/(len(signal)/48000),
                         "peak_process_rss_bytes": rss, "max_error_vs_reference": difference,
                         "frames": len(actual), "output": str(target)})
        results.append({"model": size, "sha256": sha, "bytes": path.stat().st_size,
                        "hop": hop, "n_fft": 1024, "delay_samples": 1024-hop,
                        "inputs": [{"name": i.name, "shape": i.shape, "type": i.type} for i in model.get_inputs()],
                        "providers": model.get_providers(), "load_seconds": load_seconds,
                        "reference_seconds": reference_seconds, "runs": runs})
    report = {"runtime": ort.__version__, "results": results,
              "license": "Repository MIT; release checkpoint has no separate rights statement. Not redistributed or enabled.",
              "scope": "Synthetic signal engineering proof; no speech, listening, CUDA, spec2spec parity, or long-form model claim."}
    (ROOT / "evidence" / "model-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

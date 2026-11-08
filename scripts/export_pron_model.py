"""Build the pronunciation model the app uses: models/pron/l2arctic/model_fp16.onnx.

The phone recogniser is wav2vec2-large-xlsr-53 fine-tuned on L2-ARCTIC (Apache 2.0):
https://huggingface.co/mrrubino/wav2vec2-large-xlsr-53-l2-arctic-phoneme
It is published as PyTorch weights, so this script downloads them once, exports them to ONNX (opset 17,
dynamic length) and converts the weights to float16 while keeping float32 inputs and outputs, which is what
app/tutor/assessment.py loads on the GPU with ONNX Runtime.

The export runs on the CPU in a throwaway environment, so PyTorch never enters the app's own environment:

    uv run --no-project --python 3.12 --with "torch==2.8.0" --with "transformers>=4.44,<5" ^
        --with "onnx>=1.17" --with "onnxconverter-common>=1.14" --with onnxruntime python scripts\\export_pron_model.py

    optional: --source <folder with the PyTorch model>   (skip the download)
              --out <folder>                              (default: models\\pron\\l2arctic)
              --keep-fp32                                 (also keep model.onnx, ~1.2 GB; only for experiments)

scripts\\setup.ps1 runs this for you.
"""

import argparse
import json
import shutil
import sys
import tempfile
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "mrrubino/wav2vec2-large-xlsr-53-l2-arctic-phoneme"
FILES = ("config.json", "vocab.json", "preprocessor_config.json", "README.md")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, help="local folder with the PyTorch model (default: download it)")
    ap.add_argument("--out", type=Path, default=ROOT / "models" / "pron" / "l2arctic")
    ap.add_argument("--keep-fp32", action="store_true")
    args = ap.parse_args()

    import numpy as np
    import onnx
    import torch
    from onnxconverter_common import float16
    from transformers import Wav2Vec2ForCTC

    if args.source:
        source = args.source
    else:
        from huggingface_hub import snapshot_download
        print(f"Downloading {REPO} ...", flush=True)
        source = Path(snapshot_download(REPO, allow_patterns=["*.json", "*.bin", "*.safetensors", "README.md"]))

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    print("Loading the PyTorch model ...", flush=True)
    model = Wav2Vec2ForCTC.from_pretrained(str(source)).eval()

    with tempfile.TemporaryDirectory(dir=out) as tmp:
        fp32 = Path(tmp) / "model.onnx"
        print("Exporting to ONNX (a few minutes on the CPU) ...", flush=True)
        dummy = torch.randn(1, 16000 * 3)
        with torch.no_grad():
            reference = model(dummy).logits.numpy()
            torch.onnx.export(model, (dummy,), str(fp32), input_names=["input_values"], output_names=["logits"],
                              dynamic_axes={"input_values": {0: "batch", 1: "samples"},
                                            "logits": {0: "batch", 1: "frames"}},
                              opset_version=17, do_constant_folding=True, dynamo=False)

        print("Converting the weights to float16 ...", flush=True)
        with warnings.catch_warnings():   # one line per tiny weight clipped to float16's range: harmless noise
            warnings.simplefilter("ignore", UserWarning)
            half = float16.convert_float_to_float16(onnx.load(str(fp32)), keep_io_types=True)
        onnx.save(half, str(out / "model_fp16.onnx"))
        if args.keep_fp32:
            shutil.move(str(fp32), out / "model.onnx")

    for name in FILES:
        if (source / name).exists():
            shutil.copyfile(source / name, out / name)
    blank = json.loads((out / "config.json").read_text(encoding="utf-8"))["pad_token_id"]

    # Sanity check on the CPU: the float16 model must pick the same phones as PyTorch
    try:
        import onnxruntime as ort
        session = ort.InferenceSession(str(out / "model_fp16.onnx"), providers=["CPUExecutionProvider"])
        logits = session.run(None, {"input_values": dummy.numpy()})[0]
        agree = float((logits.argmax(-1) == reference.argmax(-1)).mean())
        print(f"Check: {agree:.1%} of frames agree with PyTorch (blank id {blank})")
        if agree < 0.95:
            print("The exported model does not match PyTorch closely enough.", file=sys.stderr)
            return 1
    except ImportError:
        print("(onnxruntime is not in this environment: the comparison with PyTorch was skipped)")

    size = (out / "model_fp16.onnx").stat().st_size / 2**20
    print(f"Done: {out / 'model_fp16.onnx'} ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

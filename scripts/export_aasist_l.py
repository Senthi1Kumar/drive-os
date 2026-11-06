"""One-off export of the official clovaai/aasist AASIST-L checkpoint (MIT) to ONNX.

    git clone --depth 1 https://github.com/clovaai/aasist /tmp/aasist
    python scripts/export_aasist_l.py /tmp/aasist ~/.local/share/drive-os/models/aasist-l/aasist-l.onnx

Needs torch + onnx (use a throwaway venv; DriveOS itself only needs onnxruntime). Input: float32 [1, 64600]
raw 16 kHz audio. Output: [1, 2] logits, index 1 = bona fide.
"""

import json
import sys
from pathlib import Path

import numpy as np
import torch


def main(repo: str, out: str) -> None:
    sys.path.insert(0, repo)
    from models.AASIST import Model  # type: ignore

    cfg = json.loads((Path(repo) / "config" / "AASIST-L.conf").read_text())
    model = Model(cfg["model_config"])
    model.load_state_dict(torch.load(Path(repo) / "models" / "weights" / "AASIST-L.pth", map_location="cpu"))
    model.eval()

    class Wrap(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x):
            return self.m(x)[1]

    w = Wrap(model).eval()
    x = torch.randn(1, 64600) * 0.1
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(w, x, out, input_names=["audio"], output_names=["logits"], opset_version=17,
                      dynamo=False)
    import onnxruntime as ort

    s = ort.InferenceSession(out, providers=["CPUExecutionProvider"])
    a = s.run(None, {"audio": x.numpy()})[0]
    b = w(x).detach().numpy()
    print("max abs diff", float(np.abs(a - b).max()), "logits", a)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

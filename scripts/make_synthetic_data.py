"""Write a synthetic IMD-format sample (fictional state 'Synthetica') under sample_data/.

    python scripts/make_synthetic_data.py [output_dir]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.synthetic import make_sample  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "sample_data"
paths = make_sample(out)
print("Synthetic sample written:", *[f"{k}: {v}" for k, v in paths.items()], sep="\n  ")

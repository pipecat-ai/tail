import sys
from pathlib import Path

# Make the synthetic session helper importable from every test.
sys.path.insert(0, str(Path(__file__).parent))

"""Make the repo root importable so tests can `import server.x` / `import client.x`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

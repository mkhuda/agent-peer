"""atomic_write_json must never let a concurrent reader see a truncated/
corrupt file - the real bug behind 'list sees it, send says Active sessions:
None' (codex-8763, 2026-09-23): listener.py rewrote each status update with
open(path, 'w') + json.dump(), which truncates before writing."""

import glob
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer.protocol import atomic_write_json


class AtomicWriteRaceTest(unittest.TestCase):
    def test_concurrent_readers_never_see_a_corrupt_file(self, iterations=300):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.json")
            atomic_write_json(path, {"status": "idle", "n": 0})

            errors = []
            stop = threading.Event()

            def writer():
                for i in range(iterations):
                    atomic_write_json(path, {"status": "busy" if i % 2 else "idle", "n": i})
                stop.set()

            def reader():
                while not stop.is_set():
                    try:
                        with open(path, encoding="utf-8") as f:
                            json.load(f)
                    except Exception as e:
                        errors.append(f"{type(e).__name__}: {e}")

            t_writer = threading.Thread(target=writer)
            readers = [threading.Thread(target=reader) for _ in range(3)]
            t_writer.start()
            for r in readers:
                r.start()
            t_writer.join(timeout=30)
            for r in readers:
                r.join(timeout=5)

            self.assertEqual(errors, [], f"reader(s) saw a corrupt/partial file {len(errors)} time(s)")
            leftover_tmp = glob.glob(path + ".tmp.*")
            self.assertEqual(leftover_tmp, [], f"leftover temp file(s) not cleaned up: {leftover_tmp}")


if __name__ == "__main__":
    unittest.main()

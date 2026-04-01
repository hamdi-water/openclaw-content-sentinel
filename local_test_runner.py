import os
import subprocess
import sys


def run():
    print("Running tests/test_rag.py...")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(os.getcwd(), "src")
    cp = subprocess.run([sys.executable, "-m", "pytest", "-v", "tests/test_rag.py"],
                        capture_output=True, text=True, encoding="utf-8", env=env)
    print("STDOUT:")
    print(cp.stdout)
    print("STDERR:")
    print(cp.stderr)

if __name__ == "__main__":
    run()

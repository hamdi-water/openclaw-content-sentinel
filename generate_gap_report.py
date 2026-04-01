import sys
import os
from pathlib import Path

# Add src to sys.path
src_path = Path(r"c:\Users\water\OneDrive\Bureau\OpenClaw Content Sentinel\src")
sys.path.append(str(src_path))

from openclaw_content_sentinel.config import AppConfig
from openclaw_content_sentinel.gap_tracker import write_v61_gap_tracker

def main():
    config = AppConfig.from_env(Path(r"c:\Users\water\OneDrive\Bureau\OpenClaw Content Sentinel"))
    results = write_v61_gap_tracker(config)
    print(f"Overall Completion: {results['overall_completion_percent']}%")
    print(f"Report path: {results['markdown_path']}")

if __name__ == "__main__":
    main()

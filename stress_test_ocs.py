import asyncio
import os
import time


async def run_ocs_sim(run_index: int):
    print(f"Starting OCS Simulation [{run_index}]...")
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    python_exe = r"C:\Users\water\AppData\Local\Programs\Python\Python314\python.exe"
    cmd_str = (
        "import sys; sys.path.insert(0, 'src'); "
        "from openclaw_content_sentinel import cli; "
        f"cli.main(['daily-run', '--prompt', 'Stress Test Run {run_index}', "
        "'--competitor-url', 'https://techcrunch.com/category/artificial-intelligence/', "
        "'--simulation'])"
    )
    proc = await asyncio.create_subprocess_exec(
        python_exe, "-c", cmd_str,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env
    )



    stdout, stderr = await proc.communicate()
    print(f"OCS Simulation [{run_index}] finished with code {proc.returncode}")
    if proc.returncode != 0:
        print(f"ERROR [{run_index}]: {stderr.decode()}")
    return proc.returncode

async def stress_test(concurrency: int = 5):

    print(f"Launching {concurrency} concurrent OCS runs...")
    start = time.time()
    tasks = [run_ocs_sim(i) for i in range(concurrency)]
    results = await asyncio.gather(*tasks)
    duration = time.time() - start

    successes = results.count(0)
    print("\n--- Stress Test Result ---")
    print(f"Success: {successes}/{concurrency}")
    print(f"Duration: {duration:.2f}s")

    if successes == concurrency:
        print("PLATINUM STATUS: Concurrent integrity verified.")
    else:
        print("FAILURE: Concurrent state conflict detected.")

if __name__ == "__main__":
    asyncio.run(stress_test(5))

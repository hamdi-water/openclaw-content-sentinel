import warnings


def pytest_configure(config):
    """Early configuration hook to suppress upstream noise before collection.
    """
    warnings.filterwarnings("ignore", category=UserWarning, message="Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater")
    warnings.filterwarnings("ignore", category=DeprecationWarning, message="'asyncio.iscoroutinefunction' is deprecated")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="starlette.*")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="pydantic.*")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="fastapi.*")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="anyio.*")
    warnings.filterwarnings("ignore", category=ResourceWarning)

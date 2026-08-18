import uvicorn

from domainsmanager_api.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "domainsmanager_api.main:app",
        host=settings.server_host,
        port=settings.server_port,
    )

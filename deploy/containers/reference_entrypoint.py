"""Environment-only source image launcher. Database must be provisioned separately."""
import sys
from airport_reference.config import Settings
from airport_reference.main import create_app
import uvicorn

def main():
    try:
        settings=Settings(_env_file=None)
        if settings.env!='production' or not settings.database_path.is_file():
            raise ValueError('Production source requires a provisioned database')
        app=create_app(settings)
    except Exception:
        print('Reference startup configuration invalid; values redacted.',file=sys.stderr)
        raise SystemExit(2) from None
    uvicorn.run(app,host=settings.host,port=settings.port,access_log=False,proxy_headers=False)

if __name__=='__main__':main()

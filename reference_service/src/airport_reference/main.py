"""Standalone read-only reference API; no application backend imports."""

import argparse
from contextlib import asynccontextmanager
import ipaddress
from pathlib import Path
from typing import Annotated
from fastapi import Depends,FastAPI,Path as PathParameter,Query,Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
import uvicorn

from airport_reference.boundary import Boundary,error_response
from airport_reference.config import Settings,ConfigurationError,load_settings
from airport_reference.security import SourceError,authenticate,Caller
from airport_reference.service import SourceService
from airport_reference.store import Store
from airport_reference.models import BookingView,TransactionView,PolicyView,TransactionPage,RelationPage

Reference=Annotated[str,PathParameter(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
PageSize=Annotated[int,Query(ge=1,le=100)]
Cursor=Annotated[str|None,Query(max_length=2048)]


def create_app(settings:Settings,*,store=None):
    store=store or Store(settings.database_path)
    @asynccontextmanager
    async def lifespan(app):
        with store.read() as c:c.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()
        yield
    app=FastAPI(title='Airport Reference Source',version='0.1.0',docs_url='/docs' if settings.env!='production' else None,redoc_url=None,openapi_url='/openapi.json' if settings.env!='production' else None,lifespan=lifespan)
    service=SourceService(store,settings.context_secret.get_secret_value())
    @app.exception_handler(SourceError)
    async def source_error(request,exc):return error_response(exc.status,request.state.request_id)
    @app.exception_handler(HTTPException)
    async def http_error(request,exc):return error_response(exc.status_code,request.state.request_id,{'Allow':exc.headers['Allow']} if exc.headers and 'Allow' in exc.headers else None)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):return error_response(422,request.state.request_id)
    def caller(request:Request):
        if request.url.scheme!='https':
            try:local=ipaddress.ip_address(request.client.host).is_loopback and request.url.hostname in {'localhost','127.0.0.1','::1'}
            except (ValueError,AttributeError):local=False
            if settings.env=='production' or not local:raise SourceError(403,'ACTION_FORBIDDEN')
        if len(request.headers.getlist('host'))!=1:raise SourceError(401,'AUTH_REQUIRED')
        identity=authenticate(request.headers,request.method,request.scope.get('raw_path',request.url.path.encode()),request.scope.get('query_string',b''),settings.service_token.get_secret_value(),settings.context_secret.get_secret_value())
        if any(request.headers.getlist(name) for name in ('x-tenant-id','x-subject-id','x-principal-id','x-source-subject')):
            raise SourceError(400,'BAD_REQUEST')
        return identity
    def query(request,allowed=()):
        keys=[k for k,v in request.query_params.multi_items()]
        if len(keys)!=len(set(keys)) or set(keys)-set(allowed):raise SourceError(400,'BAD_REQUEST')
    @app.get('/health/live')
    def health():return {'status':'ok','scope':'process'}
    @app.get('/source/v1/bookings/{reference}',response_model=BookingView)
    def booking(reference:Reference,request:Request,identity:Caller=Depends(caller)):
        query(request);return service.booking(identity,reference)
    @app.get('/source/v1/bookings/{reference}/transactions',response_model=TransactionPage)
    def transactions(reference:Reference,request:Request,limit:PageSize=20,cursor:Cursor=None,identity:Caller=Depends(caller)):
        query(request,{'limit','cursor'});return service.booking_transactions(identity,reference,limit,cursor)
    @app.get('/source/v1/transactions/{reference}',response_model=TransactionView,response_model_exclude_none=True)
    def transaction(reference:Reference,request:Request,identity:Caller=Depends(caller)):
        query(request);return service.transaction(identity,reference)
    @app.get('/source/v1/policies/{reference}',response_model=PolicyView)
    def policy(reference:Reference,request:Request,identity:Caller=Depends(caller)):
        query(request);return service.policy(identity,reference)
    @app.get('/source/v1/subjects/me/relations',response_model=RelationPage)
    def relations(request:Request,limit:PageSize=20,cursor:Cursor=None,identity:Caller=Depends(caller)):
        query(request,{'limit','cursor'});return service.relations(identity,limit,cursor)
    app.add_middleware(Boundary)
    return app


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',type=Path,default=Path('reference_service/.env'))
    parser.add_argument('--check-config',action='store_true')
    args=parser.parse_args()
    try:settings=load_settings(args.env_file)
    except ConfigurationError as exc:
        print(str(exc));raise SystemExit(2) from None
    if args.check_config:print('PASS: reference configuration valid.');return
    uvicorn.run(create_app(settings),host=settings.host,port=settings.port,access_log=False,proxy_headers=False)


if __name__=='__main__':main()
